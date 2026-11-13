"""Natural-language command layer (v2.0 Part 2): `theodore say` turns plain
language into a validated, structured edit operation applied to the pending
edit queue (theodore/edits.py's ``load_or_start_pending``/``save_pending``).

The split mirrors every Claude pass elsewhere in this codebase: a single
Sonnet call (parse_command, via analyze.claude_client) decides WHAT the
editor means, and everything that turns that into an actual sequence change
(apply_operation and friends) is deterministic Python, fully testable
without an API key. Validation happens in Python too, against the real
segment registry, regardless of what Claude returned -- "validation before
execution, always" from the spec, not just a prompt instruction.

Two operations (`trim`, `extend`) deliberately never carry a timestamp
Claude computed itself. Claude identifies *which sentence* or *how many
seconds*; resolve_sentence_boundary() below turns that into a real second
from actual word timings. This is the same "measured facts, not
model-guessed numbers" principle the rest of Theodore follows.
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config, edits
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.commands")

PROMPT_PATH = Path(__file__).parent / "analyze" / "prompts" / "command_parser.md"
MAX_TOKENS = 1500

OPS = ("move", "remove", "insert", "swap", "trim", "extend", "replace", "reorder", "filter", "query")
_POSITIONS = ("before", "after", "top", "bottom")
_SIDES = ("in", "out", "both")


class CommandError(RuntimeError):
    """A parsed command failed validation or couldn't be applied. Always
    carries a message meant to be shown directly to the editor."""


@dataclass
class CommandResult:
    status: str  # "queued" | "answered" | "ambiguous" | "error"
    raw_text: str
    message: Optional[str] = None       # error / ambiguous question
    candidates: list = field(default_factory=list)
    sequence: list = field(default_factory=list)   # queued: the resulting order
    command_log: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Registry context -- pure text formatting, what Claude sees
# --------------------------------------------------------------------------

def build_registry_context(subjects_segments: dict, current_sequence: list) -> str:
    """`subjects_segments` is {subject_id: [segment dict, ...]} covering
    every segment across every subject (not just those in the current
    sequence) so `replace`/`insert`/`filter` can reach material that isn't
    in the cut yet. Segment dicts are expected to carry an optional
    `_strength` and `_theme_labels` (see selects_index_for below)."""
    lines = ["REGISTRY (every known segment, by subject):"]
    for subject_id, segments in subjects_segments.items():
        lines.append(f"\n[{subject_id}]")
        for seg in segments:
            q = seg.get("question_text") or "(volunteered)"
            strength = seg.get("_strength")
            strength_str = f"strength={strength:.2f}" if strength is not None else "strength=?"
            themes = ", ".join(seg.get("_theme_labels") or []) or "no themes"
            lines.append(f"  [{seg['id']}] {strength_str} | {themes} | Q: {q} | A: {seg.get('answer_summary', '')}")

    lines.append("\nCURRENT SEQUENCE (in order, position 1 first):")
    if current_sequence:
        for i, sid in enumerate(current_sequence, start=1):
            lines.append(f"  {i}. {sid}")
    else:
        lines.append("  (empty -- nothing queued yet)")

    return "\n".join(lines)


def index_segments_with_metadata(analysis: dict) -> list:
    """analysis["segments"], each annotated with _strength/_theme_labels
    pulled from analysis["selects"]/["themes"]/["theme_assignments"] --
    the shape build_registry_context() and apply_filter() expect."""
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    themes_by_id = {t["id"]: t for t in analysis.get("themes", [])}
    assignments = analysis.get("theme_assignments", {})

    annotated = []
    for seg in analysis.get("segments", []):
        sel = selects_by_id.get(seg["id"])
        theme_ids = assignments.get(seg["id"], [])
        annotated.append({
            **seg,
            "_strength": sel.get("strength") if sel else None,
            "_theme_labels": [themes_by_id[t]["label"] for t in theme_ids if t in themes_by_id],
        })
    return annotated


# --------------------------------------------------------------------------
# The Claude call
# --------------------------------------------------------------------------

def parse_command(
    text: str,
    context: str,
    *,
    cost_tracker: Optional[CostTracker] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    cost_tracker = cost_tracker or CostTracker()
    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    system = PROMPT_PATH.read_text()
    user = f"{context}\n\nInstruction: {text}"
    return call_json(
        client, pass_name="command_parser", model=config.COMMAND_PARSER_MODEL,
        system=system, user=user, max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
    )


# --------------------------------------------------------------------------
# Id validation -- never trust the model's ids without checking
# --------------------------------------------------------------------------

def _suggest(bad_id: Optional[str], known_ids) -> list:
    if not bad_id:
        return []
    return difflib.get_close_matches(bad_id, list(known_ids), n=3, cutoff=0.5)


def validate_referenced_ids(parsed: dict, known_ids) -> Optional[str]:
    """None if every segment id `parsed` references is real; otherwise an
    error message (with nearest-match suggestions) ready to show the editor.
    """
    fields_by_op = {
        "move": ("target", "anchor"), "remove": ("target",), "insert": ("target", "anchor"),
        "swap": ("target", "anchor"), "trim": ("target",), "extend": ("target",),
        "replace": ("target", "replacement"),
    }
    for field_name in fields_by_op.get(parsed.get("op"), ()):
        value = parsed.get(field_name)
        if value is None:
            continue
        if value not in known_ids:
            suggestions = _suggest(value, known_ids)
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            return f"'{value}' isn't a known segment id.{hint}"

    if parsed.get("op") == "reorder":
        known_subjects = {sid.split(".", 1)[0] for sid in known_ids}
        for subject_id in parsed.get("groups") or []:
            if subject_id not in known_subjects:
                return f"'{subject_id}' isn't a registered subject in this project."
    return None


# --------------------------------------------------------------------------
# trim's sentence resolution -- deterministic, from real word timings
# --------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]$")


def resolve_sentence_boundary(words: list, sentence_index: int) -> Optional[float]:
    """Start time (seconds) of the Nth sentence (1-based) in `words` (as
    returned by assembly.trim.kept_words/get_segment_words), or None if
    there aren't that many sentences. A "sentence" here is just a run of
    words ending in ./!/? -- good enough to resolve "the second sentence"
    without inventing a timestamp Claude made up.
    """
    if sentence_index < 1 or not words:
        return None
    sentence_starts = [words[0]["start"]]
    for i, w in enumerate(words[:-1]):
        if _SENTENCE_END_RE.search(w["word"]):
            sentence_starts.append(words[i + 1]["start"])
    if sentence_index > len(sentence_starts):
        return None
    return sentence_starts[sentence_index - 1]


# --------------------------------------------------------------------------
# Applying an operation to a sequence of EditEntry
# --------------------------------------------------------------------------

def _entries_by_id(entries: list) -> dict:
    return {e.segment_id: e for e in entries}


def _remove_entry(entries: list, segment_id: str) -> list:
    return [e for e in entries if e.segment_id != segment_id]


def _insert_at(entries: list, new_entry, position: str, anchor: Optional[str]) -> list:
    entries = list(entries)
    if position == "top":
        return [new_entry] + entries
    if position == "bottom":
        return entries + [new_entry]
    idx = next((i for i, e in enumerate(entries) if e.segment_id == anchor), None)
    if idx is None:
        raise CommandError(f"Anchor '{anchor}' is not in the current sequence.")
    insert_at = idx + 1 if position == "after" else idx
    return entries[:insert_at] + [new_entry] + entries[insert_at:]


def apply_move(entries: list, target: str, position: str, anchor: Optional[str]) -> list:
    by_id = _entries_by_id(entries)
    if target not in by_id:
        raise CommandError(f"'{target}' is not in the current sequence -- did you mean `insert`?")
    remaining = _remove_entry(entries, target)
    return _insert_at(remaining, by_id[target], position, anchor)


def apply_remove(entries: list, target: str) -> list:
    if target not in _entries_by_id(entries):
        raise CommandError(f"'{target}' is not in the current sequence -- nothing to remove.")
    return _remove_entry(entries, target)


def apply_insert(entries: list, target: str, position: str, anchor: Optional[str]) -> list:
    if target in _entries_by_id(entries):
        raise CommandError(f"'{target}' is already in the sequence -- did you mean `move`?")
    return _insert_at(entries, edits.EditEntry(target), position, anchor)


def apply_swap(entries: list, target: str, anchor: str) -> list:
    by_id = _entries_by_id(entries)
    if target not in by_id or anchor not in by_id:
        missing = target if target not in by_id else anchor
        raise CommandError(f"'{missing}' is not in the current sequence.")
    entries = list(entries)
    i, j = (next(k for k, e in enumerate(entries) if e.segment_id == sid) for sid in (target, anchor))
    entries[i], entries[j] = entries[j], entries[i]
    return entries


def apply_replace(entries: list, target: str, replacement: str) -> list:
    by_id = _entries_by_id(entries)
    if target not in by_id:
        raise CommandError(f"'{target}' is not in the current sequence -- nothing to replace.")
    if replacement in by_id:
        raise CommandError(f"'{replacement}' is already in the sequence.")
    return [edits.EditEntry(replacement) if e.segment_id == target else e for e in entries]


def apply_reorder(entries: list, groups: list) -> list:
    """Stable-sorts `entries` so every segment from groups[0]'s subject
    precedes groups[1]'s, etc., preserving each subject's own relative
    order. Segments from a subject not named in `groups` keep their
    original relative position, sorted after all named groups."""
    rank = {subject_id: i for i, subject_id in enumerate(groups)}
    return sorted(entries, key=lambda e: rank.get(e.segment_id.split(".", 1)[0], len(groups)))


def apply_filter(entries: list, threshold: float, comparison: str, strength_of) -> list:
    """`strength_of(segment_id) -> Optional[float]`. A segment with no
    strength score survives any filter -- there's nothing to compare."""
    def keep(entry) -> bool:
        strength = strength_of(entry.segment_id)
        if strength is None:
            return True
        return strength >= threshold if comparison == "below" else strength <= threshold

    return [e for e in entries if keep(e)]


def apply_trim(entries: list, target: str, side: str, sentence_index: int, words_of) -> list:
    """`words_of(segment_id) -> list[word]` (kept words, chronological)."""
    by_id = _entries_by_id(entries)
    if target not in by_id:
        raise CommandError(f"'{target}' is not in the current sequence.")
    boundary = resolve_sentence_boundary(words_of(target), sentence_index)
    if boundary is None:
        raise CommandError(f"'{target}' doesn't have a sentence #{sentence_index} to trim to.")
    entry = by_id[target]
    if side in ("in", "both"):
        entry.in_override = boundary
    if side == "out":
        entry.out_override = boundary
    return entries


def apply_extend(entries: list, target: str, side: str, delta_seconds: float, bounds_of) -> list:
    """`bounds_of(segment_id) -> (current_in, current_out)` -- the effective
    in/out before this command, from the entry's own override or the
    segment's trimmed/clean range, whichever applies."""
    by_id = _entries_by_id(entries)
    if target not in by_id:
        raise CommandError(f"'{target}' is not in the current sequence.")
    entry = by_id[target]
    current_in, current_out = bounds_of(target)
    if side in ("in", "both"):
        entry.in_override = max(0.0, current_in - delta_seconds)
    if side in ("out", "both"):
        entry.out_override = current_out + delta_seconds
    return entries


def apply_operation(parsed: dict, entries: list, *, strength_of=None, words_of=None, bounds_of=None) -> list:
    """Dispatches `parsed` (a validated command_parser.md response) to the
    matching apply_* function. Raises CommandError on anything that can't
    be applied even though its ids were valid (e.g. an anchor not
    currently in the sequence)."""
    op = parsed.get("op")
    if op == "move":
        return apply_move(entries, parsed["target"], parsed["position"], parsed.get("anchor"))
    if op == "remove":
        return apply_remove(entries, parsed["target"])
    if op == "insert":
        return apply_insert(entries, parsed["target"], parsed["position"], parsed.get("anchor"))
    if op == "swap":
        return apply_swap(entries, parsed["target"], parsed["anchor"])
    if op == "replace":
        return apply_replace(entries, parsed["target"], parsed["replacement"])
    if op == "reorder":
        return apply_reorder(entries, parsed["groups"])
    if op == "filter":
        if strength_of is None:
            raise CommandError("filter needs strength data that wasn't provided.")
        return apply_filter(entries, parsed["threshold"], parsed.get("comparison", "below"), strength_of)
    if op == "trim":
        if words_of is None:
            raise CommandError("trim needs transcript data that wasn't provided.")
        return apply_trim(entries, parsed["target"], parsed["side"], parsed["sentence_index"], words_of)
    if op == "extend":
        if bounds_of is None:
            raise CommandError("extend needs trim data that wasn't provided.")
        return apply_extend(entries, parsed["target"], parsed["side"], parsed.get("delta_seconds", 2.0), bounds_of)
    raise CommandError(f"Unsupported operation {op!r}.")


# --------------------------------------------------------------------------
# Orchestration: one `theodore say` call, start to finish
# --------------------------------------------------------------------------

def say(
    project_dir: Path,
    project: dict,
    text: str,
    subjects_segments: dict,
    *,
    strength_of=None,
    words_of=None,
    bounds_of=None,
    cost_tracker: Optional[CostTracker] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> CommandResult:
    """Parses and applies ONE instruction against the pending queue
    (seeding it from the current edit list if nothing is pending yet).
    Never mints a real edit list version or touches Resolve -- confirmation
    happens at `theodore build`, which folds the queue into a derived
    version and clears it.
    """
    # A read-only peek: builds context from whatever's already queued (or
    # the current version) without writing pending.json. A query/ambiguous/
    # error response must leave no trace on disk -- only an actual applied
    # mutation persists anything.
    working_ids = edits.working_sequence(project_dir, project)
    pending = edits.load_pending(project_dir)
    working_entries = list(pending.sequence) if pending else [edits.EditEntry(sid) for sid in working_ids]

    known_ids = {sid for segs in subjects_segments.values() for sid in (s["id"] for s in segs)}
    context = build_registry_context(subjects_segments, working_ids)
    parsed = parse_command(text, context, cost_tracker=cost_tracker, client=client)

    op = parsed.get("op")
    if op == "query":
        return CommandResult(status="answered", raw_text=text, message=parsed.get("answer", ""))
    if op == "ambiguous":
        return CommandResult(
            status="ambiguous", raw_text=text,
            message=parsed.get("question", "Which segment did you mean?"),
            candidates=parsed.get("candidates", []),
        )
    if op == "error":
        return CommandResult(status="error", raw_text=text, message=parsed.get("message", "Could not parse that."))
    if op not in OPS:
        return CommandResult(status="error", raw_text=text, message=f"Got an unrecognized operation {op!r} back.")

    id_error = validate_referenced_ids(parsed, known_ids)
    if id_error:
        return CommandResult(status="error", raw_text=text, message=id_error)

    try:
        new_entries = apply_operation(
            parsed, working_entries,
            strength_of=strength_of, words_of=words_of, bounds_of=bounds_of,
        )
    except CommandError as exc:
        return CommandResult(status="error", raw_text=text, message=str(exc))

    if pending is None:
        pending = edits.start_pending(project_dir, edits.load_current(project_dir, project), save=False)
    pending.sequence = new_entries
    pending.command_log = list(pending.command_log) + [text]
    edits.save_pending(project_dir, pending)

    return CommandResult(
        status="queued", raw_text=text,
        sequence=pending.segment_ids, command_log=pending.command_log,
    )
