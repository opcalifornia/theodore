import json
from dataclasses import dataclass, field

import pytest

from theodore.analyze.claude_client import CostTracker
from theodore.analyze.redundancy import load_redundancy, run_redundancy, save_redundancy


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
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Message(content=[_Block(text=item)])


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _json_response(obj):
    return json.dumps(obj)


_SEGMENTS = [
    {"id": "s001", "question_text": "What was the hardest part?", "answer_summary": "Moving away from home."},
    {"id": "s002", "question_text": "Tell me about leaving.", "answer_summary": "Moving away from home again, same story."},
    {"id": "s003", "question_text": "What did you eat?", "answer_summary": "Mostly rice and beans."},
]


def test_run_redundancy_empty_segments_makes_no_api_call():
    client = FakeClient([])
    result = run_redundancy([], {}, [], model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result == {"groups": []}
    assert client.messages.calls == []


def test_run_redundancy_single_segment_makes_no_api_call():
    client = FakeClient([])
    result = run_redundancy(_SEGMENTS[:1], {}, [], model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result == {"groups": []}
    assert client.messages.calls == []


def test_run_redundancy_pools_by_shared_theme_and_recommends_by_strength():
    theme_assignments = {"s001": ["t01"], "s002": ["t01"], "s003": ["t02"]}
    selects = [
        {"segment_id": "s001", "strength": 0.9},
        {"segment_id": "s002", "strength": 0.4},
    ]
    client = FakeClient([_json_response({"groups": [
        {"segment_ids": ["s001", "s002"], "reason": "Both tell the story of moving away from home."},
    ]})])

    result = run_redundancy(
        _SEGMENTS, theme_assignments, selects,
        model_tier="standard", cost_tracker=CostTracker(), client=client,
    )

    assert len(client.messages.calls) == 1  # only s003's theme pool is a singleton, so only one pool is sent
    assert result["groups"] == [{
        "id": "r01",
        "segment_ids": ["s001", "s002"],
        "recommended_id": "s001",
        "reason": "Both tell the story of moving away from home.",
    }]


def test_run_redundancy_merges_groups_reported_across_separate_pools():
    # s002 shares t01 with s001 and t02 with s003 -- two separate candidate
    # pools, two separate Claude calls, each reporting a pairwise overlap
    # that should be merged into one three-way group.
    theme_assignments = {"s001": ["t01"], "s002": ["t01", "t02"], "s003": ["t02"]}
    selects = [
        {"segment_id": "s001", "strength": 0.3},
        {"segment_id": "s002", "strength": 0.3},
        {"segment_id": "s003", "strength": 0.8},
    ]
    client = FakeClient([
        _json_response({"groups": [{"segment_ids": ["s001", "s002"], "reason": "Same anecdote"}]}),
        _json_response({"groups": [{"segment_ids": ["s002", "s003"], "reason": "Repeats it again later"}]}),
    ])

    result = run_redundancy(
        _SEGMENTS, theme_assignments, selects,
        model_tier="standard", cost_tracker=CostTracker(), client=client,
    )

    assert len(client.messages.calls) == 2
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["segment_ids"] == ["s001", "s002", "s003"]
    assert group["recommended_id"] == "s003"
    assert "Same anecdote" in group["reason"] and "Repeats it again later" in group["reason"]


def test_run_redundancy_falls_back_to_full_pass_without_theme_data():
    client = FakeClient([_json_response({"groups": []})])
    result = run_redundancy(
        _SEGMENTS, {}, [], model_tier="standard", cost_tracker=CostTracker(), client=client,
    )
    assert len(client.messages.calls) == 1
    user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "s001" in user_msg and "s002" in user_msg and "s003" in user_msg
    assert result == {"groups": []}


def test_run_redundancy_ignores_claude_reported_groups_with_fewer_than_two_ids():
    theme_assignments = {"s001": ["t01"], "s002": ["t01"]}
    client = FakeClient([_json_response({"groups": [{"segment_ids": ["s001"], "reason": "not actually a group"}]})])
    result = run_redundancy(
        _SEGMENTS[:2], theme_assignments, [], model_tier="standard", cost_tracker=CostTracker(), client=client,
    )
    assert result == {"groups": []}


def test_save_and_load_redundancy_round_trip(tmp_path):
    result = {"groups": [{"id": "r01", "segment_ids": ["s001", "s002"], "recommended_id": "s001", "reason": "x"}]}
    save_redundancy(result, tmp_path)
    assert load_redundancy(tmp_path) == result


def test_load_redundancy_missing_file_returns_none(tmp_path):
    assert load_redundancy(tmp_path) is None
