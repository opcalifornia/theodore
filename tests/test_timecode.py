import pytest

from theodore.resolve import timecode as tc

# fps string -> drop_frame, for every rate the spec calls out explicitly.
RATES = {
    "23.976": ("23.976", False),
    "24": ("24", False),
    "25": ("25", False),
    "29.97_ndf": ("29.97", False),
    "29.97_df": ("29.97", True),
    "30": ("30", False),
    "59.94_ndf": ("59.94", False),
    "59.94_df": ("59.94", True),
    "60": ("60", False),
}


@pytest.mark.parametrize("fps_str,drop_frame", RATES.values(), ids=RATES.keys())
def test_round_trip_zero(fps_str, drop_frame):
    fps = tc.parse_fps(fps_str)
    tc_str = tc.frames_to_timecode(0, fps, drop_frame=drop_frame)
    assert tc.timecode_to_frames(tc_str, fps, drop_frame=drop_frame) == 0


@pytest.mark.parametrize("fps_str,drop_frame", RATES.values(), ids=RATES.keys())
def test_round_trip_many_frame_counts(fps_str, drop_frame):
    fps = tc.parse_fps(fps_str)
    nfps = tc.nominal_fps(fps)
    for frames in [1, 17, nfps - 1, nfps, nfps * 61, nfps * 3600 + 5, nfps * 3661, nfps * 3600 * 2 + 123]:
        tc_str = tc.frames_to_timecode(frames, fps, drop_frame=drop_frame)
        got = tc.timecode_to_frames(tc_str, fps, drop_frame=drop_frame)
        assert got == frames, (
            f"round trip failed for {frames} frames @ {fps_str} drop_frame={drop_frame}: "
            f"got tc={tc_str!r} -> {got} frames"
        )


def test_23_976_is_exact_24000_over_1001():
    fps = tc.parse_fps("23.976")
    assert (fps.numerator, fps.denominator) == (24000, 1001)
    assert tc.nominal_fps(fps) == 24


def test_24_is_an_exact_integer_rate_not_ntsc():
    fps = tc.parse_fps("24")
    assert fps.denominator == 1
    assert fps.numerator == 24
    assert tc.is_ntsc(fps) is False


def test_29_97_is_exact_30000_over_1001():
    fps = tc.parse_fps("29.97")
    assert (fps.numerator, fps.denominator) == (30000, 1001)


def test_drop_frame_not_defined_for_23_976():
    fps = tc.parse_fps("23.976")
    assert tc.allows_drop_frame(fps) is False
    with pytest.raises(tc.TimecodeError):
        tc.timecode_to_frames("00:01:00:02", fps, drop_frame=True)
    with pytest.raises(tc.TimecodeError):
        tc.frames_to_timecode(100, fps, drop_frame=True)


def test_drop_frame_skips_labels_at_29_97():
    fps = tc.parse_fps("29.97")
    # 00:01:00:00 and 00:01:00:01 don't exist in drop-frame labeling --
    # they're the two frame numbers dropped at the top of every non-tenth minute.
    with pytest.raises(tc.TimecodeError):
        tc.timecode_to_frames("00:01:00:00", fps, drop_frame=True)
    with pytest.raises(tc.TimecodeError):
        tc.timecode_to_frames("00:01:00:01", fps, drop_frame=True)
    # :02 onward is fine, and so is the top of a tenth minute (no drop there).
    assert tc.timecode_to_frames("00:01:00:02", fps, drop_frame=True) == 1800
    assert tc.timecode_to_frames("00:10:00:00", fps, drop_frame=True) == 17982


def test_drop_frame_one_hour_is_the_famous_107892():
    # The canonical drop-frame sanity check: after exactly 1 real hour at
    # 29.97fps, drop-frame timecode reads 01:00:00;00, and the underlying
    # frame count is 107892 (108000 nominal frames minus 108 dropped labels:
    # 2 dropped/min * 54 non-tenth minutes in the hour).
    fps = tc.parse_fps("29.97")
    assert tc.timecode_to_frames("01:00:00:00", fps, drop_frame=True) == 107892
    assert tc.frames_to_timecode(107892, fps, drop_frame=True) == "01:00:00;00"


def test_drop_frame_59_94_one_hour():
    fps = tc.parse_fps("59.94")
    expected_frames = 60 * 3600 - 4 * 54  # 4 dropped/min * 54 non-tenth minutes
    assert tc.timecode_to_frames("01:00:00:00", fps, drop_frame=True) == expected_frames
    assert tc.frames_to_timecode(expected_frames, fps, drop_frame=True) == "01:00:00;00"


def test_non_drop_uses_colon_and_drop_frame_uses_semicolon():
    fps = tc.parse_fps("29.97")
    assert tc.frames_to_timecode(50, fps, drop_frame=False) == "00:00:01:20"
    assert tc.frames_to_timecode(50, fps, drop_frame=True) == "00:00:01;20"


def test_parse_timecode_string_detects_drop_frame_from_separator():
    assert tc.parse_timecode_string("01:02:03;04") == (1, 2, 3, 4, True)
    assert tc.parse_timecode_string("01:02:03:04") == (1, 2, 3, 4, False)


def test_malformed_timecode_raises():
    with pytest.raises(tc.TimecodeError):
        tc.parse_timecode_string("not-a-timecode")
    with pytest.raises(tc.TimecodeError):
        tc.parse_timecode_string("01:99:03:04")


def test_frame_out_of_range_raises():
    fps = tc.parse_fps("24")
    with pytest.raises(tc.TimecodeError):
        tc.timecode_to_frames("00:00:00:24", fps)  # only frames 0-23 exist at 24fps


def test_seconds_to_frames_exact_for_23_976():
    # 24000 frames at 24000/1001 fps is exactly 1001 seconds -- the canonical
    # example of why float fps would drift over a long timeline.
    fps = tc.parse_fps("23.976")
    assert tc.frames_to_seconds(24000, fps) == 1001
    assert tc.seconds_to_frames(1001, fps) == 24000


def test_seconds_to_frames_exact_for_29_97():
    fps = tc.parse_fps("29.97")
    assert tc.frames_to_seconds(30000, fps) == 1001
    assert tc.seconds_to_frames(1001, fps) == 30000


def test_add_frames_to_timecode():
    fps = tc.parse_fps("24")
    assert tc.add_frames_to_timecode("01:00:00:00", 24, fps) == "01:00:01:00"
    assert tc.add_frames_to_timecode("01:00:00:00", -1, fps) == "00:59:59:23"


def test_float_fps_is_rejected():
    with pytest.raises(tc.TimecodeError):
        tc.parse_fps(23.976)


def test_parse_fps_rational_string():
    fps = tc.parse_fps("24000/1001")
    assert (fps.numerator, fps.denominator) == (24000, 1001)


def test_parse_fps_plain_integer():
    fps = tc.parse_fps(25)
    assert (fps.numerator, fps.denominator) == (25, 1)


def test_negative_frames_to_timecode_raises():
    with pytest.raises(tc.TimecodeError):
        tc.frames_to_timecode(-1, tc.parse_fps("24"))
