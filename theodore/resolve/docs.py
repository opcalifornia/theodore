"""Search Resolve's own scripting documentation.

Every Resolve install carries the full API reference (README.txt in the
Developer/Scripting folder): every object, method, setting key and constant
for *that* version. Searching it beats remembering it -- the agent looks up
exactly what this Resolve can do before reaching for the escape hatch.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from theodore.resolve import paths


@lru_cache(maxsize=1)
def _readme_lines() -> tuple[str, ...]:
    detected = paths.detect_scripting_paths()
    candidates = [Path(detected.api_dir) / "README.txt"] if getattr(detected, "api_dir", None) else []
    candidates += [Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/README.txt")]
    for path in candidates:
        if path.exists():
            return tuple(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return ()


def _section_of(lines: tuple[str, ...], i: int) -> str:
    # Object sections look like "Timeline" / "TimelineItem" alone on a line,
    # followed by a line of dashes or methods; settings sections have a dashed underline.
    for j in range(i, -1, -1):
        line = lines[j].strip()
        nxt = lines[j + 1].strip() if j + 1 < len(lines) else ""
        if line and re.fullmatch(r"[A-Za-z][A-Za-z ]{1,60}", line) and (nxt.startswith("-") or nxt.startswith(" ") or not nxt):
            return line
    return ""


def search(query: str, limit: int = 30) -> dict:
    lines = _readme_lines()
    if not lines:
        return {"error": "Resolve's scripting README wasn't found on this machine."}
    terms = [t.lower() for t in re.findall(r"[\w.]+", query) if len(t) > 1]
    if not terms:
        return {"error": "Give some words to search for, e.g. 'subtitle', 'SmartReframe', 'render settings'."}
    scored = []
    for i, line in enumerate(lines):
        low = line.lower()
        hits = sum(1 for t in terms if t in low)
        if hits:
            # Method signatures are worth more than prose mentioning the word.
            bonus = 1 if re.match(r"\s{2}\w+\(", line) else 0
            scored.append((hits + bonus, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    seen, results = set(), []
    for _score, i in scored:
        if i in seen:
            continue
        block = []
        for k in range(i, min(i + 4, len(lines))):
            if k > i and (re.match(r"\s{2}\w+\(", lines[k]) or not lines[k].strip()):
                break
            block.append(lines[k].rstrip())
            seen.add(k)
        results.append({"section": _section_of(lines, i), "text": "\n".join(block)})
        if len(results) >= limit:
            break
    return {"query": query, "matches": results,
            "note": "From this Resolve's own Developer/Scripting/README.txt."}


def section(name: str, max_lines: int = 160) -> Optional[str]:
    """A whole object/settings section, e.g. 'TimelineItem' or 'Auto Caption Settings'."""
    lines = _readme_lines()
    target = name.strip().lower()
    for i, line in enumerate(lines):
        if line.strip().lower() == target or (target in line.strip().lower() and len(line.strip()) < 70
                                                and i + 1 < len(lines) and lines[i + 1].strip().startswith("-")):
            out = []
            for k in range(i, min(i + max_lines, len(lines))):
                if k > i + 2 and lines[k].strip() and re.fullmatch(r"[A-Za-z][A-Za-z ]{1,60}", lines[k].strip()) \
                        and k + 1 < len(lines) and lines[k + 1].strip().startswith("-") and lines[k + 1].strip().strip("-") == "":
                    break
                out.append(lines[k].rstrip())
            return "\n".join(out)
    return None
