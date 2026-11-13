import json
from dataclasses import dataclass, field

from theodore.analyze.claude_client import CostTracker
from theodore.analyze.search import run_search


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
    {"id": "haylee.q01", "question_text": "What was the hardest part?", "answer_summary": "Losing her mother at 19."},
    {"id": "haylee.q02", "question_text": "What did you eat?", "answer_summary": "Mostly rice and beans."},
]


def test_run_search_empty_segments_makes_no_api_call():
    client = FakeClient([])
    result = run_search([], "losing a parent", model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result == {"matches": []}
    assert client.messages.calls == []


def test_run_search_blank_query_makes_no_api_call():
    client = FakeClient([])
    result = run_search(_SEGMENTS, "   ", model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result == {"matches": []}
    assert client.messages.calls == []


def test_run_search_returns_ranked_matches():
    client = FakeClient([_json_response({"matches": [
        {"segment_id": "haylee.q01", "relevance": 0.9, "reason": "Directly about losing her mother."},
    ]})])
    result = run_search(_SEGMENTS, "losing a parent", model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result["matches"] == [{"segment_id": "haylee.q01", "relevance": 0.9, "reason": "Directly about losing her mother."}]
    user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "losing a parent" in user_msg
    assert "haylee.q01" in user_msg and "haylee.q02" in user_msg


def test_run_search_includes_best_line_when_selects_given():
    client = FakeClient([_json_response({"matches": []})])
    selects = [{"segment_id": "haylee.q01", "strength": 0.8, "best_line": "I lost her the summer I turned 19."}]
    run_search(_SEGMENTS, "losing a parent", model_tier="standard", cost_tracker=CostTracker(), selects=selects, client=client)
    user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "I lost her the summer I turned 19." in user_msg


def test_run_search_dedupes_across_chunks_keeping_highest_relevance(monkeypatch):
    monkeypatch.setattr("theodore.config.MAX_UTTERANCES_PER_CHUNK", 1)
    monkeypatch.setattr("theodore.config.CHUNK_UTTERANCE_OVERLAP", 0)
    client = FakeClient([
        _json_response({"matches": [{"segment_id": "haylee.q01", "relevance": 0.4, "reason": "weak"}]}),
        _json_response({"matches": [{"segment_id": "haylee.q01", "relevance": 0.9, "reason": "strong"}]}),
    ])
    result = run_search(_SEGMENTS, "losing a parent", model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result["matches"] == [{"segment_id": "haylee.q01", "relevance": 0.9, "reason": "strong"}]


def test_run_search_sorts_by_relevance_descending():
    client = FakeClient([_json_response({"matches": [
        {"segment_id": "haylee.q02", "relevance": 0.5, "reason": "some overlap"},
        {"segment_id": "haylee.q01", "relevance": 0.9, "reason": "strong match"},
    ]})])
    result = run_search(_SEGMENTS, "anything", model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert [m["segment_id"] for m in result["matches"]] == ["haylee.q01", "haylee.q02"]
