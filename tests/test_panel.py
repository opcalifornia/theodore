"""Panel multicam: audio sync offsets and the cutting rules (no Resolve)."""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from theodore.edit import panel


def test_offset_found_to_the_sample():
    rng = np.random.default_rng(1)
    room = rng.normal(0, 1, panel.SR * 20).astype(np.float32)
    ref = room[panel.SR * 3:]                      # this recorder started 3 s later
    other = room[panel.SR * 1:] * 0.3 + rng.normal(0, 0.05, len(room) - panel.SR).astype(np.float32)
    off, conf = panel.offset_seconds(ref, other)
    assert abs(off - (-2.0)) < 1 / panel.SR * 2 and conf > 0.3


def test_cut_rules_speaker_wide_and_min_shot():
    frames = int(30 / panel.HOP)
    a = np.zeros(frames, bool); b = np.zeros(frames, bool)
    a[int(4 / panel.HOP):int(14 / panel.HOP)] = True        # A talks 4-14 s
    b[int(14 / panel.HOP):int(14.6 / panel.HOP)] = True     # a 0.6 s interjection
    b[int(18 / panel.HOP):int(26 / panel.HOP)] = True       # B talks 18-26 s
    cams = [panel.Source("wide", "", "camera", {}, role="wide"),
            panel.Source("camA", "", "camera", {}, person="A", role="close"),
            panel.Source("camB", "", "camera", {}, person="B", role="close")]
    cuts = panel.plan_cut(cams, {"A": a, "B": b}, 30.0)
    assert cuts[0][2] == "wide" and cuts[-1][2] == "wide"
    a_time = sum(c[1] - c[0] for c in cuts if c[2] == "camA" and c[0] < 14)
    assert a_time >= 7 and any(c[2] == "camA" and c[0] <= 5 for c in cuts)
    assert any(c[2] == "camB" and c[0] <= 19 for c in cuts)
    assert all(c[1] - c[0] >= panel.MIN_SHOT - 1e-9 for c in cuts)


def _two_person_cams():
    return [panel.Source("wide", "", "camera", {}, role="wide"),
            panel.Source("camA", "", "camera", {}, person="A", role="close"),
            panel.Source("camB", "", "camera", {}, person="B", role="close")]


def test_cut_to_the_new_speaker_lands_just_before_they_speak():
    frames = int(30 / panel.HOP)
    a = np.zeros(frames, bool); b = np.zeros(frames, bool)
    a[int(4 / panel.HOP):int(10 / panel.HOP)] = True
    b[int(12 / panel.HOP):int(20 / panel.HOP)] = True
    cuts = panel.plan_cut(_two_person_cams(), {"A": a, "B": b}, 30.0)
    start_b = next(c[0] for c in cuts if c[2] == "camB")
    assert 12 - panel.LEAD - 1e-6 <= start_b < 12      # picture arrives before the voice


def test_a_long_answer_gets_a_reaction_shot_of_the_listener():
    frames = int(40 / panel.HOP)
    a = np.zeros(frames, bool)
    a[int(5 / panel.HOP):int(20 / panel.HOP)] = True   # A talks 15 s straight; B just listens
    b = np.zeros(frames, bool)
    cuts = panel.plan_cut(_two_person_cams(), {"A": a, "B": b}, 40.0)
    reactions = [c for c in cuts if c[2] == "camB"]
    assert reactions and "listening" in reactions[0][3]
    assert abs((reactions[0][1] - reactions[0][0]) - panel.REACTION_LEN) < 0.2


def test_speaker_map_attributes_a_line_to_who_talks_most(tmp_path, monkeypatch):
    from theodore import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    plan = panel.PanelPlan([], [], 24, 30.0, 100.0, cuts=[(102.0, 130.0, "wide", "x")],
                           speech={"A": [(102.0, 110.0)], "B": [(112.0, 125.0)]})
    panel.save_speakers(plan, "Episode 1")
    speech = panel.load_speakers("Episode 1")
    assert panel.speaker_at(speech, 1.0, 6.0) == "A" and panel.speaker_at(speech, 11.0, 20.0) == "B"
    assert panel.speaker_at(speech, 8.5, 9.5) is None      # silence between them
    assert panel.load_speakers("never built") is None


def test_speaker_follow_framing_centres_the_face_and_never_shows_an_edge():
    from theodore.edit import reframe
    cal = {"scale": 1.778, "px_per_pan": 3.16, "px_per_tilt": -1.0}
    left = reframe.framing([0.10, 0.2, 0.25, 0.45], 1920, 1080, 1080, 1920, cal, zoom=1.35)
    right = reframe.framing([0.75, 0.2, 0.9, 0.45], 1920, 1080, 1080, 1920, cal, zoom=1.35)
    assert left["Pan"] > 0 > right["Pan"]          # face on the left -> image moves right
    max_shift = (1920 * 1.778 * 1.35 - 1080) / 2
    assert abs(left["Pan"] * 3.16) <= max_shift + 1e-6


def test_lines_go_to_the_face_that_moves():
    from theodore.edit import reframe
    motion = np.ones((2, 120))
    motion[0, 0:60] = 5.0      # person 0 talks first
    motion[1, 60:120] = 5.0    # then person 1
    sents = [{"start": 0.5, "end": 4.5}, {"start": 5.5, "end": 9.5}]
    assert reframe.attribute_lines(sents, motion, fps=12) == [0, 1]


def test_cameras_without_sound_are_assumed_to_start_together_and_say_so(monkeypatch):
    silent = lambda n: panel.Source(n, n, "camera", {"has_audio": False})
    lav = panel.Source("lav", "lav", "lav", {"has_audio": True})
    cams = [silent("c1"), silent("c2")]
    notes = panel.sync(cams, [lav])
    assert [c.start for c in cams] == [0.0, 0.0] and lav.start == 0.0
    assert notes and "assumed to start together" in notes[0]


def test_a_very_long_hold_keeps_getting_reactions_that_rotate_between_listeners():
    frames = int(70 / panel.HOP)
    a = np.zeros(frames, bool)
    a[int(5 / panel.HOP):int(55 / panel.HOP)] = True       # A talks 50 s; B and C listen
    cams = _two_person_cams() + [panel.Source("camC", "", "camera", {}, person="C", role="close")]
    cuts = panel.plan_cut(cams, {"A": a, "B": np.zeros(frames, bool), "C": np.zeros(frames, bool)}, 70.0)
    reacts = [c for c in cuts if "listening" in c[3]]
    assert len(reacts) >= 4 and {c[2] for c in reacts} == {"camB", "camC"}
    held = [c[1] - c[0] for c in cuts if c[2] == "camA"]
    assert max(held) <= 12 and all(c[1] - c[0] >= panel.MIN_SHOT - 1e-9 for c in cuts)


def test_a_speaker_who_pauses_briefly_is_still_one_long_hold_that_gets_reactions():
    frames = int(70 / panel.HOP)
    a = np.zeros(frames, bool)
    for start in (5, 18, 31, 44):                         # A talks 13 s at a time, 1 s breaths between
        a[int(start / panel.HOP):int((start + 12) / panel.HOP)] = True
    cuts = panel.plan_cut(_two_person_cams(), {"A": a, "B": np.zeros(frames, bool)}, 70.0)
    held = [c[1] - c[0] for c in cuts if c[2] == "camA"]
    assert max(held) <= 12 and len([c for c in cuts if "listening" in c[3]]) >= 4


def test_a_long_wide_cuts_away_to_the_people_in_the_room_but_the_opening_wide_stays():
    frames = int(80 / panel.HOP)
    c = np.zeros(frames, bool)
    c[int(5 / panel.HOP):int(65 / panel.HOP)] = True       # C has no camera: the wide holds for a minute
    silent = np.zeros(frames, bool)
    cuts = panel.plan_cut(_two_person_cams(), {"A": silent, "B": silent, "C": c}, 80.0)
    assert cuts[0][3] == "opening wide"
    assert len([x for x in cuts if "cutaway" in x[3]]) >= 4
    assert cuts[0][2] == "wide" and cuts[0][1] >= 3 and cuts[-1][2] == "wide"
    assert max(x[1] - x[0] for x in cuts if x[2] == "wide") <= 14


def test_people_are_paired_with_mics_by_file_name_not_the_order_resolve_lists_them(monkeypatch):
    seen = {}

    class Clip:
        def __init__(self, path): self.path = path
        def GetClipProperty(self, k): return self.path
        def GetName(self): return self.path

    from theodore.edit import interview
    pool = {"Cams": [Clip("/m/cam1.mp4")], "Lavs": [Clip("/m/Headset-3.wav"), Clip("/m/Headset-0.wav"),
                                                   Clip("/m/Headset-1.wav")]}
    monkeypatch.setattr(interview, "find_clips", lambda h, names, bin_name: pool[bin_name])
    monkeypatch.setattr(panel, "build_plan", lambda cams, lavs, **kw: seen.update(lavs=lavs) or "plan")
    monkeypatch.setattr(panel, "import_to_resolve", lambda h, plan, name: {"timeline": name})
    monkeypatch.setattr(panel, "summary", lambda plan: {})
    panel.multicam_from_bins(None, camera_bin="Cams", lav_bin="Lavs", people=["A", "B", "D"], preview=False)
    assert [p.split("/")[-1] for p in seen["lavs"]] == ["Headset-0.wav", "Headset-1.wav", "Headset-3.wav"]


def _roam_world():
    frames = int(40 / panel.HOP)
    fred = np.zeros(frames, bool); other = np.zeros(frames, bool)
    fred[int(4 / panel.HOP):int(16 / panel.HOP)] = True        # Fred talks 4-16
    other[int(20 / panel.HOP):int(34 / panel.HOP)] = True      # the other man talks 20-34
    pan_on_fred = np.zeros(frames, bool); pan_on_other = np.zeros(frames, bool)
    pan_on_fred[int(0 / panel.HOP):int(12 / panel.HOP)] = True     # the pan is late leaving Fred...
    pan_on_other[int(22 / panel.HOP):int(40 / panel.HOP)] = True   # ...and slow arriving at the other man
    cams = [panel.Source("red", "", "camera", {}, role="wide"), panel.Source("alexa", "", "camera", {}, role="roam")]
    return cams, {"Fred": fred, "Other": other}, {"Fred": pan_on_fred, "Other": pan_on_other}


def test_the_pan_camera_is_used_only_while_it_is_actually_on_the_speaker():
    cams, masks, framing = _roam_world()
    cuts = panel.plan_cut(cams, masks, 40.0, framing)
    on_alexa = [(c[0], c[1]) for c in cuts if c[2] == "alexa"]
    assert on_alexa and all(a >= 3.6 for a, _ in on_alexa)                   # never before Fred is talking
    covered = lambda lo, hi: sum(max(0, min(b, hi) - max(a, lo)) for a, b in on_alexa)
    assert covered(4, 12) >= 6                       # on Fred while the pan is on Fred
    assert covered(12.5, 16) == 0                    # Fred still talking, pan has left: back to the wide
    assert covered(20, 22) == 0                      # pan hasn't arrived at the other man yet: wide
    assert covered(23, 34) >= 9
    wide_time = sum(c[1] - c[0] for c in cuts if c[2] == "red" and 12.5 <= c[0] < 16)
    assert wide_time > 0


def test_without_framing_a_roaming_camera_is_never_used_for_a_speaker():
    cams, masks, _ = _roam_world()
    assert all(c[2] != "alexa" for c in panel.plan_cut(cams, masks, 40.0, None))


def test_roam_framing_maps_each_sample_to_the_person_it_framed(tmp_path):
    f = tmp_path / "pan.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25:duration=12",
                    "-pix_fmt", "yuv420p", str(f)], check=True)
    cam = panel.Source("alexa", str(f), "camera", {}, start=0.0)
    seen = []
    verdicts = ["left", "left", "right", "neither", "both", "right"]    # six samples, 2 s each
    masks = panel.roam_framing(cam, ["Fred", "Other"], 12.0, 0.0, classify=lambda jpgs, client: seen.append(len(jpgs)) or verdicts)
    assert seen == [6]
    at = lambda who, t: bool(masks[who][int(t / panel.HOP)])
    assert at("Fred", 1) and at("Fred", 3) and not at("Other", 3)
    assert at("Other", 5) and not at("Fred", 5)
    assert not at("Fred", 7) and not at("Other", 7)          # 'neither'
    assert not at("Fred", 9) and not at("Other", 9)          # 'both': the wide is the better shot
    assert at("Other", 11)
    with pytest.raises(panel.PanelError):
        panel.roam_framing(cam, ["only one"], 12.0, 0.0, classify=lambda j, c: [])

