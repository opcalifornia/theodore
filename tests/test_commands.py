import json
from dataclasses import dataclass, field

from theodore import commands, edits


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 10


@dataclass
class _Message:
    content: list
    usage: _Usage = field(default_factory=_Usage)


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Message(content=[_Block(text=self._responses.pop(0))])


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _resp(obj):
    return json.dumps(obj)


def _e(*ids):
    return [edits.EditEntry(sid) for sid in ids]


def _ids(entries):
    return [e.segment_id for e in entries]


# --- pure apply_* transformations ---

def test_apply_move_after_anchor():
    result = commands.apply_move(_e("a", "b", "c"), "c", "after", "a")
    assert _ids(result) == ["a", "c", "b"]


def test_apply_move_top_and_bottom():
    assert _ids(commands.apply_move(_e("a", "b", "c"), "c", "top", None)) == ["c", "a", "b"]
    assert _ids(commands.apply_move(_e("a", "b", "c"), "a", "bottom", None)) == ["b", "c", "a"]


def test_apply_move_target_not_present_raises():
    try:
        commands.apply_move(_e("a", "b"), "z", "top", None)
        assert False, "expected CommandError"
    except commands.CommandError as exc:
        assert "insert" in str(exc)


def test_apply_remove():
    assert _ids(commands.apply_remove(_e("a", "b", "c"), "b")) == ["a", "c"]


def test_apply_remove_missing_raises():
    try:
        commands.apply_remove(_e("a"), "z")
        assert False
    except commands.CommandError:
        pass


def test_apply_insert_after_anchor():
    result = commands.apply_insert(_e("a", "b"), "new", "after", "a")
    assert _ids(result) == ["a", "new", "b"]


def test_apply_insert_already_present_raises():
    try:
        commands.apply_insert(_e("a", "b"), "a", "top", None)
        assert False
    except commands.CommandError as exc:
        assert "move" in str(exc)


def test_apply_swap():
    result = commands.apply_swap(_e("a", "b", "c"), "a", "c")
    assert _ids(result) == ["c", "b", "a"]


def test_apply_replace_preserves_position():
    result = commands.apply_replace(_e("a", "b", "c"), "b", "z")
    assert _ids(result) == ["a", "z", "c"]


def test_apply_replace_already_present_raises():
    try:
        commands.apply_replace(_e("a", "b"), "a", "b")
        assert False
    except commands.CommandError:
        pass


def test_apply_reorder_groups_by_subject_preserving_relative_order():
    entries = _e("marcus.q02", "haylee.q01", "marcus.q01", "haylee.q03")
    result = commands.apply_reorder(entries, ["haylee", "marcus"])
    assert _ids(result) == ["haylee.q01", "haylee.q03", "marcus.q02", "marcus.q01"]


def test_apply_reorder_unnamed_subject_sorts_last():
    entries = _e("x.q01", "haylee.q01")
    result = commands.apply_reorder(entries, ["haylee"])
    assert _ids(result) == ["haylee.q01", "x.q01"]


def test_apply_filter_below_threshold():
    strengths = {"a": 0.3, "b": 0.8, "c": None}
    result = commands.apply_filter(_e("a", "b", "c"), 0.5, "below", strengths.get)
    # "a" (0.3 < 0.5) removed; "b" kept; "c" (unscored) always survives.
    assert _ids(result) == ["b", "c"]


def test_apply_filter_above_threshold():
    strengths = {"a": 0.3, "b": 0.8}
    result = commands.apply_filter(_e("a", "b"), 0.5, "above", strengths.get)
    assert _ids(result) == ["a"]


# --- resolve_sentence_boundary: deterministic, from real word timings ---

def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "confidence": 0.9}


def test_resolve_sentence_boundary_finds_second_sentence():
    words = [_w("Hello.", 0.0, 0.3), _w("It", 0.5, 0.6), _w("was", 0.6, 0.8), _w("great.", 0.8, 1.1)]
    assert commands.resolve_sentence_boundary(words, 1) == 0.0
    assert commands.resolve_sentence_boundary(words, 2) == 0.5


def test_resolve_sentence_boundary_out_of_range_returns_none():
    words = [_w("Hi.", 0.0, 0.2)]
    assert commands.resolve_sentence_boundary(words, 5) is None


def test_resolve_sentence_boundary_empty_words_returns_none():
    assert commands.resolve_sentence_boundary([], 1) is None


def test_apply_trim_sets_in_override_from_sentence():
    words = [_w("Hello.", 0.0, 0.3), _w("Second", 1.0, 1.2), _w("part.", 1.2, 1.5)]
    entries = commands.apply_trim(_e("a"), "a", "in", 2, lambda sid: words)
    assert entries[0].in_override == 1.0


def test_apply_trim_unresolvable_sentence_raises():
    try:
        commands.apply_trim(_e("a"), "a", "in", 9, lambda sid: [_w("Hi.", 0.0, 0.2)])
        assert False
    except commands.CommandError:
        pass


def test_apply_extend_widens_both_sides():
    entries = commands.apply_extend(_e("a"), "a", "both", 2.0, lambda sid: (10.0, 20.0))
    assert entries[0].in_override == 8.0
    assert entries[0].out_override == 22.0


def test_apply_extend_clamps_in_at_zero():
    entries = commands.apply_extend(_e("a"), "a", "in", 5.0, lambda sid: (2.0, 20.0))
    assert entries[0].in_override == 0.0


# --- id validation ---

_KNOWN = {"haylee.q01", "haylee.q02", "marcus.q04"}


def test_validate_referenced_ids_accepts_known_ids():
    parsed = {"op": "move", "target": "haylee.q01", "anchor": "marcus.q04", "position": "after"}
    assert commands.validate_referenced_ids(parsed, _KNOWN) is None


def test_validate_referenced_ids_rejects_unknown_with_suggestion():
    parsed = {"op": "move", "target": "haylee.q1", "position": "top"}  # typo: missing zero
    error = commands.validate_referenced_ids(parsed, _KNOWN)
    assert error is not None
    assert "haylee.q01" in error


def test_validate_referenced_ids_checks_reorder_subjects():
    parsed = {"op": "reorder", "groups": ["haylee", "diego"]}
    error = commands.validate_referenced_ids(parsed, _KNOWN)
    assert "diego" in error


# --- registry context formatting ---

def test_build_registry_context_includes_segments_and_sequence():
    segs = {"haylee": [{"id": "haylee.q01", "question_text": "Q1?", "answer_summary": "A1", "_strength": 0.7, "_theme_labels": ["War"]}]}
    text = commands.build_registry_context(segs, ["haylee.q01"])
    assert "haylee.q01" in text
    assert "strength=0.70" in text
    assert "War" in text
    assert "1. haylee.q01" in text


def test_build_registry_context_empty_sequence_says_so():
    text = commands.build_registry_context({}, [])
    assert "empty" in text.lower()


def test_index_segments_with_metadata_pulls_strength_and_themes():
    analysis = {
        "segments": [{"id": "s1", "question_text": "Q"}],
        "selects": [{"segment_id": "s1", "strength": 0.9}],
        "themes": [{"id": "t1", "label": "Fear"}],
        "theme_assignments": {"s1": ["t1"]},
    }
    annotated = commands.index_segments_with_metadata(analysis)
    assert annotated[0]["_strength"] == 0.9
    assert annotated[0]["_theme_labels"] == ["Fear"]


# --- say(): full orchestration against a fake Claude client ---

_SUBJECTS_SEGMENTS = {
    "haylee": [
        {"id": "haylee.q01", "question_text": "Q1", "answer_summary": "A1", "_strength": 0.9, "_theme_labels": []},
        {"id": "haylee.q02", "question_text": "Q2", "answer_summary": "A2", "_strength": 0.3, "_theme_labels": []},
    ],
    "marcus": [
        {"id": "marcus.q01", "question_text": "Q1", "answer_summary": "A1", "_strength": 0.5, "_theme_labels": []},
    ],
}


def test_say_queued_move_persists_pending_and_returns_new_order(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02", "marcus.q01"])
    project = {"current_edit_version": "v001"}
    client = FakeClient([_resp({"op": "move", "target": "marcus.q01", "position": "top"})])

    result = commands.say(tmp_path, project, "move marcus up top", _SUBJECTS_SEGMENTS, client=client)

    assert result.status == "queued"
    assert result.sequence == ["marcus.q01", "haylee.q01", "haylee.q02"]
    assert result.command_log == ["move marcus up top"]
    # And it's actually on disk for the next `say` call to build on.
    pending = edits.load_pending(tmp_path)
    assert pending.segment_ids == ["marcus.q01", "haylee.q01", "haylee.q02"]


def test_say_accumulates_across_multiple_calls(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02"])
    project = {"current_edit_version": "v001"}
    client = FakeClient([
        _resp({"op": "remove", "target": "haylee.q02"}),
        _resp({"op": "insert", "target": "marcus.q01", "position": "bottom"}),
    ])

    r1 = commands.say(tmp_path, project, "drop haylee 2", _SUBJECTS_SEGMENTS, client=client)
    r2 = commands.say(tmp_path, project, "add marcus 1 at the end", _SUBJECTS_SEGMENTS, client=client)

    assert r1.sequence == ["haylee.q01"]
    assert r2.sequence == ["haylee.q01", "marcus.q01"]
    assert r2.command_log == ["drop haylee 2", "add marcus 1 at the end"]


def test_say_query_does_not_touch_pending(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01"])
    project = {"current_edit_version": "v001"}
    client = FakeClient([_resp({"op": "query", "answer": "haylee.q01 is the strongest."})])

    result = commands.say(tmp_path, project, "who's strongest?", _SUBJECTS_SEGMENTS, client=client)

    assert result.status == "answered"
    assert "strongest" in result.message
    assert edits.load_pending(tmp_path) is None


def test_say_ambiguous_reports_candidates_without_mutating(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02"])
    project = {"current_edit_version": "v001"}
    client = FakeClient([_resp({
        "op": "ambiguous", "question": "Which one?", "candidates": ["haylee.q01", "haylee.q02"],
    })])

    result = commands.say(tmp_path, project, "drop the boring one", _SUBJECTS_SEGMENTS, client=client)

    assert result.status == "ambiguous"
    assert result.candidates == ["haylee.q01", "haylee.q02"]
    # No pending.json is written for a response that resolved nothing.
    assert edits.load_pending(tmp_path) is None


def test_say_first_call_ever_writes_no_pending_file_until_a_real_mutation(tmp_path):
    # No edit list exists yet at all -- a query must still not create one.
    project = {"current_edit_version": None}
    client = FakeClient([_resp({"op": "query", "answer": "Nothing queued yet."})])
    commands.say(tmp_path, project, "what's queued?", {}, client=client)
    assert edits.load_pending(tmp_path) is None
    assert edits.list_versions(tmp_path) == []


def test_say_rejects_unknown_id_without_calling_apply(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01"])
    project = {"current_edit_version": "v001"}
    client = FakeClient([_resp({"op": "remove", "target": "haylee.q99"})])

    result = commands.say(tmp_path, project, "drop haylee 99", _SUBJECTS_SEGMENTS, client=client)

    assert result.status == "error"
    assert "haylee.q99" in result.message
    # A rejected command leaves no trace -- no pending.json is written at all.
    assert edits.load_pending(tmp_path) is None


def test_say_apply_failure_reports_error_not_exception(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01"])
    project = {"current_edit_version": "v001"}
    # A real, known id -- but not currently in the sequence, so remove fails.
    client = FakeClient([_resp({"op": "remove", "target": "haylee.q02"})])

    result = commands.say(tmp_path, project, "drop haylee 2", _SUBJECTS_SEGMENTS, client=client)

    assert result.status == "error"
    assert "haylee.q02" in result.message


def test_say_starts_pending_from_current_version_when_nothing_queued_yet(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02"])
    project = {"current_edit_version": "v001"}
    client = FakeClient([_resp({"op": "swap", "target": "haylee.q01", "anchor": "haylee.q02"})])

    result = commands.say(tmp_path, project, "swap them", _SUBJECTS_SEGMENTS, client=client)
    assert result.sequence == ["haylee.q02", "haylee.q01"]


def test_say_error_op_from_model_is_reported_directly(tmp_path):
    project = {"current_edit_version": None}
    client = FakeClient([_resp({"op": "error", "message": "No subject named diego."})])
    result = commands.say(tmp_path, project, "drop diego's answer", _SUBJECTS_SEGMENTS, client=client)
    assert result.status == "error"
    assert "diego" in result.message
