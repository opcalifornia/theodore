"""Interview analysis: merging two mics into one conversation and keeping Claude's quotes honest."""
import numpy as np

from theodore.edit import analysis


def _w(word, a, b):
    return {"word": word, "start": a, "end": b}


def test_each_word_stays_with_the_person_actually_speaking_and_bleed_is_dropped():
    hop = analysis.HOP
    a = np.zeros(int(20 / hop), bool); b = np.zeros(int(20 / hop), bool)
    a[int(0 / hop):int(5 / hop)] = True          # Fred talks 0-5
    b[int(6 / hop):int(10 / hop)] = True         # the other man talks 6-10
    words = {"Fred": [_w("freedom", 1, 1.5), _w("now", 7, 7.4)],      # 'now' is bleed from the other man's voice
             "Other": [_w("freedom", 1.02, 1.5), _w("justice", 7, 7.5)]}   # 'freedom' at 1 s is bleed from Fred
    merged = analysis.merge_speakers(words, {"Fred": a, "Other": b})
    assert [(w["speaker"], w["word"]) for w in merged] == [("Fred", "freedom"), ("Other", "justice")]


def test_words_with_nobody_clearly_speaking_are_kept_once():
    hop = analysis.HOP
    quiet = np.zeros(int(10 / hop), bool)
    merged = analysis.merge_speakers({"Fred": [_w("hello", 2, 2.4)], "Other": [_w("hello", 2.05, 2.4)]},
                                     {"Fred": quiet, "Other": quiet})
    assert len(merged) == 1


def test_turns_split_on_speaker_change_and_long_pauses():
    ws = [{"word": "a", "start": 0, "end": 1, "speaker": "F"}, {"word": "b", "start": 1.2, "end": 2, "speaker": "F"},
          {"word": "c", "start": 5, "end": 6, "speaker": "F"}, {"word": "d", "start": 6.1, "end": 7, "speaker": "O"}]
    t = analysis.turns(ws)
    assert [(x["speaker"], x["text"]) for x in t] == [("F", "a b"), ("F", "c"), ("O", "d")]
    assert [x["id"] for x in t] == [1, 2, 3]


def test_analyze_adds_timestamps_and_flags_quotes_that_are_not_verbatim(monkeypatch):
    ts = [{"id": 1, "speaker": "Fred", "start": 65.0, "end": 70.0, "text": "We served breakfast to the children"},
          {"id": 2, "speaker": "Other", "start": 80.0, "end": 90.0, "text": "My brother believed in service"}]
    seen = {}

    def fake(client, *, user, **kw):
        seen["user"] = user
        return {"best_quotes": [{"turn": 1, "quote": "We served breakfast to the children"},
                                {"turn": 2, "quote": "My brother changed the world"}],
                "social_clips": [{"title": "t", "start_turn": 1, "end_turn": 2}]}

    monkeypatch.setattr(analysis, "call_json", fake)
    out = analysis.analyze(ts, goal="Breakfast program anniversary event", client=object())
    assert "Breakfast program anniversary" in seen["user"] and "[2] 1:20 Other:" in seen["user"]
    assert out["best_quotes"][0]["verbatim"] is True and out["best_quotes"][1]["verbatim"] is False
    assert out["best_quotes"][0]["at"] == "1:05"
    assert out["social_clips"][0]["start_seconds"] == 65.0 and out["social_clips"][0]["end_seconds"] == 90.0
