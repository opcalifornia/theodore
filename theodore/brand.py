"""A client's brand, learned once and used on every cut.

Two ways in, used together: the editor tells me a short brief (audience, tone,
must-say lines, things to avoid), and/or drops material in a folder (brand
guide, website copy, scripts, past videos) that I read and distil. What is
still missing comes back as questions. Saved per client under the data dir;
the text form is folded into the brief of story cuts so the edit serves the
brand, not just the transcript.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from theodore import config
from theodore.analyze.claude_client import CostTracker, call_json

FIELDS = ("audience", "tone", "goal", "must_say", "avoid", "visual_style", "calls_to_action", "notes")
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".json", ".html", ".htm", ".csv", ".srt", ".vtt"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mxf", ".webm"}
MAX_CHARS = 60_000

SYSTEM = """You are a senior video editor learning a client's brand before cutting for them. \
From the material, write down what an editor needs: who the audience is, the tone and \
voice, what the content is meant to achieve, lines or messages that must land, things to \
avoid, the visual style, and any calls to action. Quote or closely paraphrase the material; \
never invent a fact about the brand. Anything an editor would need but the material does \
not say goes in `missing` as a short question to ask the client."""

INSTRUCTIONS = """Return ONLY JSON: {"audience": "", "tone": "", "goal": "", "must_say": [], \
"avoid": [], "visual_style": "", "calls_to_action": [], "notes": "", "missing": ["<question>"]}"""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_").lower() or "client"


def path_for(client: str) -> Path:
    return Path(config.DATA_ROOT) / "brands" / f"{_slug(client)}.json"


def load(client: str) -> Optional[dict]:
    try:
        return json.loads(path_for(client).read_text())
    except (OSError, ValueError):
        return None


def update(client: str, **fields) -> dict:
    """Merge the given fields into the client's profile (lists are replaced, not appended)."""
    bad = set(fields) - set(FIELDS)
    if bad:
        raise ValueError(f"Unknown brand fields {sorted(bad)}; use {list(FIELDS)}.")
    profile = load(client) or {"client": client}
    profile.update({k: v for k, v in fields.items() if v not in (None, "", [])})
    f = path_for(client)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(profile, indent=2))
    return profile


def prompt_text(client: Optional[str]) -> str:
    """The profile as editing rules for a prompt; empty when there is none."""
    profile = load(client) if client else None
    if not profile:
        return ""
    lines = [f"Brand: {profile.get('client', client)}. Serve it in every choice:"]
    labels = {"audience": "Audience", "tone": "Tone", "goal": "Goal", "visual_style": "Visual style", "notes": "Notes"}
    for k, label in labels.items():
        if profile.get(k):
            lines.append(f"- {label}: {profile[k]}")
    for k, label in (("must_say", "Must land"), ("avoid", "Avoid"), ("calls_to_action", "Call to action")):
        if profile.get(k):
            items = profile[k] if isinstance(profile[k], list) else [profile[k]]
            lines.append(f"- {label}: " + "; ".join(str(i) for i in items))
    return "\n".join(lines)


def read_material(paths: list) -> tuple[str, list[str], list[str]]:
    """(text, videos to study with study_reference, files skipped) from files and folders."""
    files = []
    for p in map(Path, paths):
        files += sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
    text, videos, skipped = [], [], []
    for f in files:
        suf = f.suffix.lower()
        if suf in VIDEO_SUFFIXES:
            videos.append(str(f))
        elif suf in TEXT_SUFFIXES:
            try:
                body = f.read_text(errors="replace")
            except OSError:
                skipped.append(str(f))
                continue
            if suf in {".html", ".htm"}:
                body = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", " ", body))
            text.append(f"=== {f.name} ===\n{re.sub(r'[ \t]+', ' ', body).strip()}")
        else:
            skipped.append(str(f))
    return "\n\n".join(text)[:MAX_CHARS], videos, skipped


def learn(client: str, paths: list, *, brief: str = "", claude=None) -> dict:
    """Distil the material (plus anything said in `brief`) into the client's saved profile."""
    from theodore.edit import EDITOR_MODEL
    from theodore.llm import make_client
    text, videos, skipped = read_material(paths or [])
    if not text and not brief:
        raise ValueError("Nothing to learn from: give a folder with text material, or say the brief.")
    got = call_json(
        claude or make_client(), pass_name="brand-learn", model=EDITOR_MODEL, system=SYSTEM,
        user=(f"Client: {client}\nWhat the editor told me: {brief or '(nothing)'}\n\nMaterial:\n{text or '(none)'}\n\n"
              + INSTRUCTIONS), max_tokens=3000, cost_tracker=CostTracker())
    missing = [str(q) for q in got.pop("missing", []) or []]
    profile = update(client, **{k: got.get(k) for k in FIELDS})
    return {"client": client, "profile": profile, "still_missing": missing,
            "videos_to_study": videos, "skipped": skipped, "saved_to": str(path_for(client))}
