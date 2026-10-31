import json

import pytest

from theodore.study import love, transition_style as ts


def _analysis():
    # 6 shots: face, face, broll, broll, face, card. Boundaries 1..5.
    shots = [{"index": i, "start_time": float(i * 4), "duration": 4.0} for i in range(6)]
    labels = ["talking_head", "talking_head", "broll_over_speech", "broll_over_speech", "talking_head", "card"]
    tr = [  # 24 fps. dissolve 12f at the face->face cut (1), dip at card (5); the rest are hard cuts.
        {"kind": "fade_in", "time": 0.0, "seconds": 0.5, "frames": 12},
        {"kind": "dissolve", "time": 4.0, "seconds": 0.5, "frames": 12},
        {"kind": "cut", "time": 8.0, "seconds": 0.0, "frames": 0},
        {"kind": "cut", "time": 12.0, "seconds": 0.0, "frames": 0},
        {"kind": "cut", "time": 16.0, "seconds": 0.0, "frames": 0},
        {"kind": "dip_to_black", "time": 20.0, "seconds": 0.75, "frames": 18},
        {"kind": "fade_out", "time": 22.0, "seconds": 1.0, "frames": 24},
    ]
    return {"media": {"duration": 24.0, "fps": 24.0}, "shots": shots, "transitions": tr,
            "layers": {"shots": [{"index": i, "label": l} for i, l in enumerate(labels)]}}


def test_context_of():
    assert ts.context_of("talking_head", "talent_silent") == "face_to_face"
    assert ts.context_of("talking_head", "broll_clean") == "face_broll"
    assert ts.context_of("broll_clean", "broll_over_speech") == "broll_to_broll"
    assert ts.context_of("broll_clean", "card") == "card"


def test_measure_rates_per_context_and_head_tail():
    m = ts.measure(_analysis())
    c = m["contexts"]
    assert c["face_to_face"] == {"cuts": 1, "with_transition_pct": 100.0, "kinds": {"dissolve": 1}, "typical_frames": 12}
    assert c["face_broll"]["with_transition_pct"] == 0.0 and c["broll_to_broll"]["with_transition_pct"] == 0.0
    assert c["card"]["kinds"] == {"dip_to_black": 1}
    assert m["head_fade"] == {"frames": 12} and m["tail_fade"] == {"frames": 24}


def test_plan_hits_the_reference_rate_evenly_and_deterministically():
    style = {"fps": 24.0, "contexts": {"face_to_face": {"cuts": 10, "with_transition_pct": 40.0,
                                                          "kinds": {"dissolve": 4}, "typical_frames": 12}},
             "head_fade": {"frames": 12}, "tail_fade": None}
    p = ts.plan(style, 10)
    assert sorted(p["cuts"]) == [3, 5, 8, 10] and p["cuts"][3] == ("dissolve", 0.5)
    assert ts.plan(style, 10) == p
    assert p["head"] == ("dissolve", 0.5) and p["tail"] is None


def test_unbuildable_kinds_are_reported_and_hard_cut_contexts_add_nothing():
    stinger = {"fps": 24.0, "contexts": {"face_to_face": {"cuts": 4, "with_transition_pct": 100.0,
                                                            "kinds": {"stinger": 4}, "typical_frames": 10}}}
    p = ts.plan(stinger, 3)
    assert len(p["cuts"]) == 3 and any("stinger" in n for n in p["notes"])
    hard = {"fps": 24.0, "contexts": {"face_to_face": {"cuts": 9, "with_transition_pct": 0.0,
                                                         "kinds": {}, "typical_frames": 0}}}
    q = ts.plan(hard, 5)
    assert q["cuts"] == {} and any("hard cuts" in n for n in q["notes"])


def test_choose_context_prefers_faces_then_broll():
    style = {"contexts": {"face_to_face": {"cuts": 1}, "broll_to_broll": {"cuts": 8}}}
    assert ts.choose_context(style) == "broll_to_broll"
    assert ts.choose_context(style, "face_to_face") == "face_to_face"


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.seen = None

        class M:
            def create(_s, **kw):
                self.seen = kw
                from types import SimpleNamespace as N
                return N(content=[N(type="text", text=json.dumps(payload))],
                         usage=N(input_tokens=1, output_tokens=1), stop_reason="end_turn")
        self.messages = M()


def test_timestamps_and_moment_lookup():
    assert love.timestamps("love the dissolve at 0:42 and 1:05") == [42, 65]
    a = _analysis()
    a["transcript_words"] = [{"word": "hello", "start": 5.0, "end": 5.4}]
    m = love._moment(a, 5.0)
    assert m["shot"]["index"] == 1 and m["said"] == "hello" and m["label"] == "talking_head"
    assert any(t["kind"] == "dissolve" for t in m["transitions_near"])


def test_love_notes_are_saved_merged_and_fed_to_builders(tmp_path, monkeypatch):
    from theodore import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    d = tmp_path / "references" / "spot"
    d.mkdir(parents=True)
    (d / "blueprint.json").write_text(json.dumps({"title": "Spot"}))
    (d / "analysis.json").write_text(json.dumps(_analysis()))
    (d / "report.md").write_text("# Spot\n\n## Other\nx\n")
    first = {"what_they_love": [{"trait": "late cutaways", "their_words": "B-roll comes in late", "weight": 5,
                                 "evidence": "median lead-in 3.5s",
                                 "replicate": [{"tool": "place_broll", "settings": "reference=spot",
                                                "how": "Hold the face ~3s before each cutaway."}]},
                                {"trait": "dissolves", "their_words": "the dissolves", "weight": 3,
                                 "evidence": "12f at 0:04", "replicate": [{"tool": "add_transitions",
                                 "settings": "reference=spot", "how": "dissolve between answers"}]}],
             "do_not_copy": ["the end card"], "unclear": ["Which dissolve?"]}
    client = _Client(first)
    result = love.add_note("spot", "I love how B-roll comes in late, and the dissolves at 0:04", client)
    sent = json.loads(client.seen["messages"][0]["content"].split("\n\n")[0])
    assert sent["moments_they_named"][0]["at"] == 4 and "late" in sent["editor_words"]
    assert [t["trait"] for t in result["traits"]] == ["late cutaways", "dissolves"]       # by weight
    assert json.loads((d / "blueprint.json").read_text())["editor_love"]["do_not_copy"] == ["the end card"]
    assert "What the editor loves" in (d / "report.md").read_text()
    # a later note refines: same trait replaced, weight re-sorts
    love.save(d, "dissolves matter more", {"what_they_love": [{"trait": "Dissolves", "their_words": "most",
                                                             "weight": 5, "replicate": [{"tool": "add_transitions",
                                                             "how": "dissolve between answers"}]}]})
    merged = json.loads((d / "blueprint.json").read_text())["editor_love"]
    assert len(merged["traits"]) == 2 and merged["traits"][0]["weight"] == 5
    assert (d / "report.md").read_text().count("## What the editor loves") == 1
    bp = json.loads((d / "blueprint.json").read_text())
    assert "late" in love.rules_text(bp, ("place_broll",)).lower()
    assert "dissolves" not in love.rules_text(bp, ("place_broll",)).lower()   # add_transitions-only traits stay out
    assert "dissolves" in love.rules_text(bp, ("add_transitions",)).lower()


def test_cuts_under_broll_are_left_alone():
    from fractions import Fraction
    from theodore.resolve import transitions

    class It:
        def __init__(self, a, b):
            self.a, self.b = a, b

        def GetStart(self):
            return self.a

        def GetEnd(self):
            return self.b

    class Tl:
        def GetStartFrame(self):
            return 86400

        def GetTrackCount(self, kind):
            return 2

        def GetItemListInTrack(self, kind, idx):
            return [It(86400 + 24 * 9, 86400 + 24 * 14)] if idx == 2 else []

    cuts = [(1, Fraction(5), None, None), (2, Fraction(10), None, None), (3, Fraction(20), None, None)]
    assert transitions._covered_cuts(Tl(), cuts, 24.0) == {2}
