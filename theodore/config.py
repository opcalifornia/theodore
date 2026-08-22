"""Central configuration: API keys, paths, model routing, cost guardrails."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("THEODORE_DATA_DIR", PROJECT_ROOT / "data"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")

# Per-1M-token USD pricing (input/output). Used only to print cost estimates
# to the user -- nothing here is billed against. Update as pricing changes.
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-opus-5": {"input": 15.00, "output": 75.00},
}

# Swappable per --model-tier. Each analyze/ pass looks up its model here by
# name rather than hardcoding one, so the whole routing table moves together.
MODEL_TIERS = {
    "economy": {
        "segmenter": "claude-haiku-4-5-20251001",
        "selects": "claude-haiku-4-5-20251001",
        "themes": "claude-haiku-4-5-20251001",
        "narrative": "claude-haiku-4-5-20251001",
    },
    "standard": {
        "segmenter": "claude-haiku-4-5-20251001",
        "selects": "claude-sonnet-5",
        "themes": "claude-sonnet-5",
        "narrative": "claude-sonnet-5",
    },
    "premium": {
        "segmenter": "claude-sonnet-5",
        "selects": "claude-opus-5",
        "themes": "claude-opus-5",
        "narrative": "claude-opus-5",
    },
}

DEFAULT_MODEL_TIER = "standard"

# Fixed to Haiku regardless of --model-tier: these are similarity/matching
# tasks, not editorial judgment, and running them at a higher tier buys
# nothing. (v2.0 Part 1: registry immutable-id matching and question-guide
# canonical matching.)
ID_MATCHING_MODEL = "claude-haiku-4-5-20251001"
QUESTION_GUIDE_MODEL = "claude-haiku-4-5-20251001"
# v2.0 Part 2: natural-language command parsing is fixed to Sonnet per spec
# ("Command parsing runs on Sonnet") -- it's translating intent into a
# structured op, not doing the high-stakes editorial judgment premium tier
# buys for selects/themes.
COMMAND_PARSER_MODEL = "claude-sonnet-5"

# Hard stop: refuse to spend more than this in a single analysis run without
# --allow-cost-over. A footgun guard, not a precise budget -- see
# estimate_preflight_cost() for how the pre-flight number is derived, and
# analyze.claude_client.CostTracker for the real per-call accounting.
MAX_ESTIMATED_COST_USD = float(os.environ.get("THEODORE_MAX_COST_USD", "20.0"))

CHUNK_UTTERANCE_OVERLAP = 10
MAX_UTTERANCES_PER_CHUNK = 220

# Trim defaults for assembly/trim.py (v1.5). Filler/backchannel sets are
# matched against lowercased, punctuation-stripped words (or word pairs, for
# the two-word phrases), so entries here are written in their bare form.
DEFAULT_SILENCE_THRESHOLD_SECONDS = 1.2
DEFAULT_HANDLE_FRAMES = 12
LEADING_TRAILING_FILLERS = {"um", "uh", "you know", "like", "so", "well"}
BACKCHANNEL_WORDS = {"mm-hmm", "mhm", "uh-huh", "right", "yeah", "okay", "ok", "mm"}


def model_for_pass(pass_name: str, tier: str = DEFAULT_MODEL_TIER) -> str:
    try:
        return MODEL_TIERS[tier][pass_name]
    except KeyError as exc:
        raise ValueError(f"Unknown model tier {tier!r} or pass {pass_name!r}") from exc


def project_dir(project: str) -> Path:
    d = DATA_ROOT / project
    d.mkdir(parents=True, exist_ok=True)
    return d


def require_anthropic_key() -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file (see .env.example)."
        )
    return ANTHROPIC_API_KEY


def require_deepgram_key() -> str:
    if not DEEPGRAM_API_KEY:
        raise RuntimeError(
            "DEEPGRAM_API_KEY is not set. Add it to your .env file (see .env.example)."
        )
    return DEEPGRAM_API_KEY


def estimate_preflight_cost(transcript_chars: int, tier: str = DEFAULT_MODEL_TIER) -> float:
    """Rough pre-flight cost estimate, printed and checked against
    MAX_ESTIMATED_COST_USD before any Claude call is made. Assumes ~4
    chars/token, that segmenter and selects each see the transcript ~1.2x
    (chunk overlap) and themes ~2.2x (vocabulary pass + tagging pass), and
    that output runs about 15% of input size. Deliberately conservative --
    exact spend is tracked per-call by analyze.claude_client.CostTracker as
    the run actually happens.
    """
    tokens = transcript_chars / 4
    pass_multipliers = {"segmenter": 1.2, "selects": 1.2, "themes": 2.2}
    total = 0.0
    for pass_name, multiplier in pass_multipliers.items():
        model = model_for_pass(pass_name, tier)
        pricing = MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
        pass_tokens = tokens * multiplier
        total += (pass_tokens / 1_000_000) * pricing["input"]
        total += (pass_tokens * 0.15 / 1_000_000) * pricing["output"]
    return total
