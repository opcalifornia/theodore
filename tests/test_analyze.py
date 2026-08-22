import json
from dataclasses import dataclass, field

import anthropic
import pytest

from theodore.analyze.chunking import chunk_items
from theodore.analyze.claude_client import ClaudeCallError, CostTracker, call_json
from theodore.analyze.segmenter import run_segmenter
from theodore.analyze.selects import run_selects
from theodore.analyze.themes import run_themes


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
    """Stands in for client.messages -- returns queued responses in order,
    or raises the queued exception."""

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


def test_call_json_strips_markdown_fences():
    client = FakeClient([f"```json\n{_json_response({'ok': True})}\n```"])
    result = call_json(
        client, pass_name="test", model="claude-haiku-4-5-20251001",
        system="sys", user="usr", max_tokens=100, cost_tracker=CostTracker(),
    )
    assert result == {"ok": True}


def test_call_json_repairs_once_on_bad_json():
    client = FakeClient(["not json at all", _json_response({"fixed": True})])
    result = call_json(
        client, pass_name="test", model="claude-haiku-4-5-20251001",
        system="sys", user="usr", max_tokens=100, cost_tracker=CostTracker(),
    )
    assert result == {"fixed": True}
    assert len(client.messages.calls) == 2


def test_call_json_raises_after_failed_repair():
    client = FakeClient(["nope", "still nope"])
    with pytest.raises(ClaudeCallError):
        call_json(
            client, pass_name="test", model="claude-haiku-4-5-20251001",
            system="sys", user="usr", max_tokens=100, cost_tracker=CostTracker(),
        )


def test_call_json_tracks_cost():
    tracker = CostTracker()
    client = FakeClient([_json_response({"a": 1})])
    call_json(
        client, pass_name="mypass", model="claude-sonnet-5",
        system="sys", user="usr", max_tokens=100, cost_tracker=tracker,
    )
    assert len(tracker.calls) == 1
    assert tracker.calls[0]["pass"] == "mypass"
    assert tracker.total_cost_usd > 0


def test_call_json_retries_transient_api_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    class FakeRequest:
        pass

    err = anthropic.APIConnectionError(request=FakeRequest())
    client = FakeClient([err, _json_response({"ok": True})])
    result = call_json(
        client, pass_name="test", model="claude-haiku-4-5-20251001",
        system="sys", user="usr", max_tokens=100, cost_tracker=CostTracker(),
    )
    assert result == {"ok": True}


def test_chunk_items_no_split_when_under_limit():
    items = list(range(50))
    assert chunk_items(items) == [items]


def test_chunk_items_overlaps_when_over_limit(monkeypatch):
    monkeypatch.setattr("theodore.config.MAX_UTTERANCES_PER_CHUNK", 10)
    monkeypatch.setattr("theodore.config.CHUNK_UTTERANCE_OVERLAP", 3)
    items = list(range(25))
    chunks = chunk_items(items)
    assert chunks[0] == list(range(0, 10))
    assert chunks[1][0] == 7  # overlaps the last 3 of the previous chunk
    assert chunks[-1][-1] == 24


_TRANSCRIPT = {
    "speakers": {"0": "Marcus", "1": "Interviewer"},
    "utterances": [
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 2.0, "text": "What happened next?"},
        {"id": "u002", "speaker": "0", "start": 2.0, "end": 6.0, "text": "Well, I went home."},
    ],
}


def test_run_segmenter_dedupes_across_overlapping_chunks(monkeypatch):
    monkeypatch.setattr("theodore.config.MAX_UTTERANCES_PER_CHUNK", 10)
    seg = {
        "question_start_utterance": "u001", "answer_start_utterance": "u002",
        "answer_end_utterance": "u002", "question_text": "What happened next?",
        "question_label": "Going home", "answer_summary": "They went home.", "confidence": 0.9,
    }
    # Same segment reported twice (simulating two overlapping chunks) should dedupe to one.
    client = FakeClient([_json_response({"segments": [seg]})])
    result, next_num = run_segmenter(
        _TRANSCRIPT, subject_id="marcus", interviewer="1", model_tier="standard",
        cost_tracker=CostTracker(), client=client,
    )
    assert len(result["segments"]) == 1
    assert result["segments"][0]["id"] == "marcus.q01"
    assert next_num == 2


def test_run_selects_formats_segment_utterance_span():
    client = FakeClient([_json_response({"selects": [{"segment_id": "s001", "strength": 0.8}]})])
    segments = [{"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Q?"}]
    result = run_selects(segments, _TRANSCRIPT, model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result["selects"][0]["strength"] == 0.8
    user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "u002" in user_msg and "u001" not in user_msg


def test_run_selects_empty_segments_makes_no_api_call():
    client = FakeClient([])
    result = run_selects([], _TRANSCRIPT, model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result == {"selects": []}
    assert client.messages.calls == []


def test_run_themes_two_pass_vocabulary_then_tagging():
    segments = [{"id": "s001", "question_text": "Q?", "answer_summary": "A."}]
    vocab_response = _json_response({"themes": [{"id": "t01", "label": "Home", "description": "About home."}]})
    tag_response = _json_response({"assignments": [{"segment_id": "s001", "theme_ids": ["t01"]}]})
    client = FakeClient([vocab_response, tag_response])

    result = run_themes(segments, model_tier="standard", cost_tracker=CostTracker(), client=client)

    assert result["themes"] == [{"id": "t01", "label": "Home", "description": "About home."}]
    assert result["assignments"] == {"s001": ["t01"]}
    assert len(client.messages.calls) == 2


def test_run_themes_empty_segments_makes_no_api_call():
    client = FakeClient([])
    result = run_themes([], model_tier="standard", cost_tracker=CostTracker(), client=client)
    assert result == {"themes": [], "assignments": {}}
    assert client.messages.calls == []
