import json
from dataclasses import dataclass, field

import pytest

from theodore.analyze.claude_client import CostTracker
from theodore.assembly import ordering


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


_ANALYSIS = {
    "segments": [
        {"id": "s001", "answer_start_utterance": "u002", "question_text": "Q1", "answer_summary": "A1"},
        {"id": "s002", "answer_start_utterance": "u005", "question_text": "Q2", "answer_summary": "A2"},
        {"id": "s003", "answer_start_utterance": "u008", "question_text": None, "answer_summary": "A3 (volunteered)"},
    ],
    "selects": [
        {"segment_id": "s001", "strength": 0.4},
        {"segment_id": "s002", "strength": 0.9},
        # s003 intentionally has no selects entry -- unscored.
    ],
    "themes": [
        {"id": "t01", "label": "Career", "description": "..."},
        {"id": "t02", "label": "Family", "description": "..."},
    ],
    "theme_assignments": {
        "s001": ["t02"],  # Family, strength 0.4
        "s002": ["t01"],  # Career, strength 0.9
        # s003 unassigned
    },
}


def test_order_chronological_sorts_by_utterance_index():
    # Deliberately shuffle segment list order to prove sorting, not identity.
    analysis = {**_ANALYSIS, "segments": list(reversed(_ANALYSIS["segments"]))}
    assert ordering.order_chronological(analysis) == ["s001", "s002", "s003"]


def test_order_by_strength_descending_unscored_last():
    assert ordering.order_by_strength(_ANALYSIS) == ["s002", "s001", "s003"]


def test_order_thematic_groups_by_primary_theme_strongest_theme_first():
    # Career (mean strength 0.9) should come before Family (mean strength 0.4);
    # the unassigned s003 lands in the trailing "other" group.
    assert ordering.order_thematic(_ANALYSIS) == ["s002", "s001", "s003"]


def test_order_thematic_multiple_segments_per_theme_sorted_by_strength():
    analysis = {
        "segments": [
            {"id": "s001", "answer_start_utterance": "u001"},
            {"id": "s002", "answer_start_utterance": "u002"},
            {"id": "s003", "answer_start_utterance": "u003"},
        ],
        "selects": [
            {"segment_id": "s001", "strength": 0.3},
            {"segment_id": "s002", "strength": 0.8},
            {"segment_id": "s003", "strength": 0.5},
        ],
        "themes": [{"id": "t01", "label": "Career", "description": "..."}],
        "theme_assignments": {"s001": ["t01"], "s002": ["t01"], "s003": ["t01"]},
    }
    assert ordering.order_thematic(analysis) == ["s002", "s003", "s001"]


def test_run_narrative_ordering_returns_order_and_rationale():
    response = json.dumps({"order": ["s003", "s001", "s002"], "rationale": "Opens with the volunteered story."})
    client = FakeClient([response])
    order, rationale = ordering.run_narrative_ordering(_ANALYSIS, cost_tracker=CostTracker(), client=client)
    assert order == ["s003", "s001", "s002"]
    assert rationale == "Opens with the volunteered story."


def test_run_narrative_ordering_appends_segments_claude_dropped():
    # Claude's response omits s002 -- it must still show up in the final order.
    response = json.dumps({"order": ["s003", "s001"], "rationale": "..."})
    client = FakeClient([response])
    order, _ = ordering.run_narrative_ordering(_ANALYSIS, cost_tracker=CostTracker(), client=client)
    assert set(order) == {"s001", "s002", "s003"}
    assert order[-1] == "s002"  # appended chronologically after the given order


def test_run_narrative_ordering_ignores_unknown_ids():
    response = json.dumps({"order": ["s001", "s999", "s002", "s003"], "rationale": "..."})
    client = FakeClient([response])
    order, _ = ordering.run_narrative_ordering(_ANALYSIS, cost_tracker=CostTracker(), client=client)
    assert order == ["s001", "s002", "s003"]


def test_run_narrative_ordering_empty_segments_makes_no_api_call():
    client = FakeClient([])
    order, rationale = ordering.run_narrative_ordering({"segments": []}, cost_tracker=CostTracker(), client=client)
    assert order == []
    assert client.messages.calls == []


def test_compute_order_dispatches_by_mode():
    assert ordering.compute_order(_ANALYSIS, "chronological") == (["s001", "s002", "s003"], None)
    assert ordering.compute_order(_ANALYSIS, "strength") == (["s002", "s001", "s003"], None)


def test_compute_order_unknown_mode_raises():
    with pytest.raises(ValueError):
        ordering.compute_order(_ANALYSIS, "bogus")
