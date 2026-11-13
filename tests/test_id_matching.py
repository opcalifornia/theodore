import json
from dataclasses import dataclass, field

import pytest

from theodore.analyze.claude_client import ClaudeCallError, CostTracker
from theodore.analyze.id_matching import assign_immutable_ids


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


def _seg(uid_start, uid_end, question="Q?", summary="A."):
    return {"answer_start_utterance": uid_start, "answer_end_utterance": uid_end,
            "question_text": question, "answer_summary": summary}


def test_exact_overlap_match_keeps_existing_id_no_claude_call():
    existing = [{"id": "haylee.q01", **_seg("u002", "u002")}]
    new = [_seg("u002", "u002")]
    client = FakeClient([])  # would raise IndexError if called

    result, next_num = assign_immutable_ids(new, existing, "haylee", next_number=2, client=client)

    assert result[0]["id"] == "haylee.q01"
    assert next_num == 2  # unchanged -- no new id minted
    assert client.messages.calls == []


def test_new_segment_gets_next_number_and_counter_advances():
    result, next_num = assign_immutable_ids(
        [_seg("u010", "u012")], existing_segments=[], subject_id="haylee",
        next_number=3, use_claude=False,
    )
    assert result[0]["id"] == "haylee.q03"
    assert next_num == 4


def test_multiple_new_segments_get_sequential_numbers():
    result, next_num = assign_immutable_ids(
        [_seg("u001", "u001"), _seg("u005", "u005")], existing_segments=[],
        subject_id="marcus", next_number=1, use_claude=False,
    )
    assert [s["id"] for s in result] == ["marcus.q01", "marcus.q02"]
    assert next_num == 3


def test_fuzzy_match_via_claude_for_boundary_shifted_segment():
    existing = [{"id": "haylee.q04", **_seg("u020", "u025", question="Tell me about the farm", summary="Grew up on a farm.")}]
    # Same question, boundaries shifted by one utterance on each side.
    new = [_seg("u019", "u026", question="Tell me about the farm", summary="Grew up on a farm, loved it.")]
    response = json.dumps({"matches": [{"new_index": 0, "existing_id": "haylee.q04"}]})
    client = FakeClient([response])

    result, next_num = assign_immutable_ids(new, existing, "haylee", next_number=5, client=client)

    assert result[0]["id"] == "haylee.q04"
    assert next_num == 5  # matched, not new
    assert len(client.messages.calls) == 1


def test_retired_ids_are_never_recycled():
    # existing_segments no longer contains q02 (it was deleted/merged away),
    # but next_number was already advanced past it by the caller -- the
    # counter, not the presence of existing segments, is authoritative.
    existing = [{"id": "haylee.q01", **_seg("u001", "u001")}]
    new = [_seg("u001", "u001"), _seg("u050", "u050")]  # second is genuinely new
    result, next_num = assign_immutable_ids(new, existing, "haylee", next_number=3, use_claude=False)

    assert result[0]["id"] == "haylee.q01"
    assert result[1]["id"] == "haylee.q03"  # not q02 -- that number stays retired
    assert next_num == 4


def test_claude_failure_falls_back_to_new_ids_instead_of_crashing():
    existing = [{"id": "haylee.q01", **_seg("u010", "u010", question="Old Q", summary="Old A")}]
    new = [_seg("u011", "u011", question="Totally different", summary="Different")]
    client = FakeClient([ClaudeCallError("boom")])
    result, next_num = assign_immutable_ids(new, existing, "haylee", next_number=2, client=client)

    assert result[0]["id"] == "haylee.q02"  # treated as new, run didn't crash
    assert next_num == 3


def test_use_claude_false_skips_fuzzy_matching_entirely():
    existing = [{"id": "haylee.q01", **_seg("u010", "u012")}]
    new = [_seg("u011", "u012")]  # overlaps but isn't an exact range match
    client = FakeClient([])  # would raise if called

    result, next_num = assign_immutable_ids(new, existing, "haylee", next_number=2, use_claude=False, client=client)

    assert result[0]["id"] == "haylee.q02"  # no fuzzy match attempted -- treated as new
    assert client.messages.calls == []
