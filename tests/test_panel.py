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
