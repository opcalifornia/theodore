import numpy as np

from theodore.study import layers, rhythm


def _w(text, a, b):
    return {"word": text.strip(".,").lower(), "punctuated_word": text, "start": a, "end": b}


# 0-10s: host talks (face 0-4, B-roll 4-7 over the line, face 7-10); 10-12 music only B-roll; 12 end card.
SHOTS = [
    {"index": 0, "start_time": 0.0, "duration": 4.0},
    {"index": 1, "start_time": 4.0, "duration": 3.0},
    {"index": 2, "start_time": 7.0, "duration": 3.0},
    {"index": 3, "start_time": 10.0, "duration": 2.0},
    {"index": 4, "start_time": 12.0, "duration": 1.0},
]
LOG = {0: {"role": "a_roll", "shot_size": "MCU"}, 1: {"role": "b_roll", "shot_size": "WS", "subject": "factory floor"},
       2: {"role": "a_roll", "shot_size": "MCU"}, 3: {"role": "establishing", "shot_size": "aerial", "subject": "city"},
       4: {"role": "end_card"}}
WORDS = [_w("We", 0.5, 0.7), _w("build", 0.7, 1.2), _w("things.", 1.2, 4.9), _w("Every", 5.0, 5.3),
         _w("day", 5.3, 5.8), _w("on", 5.8, 6.0), _w("the", 6.0, 6.2), _w("floor,", 6.2, 8.0),
         _w("it", 8.2, 8.5), _w("matters.", 8.5, 9.6)]


def _rows():
    spans = rhythm.speech_spans(WORDS)
    return spans, layers.label_shots(SHOTS, LOG, spans)


def test_labels_separate_talking_head_cutaway_clean_broll_and_card():
    spans, rows = _rows()
    assert [r["label"] for r in rows] == ["talking_head", "broll_over_speech", "talking_head", "broll_clean", "card"]


def test_cutaway_timing_against_the_line():
    spans, rows = _rows()
    (c,) = layers.cutaways(rows, WORDS, spans)
    assert c["at"] == 4.0 and c["dur"] == 3.0 and c["shots"] == 1
    assert c["after"] == "talking_head" and c["returns_before_line_ends"] is True
    assert c["lead_in"] == 3.5            # the line (one span 0.5-8.0 given the small gaps) began 3.5s earlier
    assert "Every day" in c["covers"] or "things." in c["covers"]
    assert c["pictures"] == ["factory floor"]


def test_summary_numbers():
    spans, rows = _rows()
    cuts = layers.cutaways(rows, WORDS, spans)
    sm = layers.summarize(rows, cuts, spans, 13.0)
    assert sm["cutaways"] == 1 and sm["first_cutaway_at"] == 4.0
    assert 0 < sm["dialogue_covered_by_broll_pct"] < 100
    assert sm["time_pct"]["card"] == round(100 * 1 / 13, 1)
    assert sm["sequence"].startswith("T4 B3 T3")


def test_music_bed_ducks_under_speech_and_rises_in_pauses():
    rate = rhythm.ENV_RATE
    n = int(30 * rate)
    rms = np.full(n, 0.2)
    spans = [(2.0, 8.0), (14.0, 20.0)]
    for s, e in spans:
        rms[int(s * rate):int(e * rate)] = 0.05     # music pulled down under the voice
    out = layers.score_vs_speech(rms, spans, 30.0, rate)
    assert out["level_under_speech_vs_gaps_db"] < -8 and out["music_rises_in_pauses"] is True
    assert out["audio_before_first_word_s"] == 2.0 and out["audio_after_last_word_s"] == 10.0


def test_broll_style_comes_from_the_references_measurements(tmp_path, monkeypatch):
    import json
    from theodore import config
    from theodore.edit import broll
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    spans, rows = _rows()
    cuts = layers.cutaways(rows, WORDS, spans)
    d = tmp_path / "references" / "nike-ad"
    d.mkdir(parents=True)
    (d / "blueprint.json").write_text(json.dumps({"dialogue_broll_system": {"picture_to_words": "literal"}}))
    (d / "analysis.json").write_text(json.dumps({"layers": {"summary": layers.summarize(rows, cuts, spans, 13.0),
                                                            "cutaways": cuts}}))
    style = broll.style_from_reference("nike-ad")
    assert style["min_seconds"] == 3.0 and style["max_seconds"] == 3.0 and style["coverage"] > 0
    assert "factory floor" in style["rules"] and "literal" in style["rules"]
    (d / "analysis.json").write_text(json.dumps({"layers": {"summary": {"cutaways": 0}}}))
    import pytest
    with pytest.raises(RuntimeError):
        broll.style_from_reference("nike-ad")
