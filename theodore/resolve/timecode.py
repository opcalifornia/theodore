"""SMPTE timecode <-> frame conversion.

This is the single most likely source of silent bugs in Theodore: drop-frame
vs non-drop-frame, and 23.976 vs 24, will misplace every marker downstream if
handled sloppily. All internal math uses fractions.Fraction so long-timeline
arithmetic never drifts the way float fps would.

Two conversions live here, and they are deliberately kept separate:
  - timecode <-> frame count: depends only on the *nominal* (rounded) frame
    rate and drop-frame labeling, e.g. 24000/1001 labels as 24 frames/sec
    even though playback is 23.976 fps.
  - frames <-> real elapsed seconds: depends on the *exact* rational fps.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Optional, Union

FpsLike = Union[Fraction, int, float, str]


class TimecodeError(ValueError):
    """Malformed timecode strings, or an invalid frame rate/timecode combination."""


# Canonical string -> exact rational fps. Anything not listed here can still
# be passed as an "N/D" string or a bare integer string; see parse_fps().
_KNOWN_RATES = {
    "23.976": Fraction(24000, 1001),
    "23.98": Fraction(24000, 1001),
    "24": Fraction(24, 1),
    "25": Fraction(25, 1),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30, 1),
    "50": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60, 1),
}

# Drop-frame labeling is only standardized for the 30 and 60 nominal NTSC
# rates. 23.976 has no standard drop-frame convention -- it's always non-drop.
_DROP_FRAMES_PER_MIN = {30: 2, 60: 4}

_TC_RE = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?P<sep>[:;])(?P<f>\d{1,3})$"
)


def parse_fps(value: FpsLike) -> Fraction:
    """Parse a frame rate given as a Fraction, an "N/D" string, or a known
    decimal/integer string (e.g. "24000/1001", "23.976", "24") into an exact
    Fraction. Floats are refused outright -- a float can't distinguish
    23.976 (24000/1001) from a rounding of 24, and that distinction is
    exactly what this module exists to get right.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TimecodeError(f"Refusing to parse fps from bool {value!r}")
    if isinstance(value, int):
        return Fraction(value, 1)
    if isinstance(value, float):
        raise TimecodeError(
            f"Refusing to parse fps from float {value!r} -- floats lose the "
            "exact 24000/1001 vs 24 distinction. Pass an 'N/D' string, a "
            "known decimal string like '23.976', or a Fraction."
        )
    s = str(value).strip()
    if "/" in s:
        num, _, den = s.partition("/")
        try:
            return Fraction(int(num), int(den))
        except (ValueError, ZeroDivisionError) as exc:
            raise TimecodeError(f"Malformed rational fps string {value!r}") from exc
    if s in _KNOWN_RATES:
        return _KNOWN_RATES[s]
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError) as exc:
        raise TimecodeError(f"Unrecognized fps value {value!r}") from exc


def nominal_fps(fps: Fraction) -> int:
    """The integer frame count per second used for timecode *labeling*
    purposes (e.g. 24000/1001 labels as 24 frames/sec even though playback
    is 23.976 fps)."""
    return round(fps)


def is_ntsc(fps: Fraction) -> bool:
    """True if fps is an NTSC (*1000/1001) rate rather than an integer rate."""
    return fps.denominator != 1


def allows_drop_frame(fps: Fraction) -> bool:
    """Drop-frame labeling is only defined for the 30 and 60 nominal NTSC
    rates. 23.976 (and any non-NTSC rate) has no drop-frame convention."""
    return is_ntsc(fps) and nominal_fps(fps) in _DROP_FRAMES_PER_MIN


def parse_timecode_string(tc: str) -> tuple[int, int, int, int, bool]:
    """Split "HH:MM:SS:FF" / "HH:MM:SS;FF" into (h, m, s, f, drop_frame_hint).

    drop_frame_hint is True when a ';' separates seconds and frames (the
    SMPTE convention for drop-frame display), False for ':'.
    """
    m = _TC_RE.match(tc.strip())
    if not m:
        raise TimecodeError(f"Malformed timecode string {tc!r}, expected HH:MM:SS:FF")
    h, mi, s, f = int(m["h"]), int(m["m"]), int(m["s"]), int(m["f"])
    if mi > 59 or s > 59:
        raise TimecodeError(f"Malformed timecode string {tc!r}: minutes/seconds out of range")
    return h, mi, s, f, m["sep"] == ";"


def timecode_to_frames(tc: str, fps: FpsLike, drop_frame: Optional[bool] = None) -> int:
    """Convert an "HH:MM:SS:FF" timecode string to an absolute frame count.

    `drop_frame` overrides the separator-based hint in `tc` when given
    explicitly. Raises TimecodeError for frame labels that don't exist in
    drop-frame timecode (e.g. 00:01:00:00 and 00:01:00:01 are skipped labels
    at 29.97 DF).
    """
    fps = parse_fps(fps)
    h, m, s, f, hinted_df = parse_timecode_string(tc)
    if drop_frame is None:
        drop_frame = hinted_df
    nfps = nominal_fps(fps)

    if f >= nfps:
        raise TimecodeError(f"Frame {f} out of range for {nfps}fps timecode {tc!r}")

    if drop_frame and not allows_drop_frame(fps):
        raise TimecodeError(f"Drop-frame is not defined for fps={fps} ({nfps} nominal)")

    if not drop_frame:
        return nfps * 3600 * h + nfps * 60 * m + nfps * s + f

    drop_per_min = _DROP_FRAMES_PER_MIN[nfps]
    if m % 10 != 0 and s == 0 and f < drop_per_min:
        raise TimecodeError(
            f"Timecode {tc!r} names a dropped frame label (frames 0-{drop_per_min - 1} "
            f"are skipped at the top of non-tenth minutes in {nfps}fps drop-frame)"
        )

    total_minutes = 60 * h + m
    frame_number = nfps * 3600 * h + nfps * 60 * m + nfps * s + f
    frame_number -= drop_per_min * (total_minutes - total_minutes // 10)
    return frame_number


def frames_to_timecode(frames: int, fps: FpsLike, drop_frame: bool = False) -> str:
    """Convert an absolute frame count to an "HH:MM:SS:FF" ("HH:MM:SS;FF" if
    drop-frame) timecode string."""
    fps = parse_fps(fps)
    if frames < 0:
        raise TimecodeError(f"Cannot convert negative frame count {frames} to timecode")
    nfps = nominal_fps(fps)

    if drop_frame and not allows_drop_frame(fps):
        raise TimecodeError(f"Drop-frame is not defined for fps={fps} ({nfps} nominal)")

    if drop_frame:
        drop_per_min = _DROP_FRAMES_PER_MIN[nfps]
        frames_per_10min = nfps * 60 * 10
        frames_per_min = nfps * 60 - drop_per_min

        d, m_rem = divmod(frames, frames_per_10min)
        if m_rem > drop_per_min:
            adjustment = drop_per_min * 9 * d + drop_per_min * ((m_rem - drop_per_min) // frames_per_min)
        else:
            adjustment = drop_per_min * 9 * d
        frames = frames + adjustment

    f = frames % nfps
    total_seconds = frames // nfps
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    sep = ";" if drop_frame else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"


def frames_to_seconds(frames: int, fps: FpsLike) -> Fraction:
    """Exact elapsed time in seconds for a frame count, at the *real*
    playback rate (uses the exact rational fps, not the nominal label rate)."""
    fps = parse_fps(fps)
    return Fraction(frames) / fps


def seconds_to_frames(seconds: Union[float, Fraction], fps: FpsLike) -> int:
    """Inverse of frames_to_seconds. Floors to the containing frame."""
    fps = parse_fps(fps)
    if not isinstance(seconds, Fraction):
        seconds = Fraction(seconds).limit_denominator(10**9)
    return int(seconds * fps)


def add_frames_to_timecode(tc: str, delta_frames: int, fps: FpsLike, drop_frame: Optional[bool] = None) -> str:
    """Offset a timecode by a signed frame count, preserving its drop/non-drop
    labeling unless overridden."""
    fps = parse_fps(fps)
    _, _, _, _, hinted_df = parse_timecode_string(tc)
    df = hinted_df if drop_frame is None else drop_frame
    start = timecode_to_frames(tc, fps, drop_frame=df)
    return frames_to_timecode(start + delta_frames, fps, drop_frame=df)
