"""Shared Claude call wrapper for every analyze/ pass: strips markdown
fences from JSON responses, retries once with a repair prompt on parse
failure, retries transient API errors with backoff, and tracks estimated
cost per call.

Nothing in analyze/ may import from theodore.resolve -- see
theodore/analyze/__init__.py.
"""
from __future__ import annotations

import json
import logging
import re
import time

import anthropic

from theodore import config

logger = logging.getLogger("theodore.analyze")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

MAX_RETRIES = 4


class ClaudeCallError(RuntimeError):
    pass


class CostTracker:
    """Accumulates estimated USD cost across every Claude call in a run."""

    def __init__(self):
        self.calls: list[dict] = []

    def record(self, pass_name: str, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = config.MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
        cost = (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]
        self.calls.append({
            "pass": pass_name, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": cost,
        })
        logger.info("[%s] %s: %d in / %d out tokens, ~$%.4f", pass_name, model, input_tokens, output_tokens, cost)
        return cost

    @property
    def total_cost_usd(self) -> float:
        return sum(c["cost_usd"] for c in self.calls)

    def summary(self) -> str:
        lines = [f"  {c['pass']:<16} {c['model']:<30} ${c['cost_usd']:.4f}" for c in self.calls]
        lines.append(f"  {'TOTAL':<16} {'':<30} ${self.total_cost_usd:.4f}")
        return "\n".join(lines)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


def _extract_text(message) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def call_json(
    client: "anthropic.Anthropic",
    *,
    pass_name: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    cost_tracker: CostTracker,
) -> dict:
    """Call Claude expecting a single JSON object back.

    Strips markdown fences, retries once with a repair prompt on parse
    failure, and retries on transient API errors with backoff. Never
    silently swallows a failure that would waste money already spent on
    transcription -- raises ClaudeCallError (with the offending text
    attached) if the repair attempt also fails.
    """
    resp = None
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            break
        except anthropic.APIError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "[%s] Claude API error (attempt %d/%d): %s -- retrying in %ds",
                    pass_name, attempt + 1, MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)

    if resp is None:
        raise ClaudeCallError(f"[{pass_name}] Claude API failed after {MAX_RETRIES} attempts: {last_exc}") from last_exc

    cost_tracker.record(pass_name, model, resp.usage.input_tokens, resp.usage.output_tokens)
    raw_text = _extract_text(resp)

    try:
        return json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        logger.warning("[%s] response wasn't valid JSON, retrying once with a repair prompt", pass_name)

    repair_resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        system=system,
        messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": raw_text},
            {"role": "user", "content": (
                "That was not valid JSON. Respond again with ONLY the corrected "
                "JSON object -- no prose, no markdown code fences."
            )},
        ],
    )
    cost_tracker.record(f"{pass_name}-repair", model, repair_resp.usage.input_tokens, repair_resp.usage.output_tokens)
    repair_text = _extract_text(repair_resp)
    try:
        return json.loads(_strip_fences(repair_text))
    except json.JSONDecodeError as exc:
        raise ClaudeCallError(
            f"[{pass_name}] Claude's response was not valid JSON even after a repair "
            f"retry. Raw response follows:\n{repair_text[:2000]}"
        ) from exc
