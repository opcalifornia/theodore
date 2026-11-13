"""theodore.study: shot/transition detection, rhythm, and the agent tools.

The detector is checked against videos ffmpeg builds with known transitions
at known frames -- a cut, a dissolve, a wipe, a dip through black -- so a
regression in any classifier shows up as the wrong name or the wrong frame.
"""
from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from theodore.study import rhythm, shots, signals

FPS = 24
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

SOURCES = [
    "testsrc2=size=160x90:rate=24:duration={d}",
    "smptebars=size=160x90:rate=24:duration={d}",
    "mandelbrot=size=160x90:rate=24:end_scale=0.3",
    "rgbtestsrc=size=160x90:rate=24:duration={d}",
]


def _render(tmp_path, name, filter_complex, inputs):
    out = tmp_path / f"{name}.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for src in inputs:
        cmd += ["-f", "lavfi", "-i", src]
    cmd += ["-filter_complex", filter_complex, "-map", "[v]", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-crf", "12", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def _detect(path):
    info = signals.probe(path)
    sig = signals.video_signals(path)
    return info, sig, shots.detect(sig, info.fps)


@needs_ffmpeg
def test_hard_cut_found_at_the_right_frame(tmp_path):
    video = _render(tmp_path, "cut",
                    "[0:v]trim=duration=2,setpts=PTS-STARTPTS,format=yuv420p[a];"
                    "[1:v]trim=duration=2,setpts=PTS-STARTPTS,format=yuv420p[b];[a][b]concat=n=2:v=1[v]",
                    [SOURCES[0].format(d=3), SOURCES[1].format(d=3)])
    _info, sig, found = _detect(video)
    cuts = [t for t in found if t.kind == "cut"]
    assert len(cuts) == 1, found
    assert abs(cuts[0].start - 2 * FPS) <= 1
    assert len(shots.shots_from_transitions(sig, found)) == 2


@needs_ffmpeg
@pytest.mark.parametrize("xfade,expected", [
    ("fade", "dissolve"),
    ("fadeblack", "dip_to_black"),
    ("wipeleft", "wipe"),
])
def test_transition_kinds(tmp_path, xfade, expected):
    video = _render(tmp_path, xfade,
                    f"[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];"
                    f"[a][b]xfade=transition={xfade}:duration=0.75:offset=2[v]",
                    [SOURCES[1].format(d=3), SOURCES[3].format(d=3)])
    _info, _sig, found = _detect(video)
    kinds = [t.kind for t in found]
    assert expected in kinds, found
    hit = next(t for t in found if t.kind == expected)
    # The transition covers 18 frames starting at frame 48.
    assert 44 <= hit.start <= 54 and 60 <= hit.end <= 70, hit


@needs_ffmpeg
def test_steady_footage_has_no_boundaries(tmp_path):
    video = _render(tmp_path, "steady", "[0:v]format=yuv420p[v]", [SOURCES[0].format(d=4)])
    _info, _sig, found = _detect(video)
    assert [t for t in found if t.kind != "flash_in_shot"] == []


def test_tempo_and_beats_from_a_click_track():
    sr = signals.AUDIO_SR
    y = np.zeros(sr * 12, dtype=np.float32)
    for k in range(24):  # 120 BPM
        i = int(k * 0.5 * sr)
        y[i:i + 400] = np.sin(np.linspace(0, 200 * np.pi, 400)) * np.linspace(1, 0, 400)
    env = signals.onset_envelope(y)
    bpm, periodicity = rhythm.estimate_tempo(env)
    assert abs(bpm - 120) < 3, bpm
    assert periodicity > 0.25
    beats = rhythm.track_beats(env, bpm)
    sync = rhythm.music_sync([2.0, 4.0, 6.0], beats, bpm, 0, FPS)
    assert sync["cuts_on_beat_pct"] == 100.0


def test_speech_relation_counts_mid_word_cuts():
    words = [{"word": "hello", "punctuated_word": "Hello", "start": 0.0, "end": 0.5},
             {"word": "there", "punctuated_word": "there.", "start": 0.6, "end": 1.0}]
    rel = rhythm.speech_relation([0.3, 1.2], words, 10.0)
    assert rel["cuts_mid_word_pct"] == 50.0
    assert rel["cuts_near_sentence_end_pct"] == 50.0
    assert rel["speech_ratio"] == 0.1


def test_reference_tools_run_without_resolve(tmp_path, monkeypatch):
    from theodore import config
    from theodore.agent import tools
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    ref = tmp_path / "references" / "promo"
    ref.mkdir(parents=True)
    (ref / "blueprint.json").write_text('{"title": "Promo", "genre": "promo", "driven_by": "music"}')
    (ref / "analysis.json").write_text('{"media": {"fps": 24}, "shots": [], "shot_log": {}, '
                                       '"transitions": [], "transition_summary": {}}')

    def no_resolve():
        raise AssertionError("reference tools must not connect to Resolve")

    ctx = tools.AgentContext(connect=no_resolve)
    listed = tools.execute(tools.TOOLS_BY_NAME["list_references"], ctx, {})
    assert listed.data["ok"] and listed.data["references"][0]["name"] == "promo"
    got = tools.execute(tools.TOOLS_BY_NAME["get_reference"], ctx, {"name": "promo"})
    assert got.data["blueprint"]["genre"] == "promo"
    missing = tools.execute(tools.TOOLS_BY_NAME["get_reference"], ctx, {"name": "nope"})
    assert missing.data["ok"] is False
