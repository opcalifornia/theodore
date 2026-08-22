"""Project registry (v2.0 Part 1): restructures Theodore from "one interview
per project" to "one project, many subjects," addressed by immutable
per-subject question ids (e.g. "haylee.q03").

    data/<project>/
    ├── project.json            # this module's schema
    ├── subjects/<subject_id>/   # everything ingest/transcribe/analyze used
    │                             # to write flat into data/<project>/ now
    │                             # lives here, one directory per subject
    ├── edits/                   # versioned edit decision lists (not yet built)
    └── exports/                 # not yet built

Nothing about ingest/transcribe/analyze/export/resolve/assembly's own file
formats changes -- they already operate on explicit paths and plain dicts.
Only *where* those paths point changes, plus segment ids now come from
analyze.id_matching instead of being blindly renumbered every run.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("theodore.registry")

PROJECT_FILE = "project.json"

_SUBJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Files/dirs a pre-registry (v1/v1.5) project wrote flat into data/<project>/.
_LEGACY_FILES = (
    "media.json", "transcript_raw.json", "transcript.json", "speakers.json",
    "segments.json", "selects.json", "themes.json", "analysis.json",
    "trims.json",
)
_LEGACY_DIRS = ("audio", "transcript_cache")
_LEGACY_MARKERS = ("transcript.json", "media.json", "analysis.json")


class RegistryError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify_subject_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not slug:
        raise RegistryError(f"Could not derive a subject id from {name!r}")
    return slug


def project_json_path(project_dir: Path) -> Path:
    return project_dir / PROJECT_FILE


def _default_project(project_name: str) -> dict:
    return {
        "project": project_name,
        "created": _now_iso(),
        "subjects": {},
        "question_guide": [],
        "addressing": "sequential",
        "current_edit_version": None,
    }


def is_legacy_project(project_dir: Path) -> bool:
    """True if this directory has v1/v1.5-era flat files (no project.json)
    that should be migrated rather than treated as a fresh, empty project."""
    if project_json_path(project_dir).exists():
        return False
    return any((project_dir / marker).exists() for marker in _LEGACY_MARKERS)


def migrate_legacy_project(project_dir: Path, subject_name: str) -> dict:
    """Non-destructive migration: COPIES (never moves or deletes) the old
    flat data/<project>/*.json layout into subjects/<subject_id>/, and
    writes a fresh project.json pointing at it. The original flat files are
    left in place untouched, as a backup, in case the migration needs to be
    redone or inspected.
    """
    subject_id = slugify_subject_id(subject_name)
    dest = subject_dir(project_dir, subject_id)

    copied = []
    for name in _LEGACY_FILES:
        src = project_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied.append(name)
    for dirname in _LEGACY_DIRS:
        src = project_dir / dirname
        if src.exists() and not (dest / dirname).exists():
            shutil.copytree(src, dest / dirname)
            copied.append(dirname + "/")

    logger.info(
        "Migrated legacy project at %s into subjects/%s/ (%s)",
        project_dir, subject_id, ", ".join(copied) or "nothing found to copy",
    )

    project = _default_project(project_dir.name)
    register_subject(project, subject_id, display_name=subject_name)
    # A migrated legacy project has already been through the segmenter with
    # plain "sNNN" ids -- start the immutable counter above any of those so
    # a subsequent re-analyze can't collide with them.
    try:
        existing = json.loads((dest / "segments.json").read_text())
        max_num = 0
        for seg in existing.get("segments", []):
            m = re.match(r"^s0*(\d+)$", str(seg.get("id", "")))
            if m:
                max_num = max(max_num, int(m.group(1)))
        if max_num:
            set_next_question_number(project, subject_id, max_num + 1)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    save_project(project_dir, project)
    return project


def load_project(project_dir: Path, project_name: Optional[str] = None) -> dict:
    """Loads project.json, migrating a legacy flat-layout project in place
    if one is detected. Always returns a valid registry dict, creating a
    fresh one if this is a brand new project with nothing on disk yet."""
    path = project_json_path(project_dir)
    if path.exists():
        return json.loads(path.read_text())

    name = project_name or project_dir.name
    if is_legacy_project(project_dir):
        return migrate_legacy_project(project_dir, name)

    project = _default_project(name)
    save_project(project_dir, project)
    return project


def save_project(project_dir: Path, project: dict) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_json_path(project_dir)
    path.write_text(json.dumps(project, indent=2))
    return path


def subject_dir(project_dir: Path, subject_id: str) -> Path:
    d = project_dir / "subjects" / subject_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_subject(
    project: dict,
    subject_id: str,
    *,
    display_name: Optional[str] = None,
    role: str = "subject",
) -> dict:
    """Adds `subject_id` to the registry if not already present. Idempotent
    -- calling this on ingest every time is fine and expected."""
    if not _SUBJECT_ID_RE.match(subject_id):
        raise RegistryError(
            f"Invalid subject id {subject_id!r}: must be lowercase letters, digits, "
            "underscores, or hyphens, starting with a letter or digit."
        )
    subjects = project.setdefault("subjects", {})
    if subject_id not in subjects:
        subjects[subject_id] = {
            "display_name": display_name or subject_id,
            "role": role,
            "source_files": [],
            "speaker_map": {},
            "next_question_number": 1,
        }
    return project


def require_subject(project: dict, subject_id: str) -> dict:
    subjects = project.get("subjects", {})
    if subject_id not in subjects:
        available = ", ".join(sorted(subjects)) or "(none registered yet)"
        raise RegistryError(f"No subject {subject_id!r} in this project. Registered subjects: {available}")
    return subjects[subject_id]


def next_question_number(project: dict, subject_id: str) -> int:
    return require_subject(project, subject_id).get("next_question_number", 1)


def set_next_question_number(project: dict, subject_id: str, value: int) -> None:
    require_subject(project, subject_id)["next_question_number"] = value


def list_subjects(project: dict) -> list[str]:
    return list(project.get("subjects", {}).keys())
