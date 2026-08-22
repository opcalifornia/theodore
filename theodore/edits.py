"""Versioned edit decision lists (v2.0 Part 2, build-order step 3).

This module is the reason Theodore never surgically mutates a live Resolve
timeline. Resolve's scripting API is far more reliable at building a
timeline from scratch than at moving clips within one, and in-place edits
fail quietly. So:

    1. The edit order lives in edits/vNNN.json as an ordered sequence of
       segment ids with per-segment overrides.
    2. Commands mutate that list, producing a NEW version.
    3. assembly/builder.py rebuilds a fresh timeline from the new list.

Every mutation forks a new version rather than editing one in place, which
buys free undo, free A/B comparison between cuts, and no risk of corrupting
an assembly you liked. `command_log` records what changed in plain language
so you can read the history without diffing frame numbers.

Nothing here talks to Resolve, or to Claude -- it's a pure version store
over JSON, so the command parser (step 4) and the builder can both be
tested against it without either being present.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("theodore.edits")

_VERSION_RE = re.compile(r"^v(\d{3,})$")


class EditListError(RuntimeError):
    pass


@dataclass
class EditEntry:
    """One clip in the cut. `in_override`/`out_override` are seconds into
    the SOURCE media, overriding whatever assembly/trim.py proposed for
    this segment -- that's how a `trim`/`extend` command pins a specific
    in/out without re-running the trim pass."""
    segment_id: str
    in_override: Optional[float] = None
    out_override: Optional[float] = None
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "EditEntry":
        return cls(
            segment_id=d["segment_id"],
            in_override=d.get("in_override"),
            out_override=d.get("out_override"),
            notes=d.get("notes"),
        )


@dataclass
class EditList:
    version: str
    parent: Optional[str]
    created: str
    command_log: list[str] = field(default_factory=list)
    sequence: list[EditEntry] = field(default_factory=list)

    @property
    def segment_ids(self) -> list[str]:
        return [e.segment_id for e in self.sequence]

    def entry_for(self, segment_id: str) -> Optional[EditEntry]:
        return next((e for e in self.sequence if e.segment_id == segment_id), None)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "parent": self.parent,
            "created": self.created,
            "command_log": list(self.command_log),
            "sequence": [asdict(e) for e in self.sequence],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditList":
        return cls(
            version=d["version"],
            parent=d.get("parent"),
            created=d.get("created", ""),
            command_log=list(d.get("command_log", [])),
            sequence=[EditEntry.from_dict(e) for e in d.get("sequence", [])],
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def edits_dir(project_dir: Path) -> Path:
    d = project_dir / "edits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def version_path(project_dir: Path, version: str) -> Path:
    return edits_dir(project_dir) / f"{version}.json"


def parse_version(version: str) -> int:
    m = _VERSION_RE.match(version)
    if not m:
        raise EditListError(f"Malformed edit version {version!r}, expected e.g. 'v001'")
    return int(m.group(1))


def format_version(number: int) -> str:
    return f"v{number:03d}"


def list_versions(project_dir: Path) -> list[str]:
    """Every stored version, oldest first."""
    d = edits_dir(project_dir)
    versions = []
    for p in d.glob("v*.json"):
        try:
            versions.append((parse_version(p.stem), p.stem))
        except EditListError:
            logger.warning("Ignoring unrecognized file in edits/: %s", p.name)
    return [name for _, name in sorted(versions)]


def next_version(project_dir: Path) -> str:
    """The next version number, derived from the highest one on disk so a
    reverted-to-then-edited history never overwrites an existing version."""
    existing = list_versions(project_dir)
    if not existing:
        return format_version(1)
    return format_version(parse_version(existing[-1]) + 1)


def load(project_dir: Path, version: str) -> EditList:
    path = version_path(project_dir, version)
    if not path.exists():
        available = ", ".join(list_versions(project_dir)) or "(none yet)"
        raise EditListError(f"No edit version {version!r} in this project. Available: {available}")
    return EditList.from_dict(json.loads(path.read_text()))


def save(project_dir: Path, edit_list: EditList) -> Path:
    path = version_path(project_dir, edit_list.version)
    if path.exists():
        raise EditListError(
            f"Refusing to overwrite existing edit version {edit_list.version!r}. "
            "Versions are immutable -- fork a new one with derive() instead."
        )
    path.write_text(json.dumps(edit_list.to_dict(), indent=2))
    logger.info("Wrote edit list %s (%d clips)", edit_list.version, len(edit_list.sequence))
    return path


def create_initial(project_dir: Path, segment_ids: list[str], *, command_log: Optional[list[str]] = None) -> EditList:
    """Seed v001 from an ordering pass's output (assembly/ordering.py)."""
    edit_list = EditList(
        version=next_version(project_dir),
        parent=None,
        created=_now_iso(),
        command_log=list(command_log or ["(seeded from ordering pass)"]),
        sequence=[EditEntry(segment_id=sid) for sid in segment_ids],
    )
    save(project_dir, edit_list)
    return edit_list


def derive(
    project_dir: Path,
    parent: EditList,
    new_sequence: list[EditEntry],
    commands: list[str],
) -> EditList:
    """Fork a new version from `parent` with a mutated sequence. The parent
    is never modified -- that immutability is what makes `revert` safe."""
    edit_list = EditList(
        version=next_version(project_dir),
        parent=parent.version,
        created=_now_iso(),
        command_log=list(commands),
        sequence=list(new_sequence),
    )
    save(project_dir, edit_list)
    return edit_list


def revert(project_dir: Path, to_version: str, *, note: Optional[str] = None) -> EditList:
    """Reverting forks a NEW version carrying the old sequence, rather than
    deleting history or resetting a pointer backwards. You can always get
    back to whatever you reverted away from."""
    source = load(project_dir, to_version)
    return derive(
        project_dir, source, list(source.sequence),
        [note or f"revert to {to_version}"],
    )


def diff(a: EditList, b: EditList) -> dict:
    """What changed between two versions, in segment terms rather than
    frame numbers."""
    a_ids, b_ids = a.segment_ids, b.segment_ids
    a_set, b_set = set(a_ids), set(b_ids)

    kept = [sid for sid in b_ids if sid in a_set]
    kept_in_a_order = [sid for sid in a_ids if sid in b_set]

    overrides_changed = []
    for entry in b.sequence:
        prior = a.entry_for(entry.segment_id)
        if prior is None:
            continue
        if (prior.in_override, prior.out_override) != (entry.in_override, entry.out_override):
            overrides_changed.append({
                "segment_id": entry.segment_id,
                "from": [prior.in_override, prior.out_override],
                "to": [entry.in_override, entry.out_override],
            })

    return {
        "from_version": a.version,
        "to_version": b.version,
        "added": [sid for sid in b_ids if sid not in a_set],
        "removed": [sid for sid in a_ids if sid not in b_set],
        "reordered": kept != kept_in_a_order,
        "overrides_changed": overrides_changed,
        "commands": list(b.command_log),
    }


def format_diff(d: dict) -> str:
    lines = [f"{d['from_version']} -> {d['to_version']}"]
    if d["commands"]:
        lines.append("  commands:")
        lines.extend(f"    - {c}" for c in d["commands"])
    if d["added"]:
        lines.append(f"  added:    {', '.join(d['added'])}")
    if d["removed"]:
        lines.append(f"  removed:  {', '.join(d['removed'])}")
    if d["reordered"]:
        lines.append("  reordered: yes")
    for oc in d["overrides_changed"]:
        lines.append(f"  in/out:   {oc['segment_id']} {oc['from']} -> {oc['to']}")
    if not (d["added"] or d["removed"] or d["reordered"] or d["overrides_changed"]):
        lines.append("  (no structural change)")
    return "\n".join(lines)


def current_version(project: dict) -> Optional[str]:
    return project.get("current_edit_version")


def set_current_version(project: dict, version: str) -> dict:
    project["current_edit_version"] = version
    return project


def load_current(project_dir: Path, project: dict) -> Optional[EditList]:
    version = current_version(project)
    if not version:
        return None
    return load(project_dir, version)


# --------------------------------------------------------------------------
# The pending queue (v2.0 Part 2): `theodore say` accumulates commands here
# without minting a real version -- "batch by default... rebuild once on
# confirmation." Confirmation (deriving a real version from this and
# clearing it) happens in the CLI's `build` command. A pending queue is
# just an EditList with version="(pending)"; it never appears in
# list_versions() (the glob only matches "v*.json") and is never load()able
# by version name.
# --------------------------------------------------------------------------

PENDING_VERSION_LABEL = "(pending)"


def pending_path(project_dir: Path) -> Path:
    return edits_dir(project_dir) / "pending.json"


def load_pending(project_dir: Path) -> Optional[EditList]:
    path = pending_path(project_dir)
    if not path.exists():
        return None
    return EditList.from_dict(json.loads(path.read_text()))


def save_pending(project_dir: Path, edit_list: EditList) -> Path:
    path = pending_path(project_dir)
    path.write_text(json.dumps(edit_list.to_dict(), indent=2))
    return path


def clear_pending(project_dir: Path) -> None:
    pending_path(project_dir).unlink(missing_ok=True)


def start_pending(project_dir: Path, base: Optional[EditList], *, save: bool = True) -> EditList:
    """A working copy of `base`'s sequence (or an empty one, if there is no
    current version yet) to accumulate commands against. `save=False` builds
    the object without writing it -- for a caller that only wants to
    persist once it has an actual change to record, rather than writing an
    identical-to-current pending.json for e.g. a read-only query."""
    pending = EditList(
        version=PENDING_VERSION_LABEL,
        parent=base.version if base else None,
        created=_now_iso(),
        command_log=[],
        sequence=list(base.sequence) if base else [],
    )
    if save:
        save_pending(project_dir, pending)
    return pending


def load_or_start_pending(project_dir: Path, project: dict) -> EditList:
    """The pending queue if one is already accumulating commands, otherwise
    a fresh one seeded from the current version (or empty)."""
    pending = load_pending(project_dir)
    if pending is not None:
        return pending
    return start_pending(project_dir, load_current(project_dir, project))


def working_sequence(project_dir: Path, project: dict) -> list:
    """The segment ids a new command should be evaluated against: the
    pending queue's if one exists, else the current version's, else empty.
    Read-only -- unlike load_or_start_pending, this never creates
    pending.json, so a caller can build context/preview without a
    read causing a write."""
    pending = load_pending(project_dir)
    if pending is not None:
        return pending.segment_ids
    current = load_current(project_dir, project)
    return current.segment_ids if current else []


def apply_overrides_to_trims(edit_list: EditList, trims: dict) -> dict:
    """Fold an edit list's per-segment in/out overrides into a trims dict so
    assembly/plan.py picks them up without needing to know edit lists exist.

    plan.build_plan() reads `trimmed_start`/`trimmed_end` (and clamps
    handles to `original_start`/`original_end`), so an override is applied
    by rewriting those two fields. The original bounds are widened to
    include the override when it reaches outside them -- that's what makes
    an `extend` command able to reach past what the trim pass proposed,
    which is the entire point of the operation.
    """
    merged = {sid: dict(t) for sid, t in trims.items()}
    for entry in edit_list.sequence:
        if entry.in_override is None and entry.out_override is None:
            continue
        t = merged.get(entry.segment_id)
        if t is None:
            # No trim proposal for this segment: synthesize one from the
            # overrides alone. A one-sided override with no baseline can't
            # be resolved here, so leave it for plan.py's own fallback.
            if entry.in_override is None or entry.out_override is None:
                logger.warning(
                    "Segment %s has a one-sided in/out override but no trim entry to anchor it; ignoring",
                    entry.segment_id,
                )
                continue
            merged[entry.segment_id] = {
                "segment_id": entry.segment_id,
                "original_start": entry.in_override,
                "original_end": entry.out_override,
                "trimmed_start": entry.in_override,
                "trimmed_end": entry.out_override,
                "cuts": [],
            }
            continue

        if entry.in_override is not None:
            t["trimmed_start"] = entry.in_override
            t["original_start"] = min(t.get("original_start", entry.in_override), entry.in_override)
        if entry.out_override is not None:
            t["trimmed_end"] = entry.out_override
            t["original_end"] = max(t.get("original_end", entry.out_override), entry.out_override)
    return merged
