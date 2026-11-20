"""Per-project memory: what the agent did, and what the editor prefers.

One small JSON file per Resolve project under the data dir. It is fed back
into the system prompt, so a stated preference ("captions in white, lower
third", "name renders <project>_<n>") sticks across sessions instead of being
re-explained every time.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from theodore import config

MAX_LOG_ENTRIES = 200
PROMPT_LOG_ENTRIES = 12


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "project"


def memory_path(project_name: str, root: Optional[Path] = None) -> Path:
    base = Path(root) if root else config.DATA_ROOT
    return base / "agent_memory" / f"{_slug(project_name)}.json"


def load(project_name: str, root: Optional[Path] = None) -> dict:
    path = memory_path(project_name, root)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("preferences", {})
    data.setdefault("log", [])
    return data


def _save(project_name: str, data: dict, root: Optional[Path] = None) -> None:
    path = memory_path(project_name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["log"] = data["log"][-MAX_LOG_ENTRIES:]
    path.write_text(json.dumps(data, indent=2))


def set_preference(project_name: str, key: str, value: str, root: Optional[Path] = None) -> None:
    data = load(project_name, root)
    data["preferences"][key] = value
    _save(project_name, data, root)


def record_action(project_name: str, instruction: str, summary: str, root: Optional[Path] = None) -> None:
    data = load(project_name, root)
    data["log"].append({
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "instruction": instruction,
        "summary": summary,
    })
    _save(project_name, data, root)


#: The editor's house style: preferences that apply to every project.
STUDIO = "__studio__"


def prompt_block(project_name: str, root: Optional[Path] = None) -> str:
    """Text for the system prompt; empty when there is nothing to recall."""
    lines = []
    studio = load(STUDIO, root)["preferences"]
    if studio:
        lines.append("The editor's house style (every project; a project preference below wins):")
        lines += [f"- {k}: {v}" for k, v in sorted(studio.items())]
    data = load(project_name, root) if project_name else {"preferences": {}, "log": []}
    if data["preferences"]:
        lines.append("Editor preferences for this project (follow them unless told otherwise):")
        lines += [f"- {k}: {v}" for k, v in sorted(data["preferences"].items())]
    recent = data["log"][-PROMPT_LOG_ENTRIES:]
    if recent:
        lines.append("Recent work on this project:")
        lines += [f"- [{e['at']}] {e['instruction']} -> {e['summary']}" for e in recent]
    return "\n".join(lines)
