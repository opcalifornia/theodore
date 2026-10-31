from theodore.analyze.coverage import find_gaps, find_rejects


_SEGMENTS = [
    {"id": "s001", "question_text": "What was the hardest part?", "canonical_question_id": "g01"},
    {"id": "s002", "question_text": "What did you eat?", "canonical_question_id": None},
    {"id": "s003", "question_text": "Tell me about your childhood.", "canonical_question_id": "g02"},
]

_SELECTS = [
    {"segment_id": "s001", "strength": 0.9, "issues": [], "rationale": "Strong."},
    {"segment_id": "s002", "strength": 0.2, "issues": ["rambles"], "rationale": "Weak, no clear point."},
    {"segment_id": "s003", "strength": 0.35, "issues": [], "rationale": "Thin answer."},
]


def test_find_rejects_filters_by_threshold_and_sorts_weakest_first():
    rejects = find_rejects(_SEGMENTS, _SELECTS, threshold=0.4)
    assert [r["segment_id"] for r in rejects] == ["s002", "s003"]
    assert rejects[0]["strength"] == 0.2
    assert rejects[0]["issues"] == ["rambles"]
    assert rejects[0]["question_text"] == "What did you eat?"


def test_find_rejects_excludes_segments_at_or_above_threshold():
    rejects = find_rejects(_SEGMENTS, _SELECTS, threshold=0.2)
    assert rejects == []  # 0.2 is not < 0.2


def test_find_rejects_ignores_selects_missing_a_strength():
    selects = [{"segment_id": "s001"}]
    assert find_rejects(_SEGMENTS, selects, threshold=0.9) == []


def test_find_rejects_default_threshold():
    rejects = find_rejects(_SEGMENTS, _SELECTS)
    assert [r["segment_id"] for r in rejects] == ["s002", "s003"]


def test_find_gaps_returns_unanswered_guide_questions():
    guide = [
        {"id": "g01", "canonical": "What was the hardest part?"},
        {"id": "g02", "canonical": "Tell me about your childhood."},
        {"id": "g03", "canonical": "What's next for you?"},
    ]
    gaps = find_gaps(_SEGMENTS, guide)
    assert gaps == [{"id": "g03", "canonical": "What's next for you?"}]


def test_find_gaps_empty_when_everything_answered():
    guide = [
        {"id": "g01", "canonical": "What was the hardest part?"},
        {"id": "g02", "canonical": "Tell me about your childhood."},
    ]
    assert find_gaps(_SEGMENTS, guide) == []


def test_find_gaps_returns_empty_list_with_no_guide():
    assert find_gaps(_SEGMENTS, []) == []


def test_find_gaps_ignores_segments_with_no_canonical_id():
    guide = [{"id": "g99", "canonical": "Something nobody was asked."}]
    gaps = find_gaps(_SEGMENTS, guide)
    assert gaps == guide
