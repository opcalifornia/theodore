"""The editorial engine's pure logic: clean-up cut planning, FCPXML transition
insertion, story select ranges, studio-wide memory."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

from theodore.edit import cleanup, interview
from theodore.resolve import transitions


def _w(text, a, b):
    return {"word": text.strip(",.").lower(), "punctuated_word": text, "frame": a, "end": b}


def test_cleanup_cuts_fillers_repeats_and_long_pauses():
    words = [_w("So,", 0, 10), _w("um,", 12, 20), _w("I,", 22, 26), _w("I", 27, 30), _w("started", 30, 40),
             _w("this.", 40, 48), _w("Then", 96, 104), _w("it", 104, 108), _w("grew.", 108, 120)]
    cuts = cleanup.plan_cuts(words, 24.0, max_pause=0.6, keep_pause=0.25)
    reasons = [c.reason for c in cuts]
    assert any("filler" in r for r in reasons) and any("repeat" in r for r in reasons)
    pause = next(c for c in cuts if "pause" in c.reason and c.start > 40)
    # 48 -> 96 is 2 s of silence; ~0.25 s is kept, not all of it removed.
    assert pause.start > 48 and pause.end < 96 and (96 - 48) - (pause.end - pause.start) == 6
    # The filler cut never eats into the words around it.
    filler = next(c for c in cuts if "filler" in c.reason)
    assert filler.start >= 11 and filler.end <= 27  # merged with the "I," stumble; the kept "I" starts at 27


def test_cleanup_leaves_clean_speech_alone():
    words = [_w("We", 0, 5), _w("kept", 5, 12), _w("going.", 12, 20), _w("Today", 24, 30)]
    assert cleanup.plan_cuts(words, 24.0) == []


def test_select_range_pads_without_reaching_into_neighbours():
    sent = {"words": [{"start_frame": 100, "end_frame": 110}, {"start_frame": 110, "end_frame": 130},
                      {"start_frame": 132, "end_frame": 150}]}
    a, b = interview._select_range(sent, 1, 0, prev_end=98, next_start=152)
    assert a >= 110 - interview.PAD_IN and a > 105    # trimmed the first word, padded, not into it
    assert b <= 151                                   # stops mid-gap before the next sentence


SPINE = """<fcpxml version="1.10"><library><event><project><sequence><spine>
<clip name="a" offset="3600/1s" duration="2/1s"/>
<clip name="b" offset="3602/1s" duration="3/1s"/>
<clip name="c" offset="3605/1s" duration="2/1s"/>
</spine></sequence></project></event></library></fcpxml>"""


def test_cut_points_and_transition_offsets():
    spine = ET.fromstring(SPINE).find(".//spine")
    cuts = transitions.cut_points(spine)
    assert [c[1] for c in cuts] == [Fraction(3602), Fraction(3605)]
    dur = Fraction(1, 2)
    assert transitions._s(cuts[0][1] - dur / 2) == "14407/4s"   # 3601.75 s


def test_studio_preferences_apply_to_every_project(tmp_path):
    from theodore.agent import memory
    memory.set_preference(memory.STUDIO, "captions", "bold yellow, two words at a time", tmp_path)
    memory.set_preference("Client A", "brand colour", "#0A84FF", tmp_path)
    block_a = memory.prompt_block("Client A", tmp_path)
    block_b = memory.prompt_block("Client B", tmp_path)
    assert "bold yellow" in block_a and "#0A84FF" in block_a
    assert "bold yellow" in block_b and "#0A84FF" not in block_b
