import json
from dataclasses import dataclass, field

from theodore.analyze.claude_client import CostTracker
from theodore.analyze.question_guide import apply_canonical_ids, match_to_guide


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


_GUIDE = [
    {"id": "q01", "canonical": "Tell me about the day you enlisted.", "aliases": ["enlistment story"]},
    {"id": "q02", "canonical": "What did your family say?", "aliases": []},
]

_SEGMENTS = [
    {"id": "haylee.q01", "question_text": "Walk me through enlisting.", "answer_summary": "Enlisted at 19."},
    {"id": "haylee.q02", "question_text": "Any hobbies?", "answer_summary": "Likes hiking."},
]


def test_match_to_guide_returns_matches():
    response = json.dumps({"matches": [{"segment_id": "haylee.q01", "guide_id": "q01"}]})
    client = FakeClient([response])
    matches = match_to_guide(_SEGMENTS, _GUIDE, cost_tracker=CostTracker(), client=client)
    assert matches == {"haylee.q01": "q01"}


def test_match_to_guide_empty_guide_makes_no_api_call():
    client = FakeClient([])
    assert match_to_guide(_SEGMENTS, [], client=client) == {}
    assert client.messages.calls == []


def test_match_to_guide_empty_segments_makes_no_api_call():
    client = FakeClient([])
    assert match_to_guide([], _GUIDE, client=client) == {}
    assert client.messages.calls == []


def test_match_to_guide_ignores_unknown_ids():
    response = json.dumps({"matches": [
        {"segment_id": "haylee.q01", "guide_id": "q01"},
        {"segment_id": "haylee.q99", "guide_id": "q01"},  # unknown segment id
        {"segment_id": "haylee.q02", "guide_id": "q99"},  # unknown guide id
    ]})
    client = FakeClient([response])
    matches = match_to_guide(_SEGMENTS, _GUIDE, client=client)
    assert matches == {"haylee.q01": "q01"}


def test_match_to_guide_first_match_wins_on_duplicate_guide_id():
    response = json.dumps({"matches": [
        {"segment_id": "haylee.q01", "guide_id": "q01"},
        {"segment_id": "haylee.q02", "guide_id": "q01"},  # q01 already claimed
    ]})
    client = FakeClient([response])
    matches = match_to_guide(_SEGMENTS, _GUIDE, client=client)
    assert matches == {"haylee.q01": "q01"}


def test_apply_canonical_ids_sets_none_for_unmatched():
    segments = [{"id": "haylee.q01"}, {"id": "haylee.q02"}]
    apply_canonical_ids(segments, {"haylee.q01": "q01"})
    assert segments[0]["canonical_question_id"] == "q01"
    assert segments[1]["canonical_question_id"] is None


def test_apply_canonical_ids_clears_stale_value_from_prior_run():
    segments = [{"id": "haylee.q01", "canonical_question_id": "q07"}]  # stale from an old guide
    apply_canonical_ids(segments, {})
    assert segments[0]["canonical_question_id"] is None
