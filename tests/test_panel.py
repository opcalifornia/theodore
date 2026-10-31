"""Panel multicam: audio sync offsets and the cutting rules (no Resolve)."""
from __future__ import annotations

import numpy as np

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
    assert any(c[2] == "camA" and c[0] <= 5 and c[1] >= 13 for c in cuts)
    assert any(c[2] == "camB" and c[0] <= 19 for c in cuts)
    assert all(c[1] - c[0] >= panel.MIN_SHOT - 1e-9 for c in cuts)


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
