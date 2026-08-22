from theodore import edits
from theodore.analyze.learning import summarize

_SEGMENTS = [
    {"id": "haylee.q01", "question_text": "Q1"},
    {"id": "haylee.q02", "question_text": "Q2"},
    {"id": "haylee.q03", "question_text": "Q3"},
]

_SELECTS = [
    {"segment_id": "haylee.q01", "strength": 0.9},   # strong, kept
    {"segment_id": "haylee.q02", "strength": 0.2},   # weak, kept anyway (surprise)
    {"segment_id": "haylee.q03", "strength": 0.85},  # strong, dropped anyway (surprise)
]


def test_summarize_no_versions_yet(tmp_path):
    result = summarize(tmp_path, "haylee", _SEGMENTS, _SELECTS)
    assert result == {
        "versions_checked": 0,
        "kept_count": 0,
        "dropped_count": 0,
        "avg_strength_kept": None,
        "avg_strength_dropped": None,
        "dropped_despite_high_strength": [],
        "kept_despite_low_strength": [],
    }


def test_summarize_flags_dropped_despite_high_strength_and_kept_despite_low(tmp_path):
    # v001 includes everything, then a `remove` command derives v002 that
    # drops the strong q03 -- an active editorial override, and the latest
    # (current) state of the world.
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02", "haylee.q03"])
    v001 = edits.load(tmp_path, "v001")
    entries = [edits.EditEntry(sid) for sid in ["haylee.q01", "haylee.q02"]]
    edits.derive(tmp_path, v001, entries, ["remove haylee.q03"])

    result = summarize(tmp_path, "haylee", _SEGMENTS, _SELECTS)

    assert result["versions_checked"] == 2
    assert result["kept_count"] == 2  # q01, q02 -- in the LATEST version
    assert result["dropped_count"] == 1  # q03 -- removed in v002, even though v001 had it
    assert result["dropped_despite_high_strength"] == ["haylee.q03"]
    assert result["kept_despite_low_strength"] == ["haylee.q02"]


def test_summarize_computes_average_strengths(tmp_path):
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02"])
    result = summarize(tmp_path, "haylee", _SEGMENTS, _SELECTS)
    assert result["avg_strength_kept"] == (0.9 + 0.2) / 2
    assert result["avg_strength_dropped"] == 0.85


def test_summarize_ignores_versions_belonging_to_another_subject(tmp_path):
    edits.create_initial(tmp_path, ["marcus.q01", "marcus.q02"])
    result = summarize(tmp_path, "haylee", _SEGMENTS, _SELECTS)
    assert result["versions_checked"] == 0
    assert result["kept_count"] == 0


def test_summarize_no_surprises_when_choices_track_strength(tmp_path):
    segments = [{"id": "haylee.q01"}, {"id": "haylee.q02"}]
    selects = [{"segment_id": "haylee.q01", "strength": 0.9}, {"segment_id": "haylee.q02", "strength": 0.8}]
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02"])
    result = summarize(tmp_path, "haylee", segments, selects)
    assert result["dropped_despite_high_strength"] == []
    assert result["kept_despite_low_strength"] == []


def test_summarize_treats_unscored_segment_as_zero_strength(tmp_path):
    segments = [{"id": "haylee.q01"}, {"id": "haylee.q02"}]
    selects = [{"segment_id": "haylee.q01", "strength": 0.9}]  # q02 unscored
    edits.create_initial(tmp_path, ["haylee.q01", "haylee.q02"])
    result = summarize(tmp_path, "haylee", segments, selects)
    # q02 was never scored at all -- it must not be reported as a "kept
    # despite low strength" surprise, since there's no real prediction it
    # diverged from.
    assert result["kept_despite_low_strength"] == []
    assert result["avg_strength_kept"] == 0.9  # q02 excluded from the average too
