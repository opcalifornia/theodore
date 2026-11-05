"""Professional text animation portfolio: per-letter animation, glitch, light sweeps, wipes, lower thirds.

Each entry is one Fusion title template (a `.setting` file, installed next to the basic ones from `text_animations`)
and is paired with a font that suits it, so a template looks right the moment it is dropped on the timeline.
The per-letter ones use Fusion's own Text+ Follower, so any text length works and every letter is animated in turn.
Fonts are open-licence (OFL) families from `data/fonts`, installed by `install_fonts()`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import text_animations as ta
from .text_animations import _instance, spline, text_tool, wrap

FONT_DIR = Path(__file__).resolve().parents[2] / "data" / "fonts"
USER_FONTS = Path.home() / "Library" / "Fonts"

SAMPLE = "Your Text"


def install_fonts() -> list[Path]:
    USER_FONTS.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(FONT_DIR.glob("*.ttf")):
        dest = USER_FONTS / f.name
        if not dest.exists():
            shutil.copy2(f, dest)
        out.append(dest)
    return out


_LAST_TOGGLES: tuple = ()


def _follower(inputs: str, delay: float = 2, order: int = 0, toggles=(), text: str = SAMPLE) -> str:
    global _LAST_TOGGLES
    _LAST_TOGGLES = tuple(toggles)
    tg = "".join(f"\t\t\t\t\t\t{t} = Input {{ Value = 1, }},\n" for t in toggles)
    return ("\t\t\t\tFollower = StyledTextFollower {\n\t\t\t\t\tCtrlWZoom = false,\n\t\t\t\t\tInputs = {\n"
            f"\t\t\t\t\t\tDelay = Input {{ Value = {delay}, }},\n\t\t\t\t\t\tOrder = Input {{ Value = {order}, }},\n"
            f"\t\t\t\t\t\tText = Input {{ Value = StyledText {{ Value = \"{text}\" }}, }},\n{tg}{inputs}\t\t\t\t\t}},\n\t\t\t\t}},\n")


def _src(name: str, inp: str) -> str:
    return f"\t\t\t\t\t\t{inp} = Input {{ SourceOp = \"{name}\", Source = \"Value\", }},\n"


def _expr(inp: str, expr: str, value: str = "0") -> str:
    return f"\t\t\t\t\t\t{inp} = Input {{ Value = {value}, Expression = \"{expr}\", }},\n"


def _letters(name: str, font: str, style: str, size: float, tracking: float, follower: str, splines: str = "",
             out: str = "Text", extra_text: str = "") -> str:
    """Text+ fed by a Follower: the per-letter family."""
    text_in = ("\t\t\t\t\t\tStyledText = Input { SourceOp = \"Follower\", Source = \"StyledText\", },\n" +
               f"\t\t\t\t\t\tCharacterSpacing = Input {{ Value = {tracking}, }},\n" + extra_text +
               "".join(f"\t\t\t\t\t\t{t} = Input {{ Value = 1, }},\n" for t in _LAST_TOGGLES))
    tools = splines + follower + text_tool(text_in, size=size, font=font, style=style)
    pub = _instance(8, "Follower", "Delay", "\t\t\t\t\tName = \"Letter Delay\",\n")
    return wrap(name, tools, out, size=size, text_op="Follower", text_src="Text", extra_published=pub)


# ---------------------------------------------------------------------------
# per-letter animations
# ---------------------------------------------------------------------------

def letter_slam(font="Anton", style="Regular", size=0.16, tracking=1.0) -> str:
    """Each letter slams down from the camera: huge and soft, shrinking to place with a small rebound."""
    sz = spline("SlamSize", [(0, 4.5), (6, 0.9), (9, 1.06), (12, 1.0)])
    blur = spline("SlamBlur", [(0, 30.0), (6, 0.0)])
    fade = spline("SlamOp", [(0, 0.0), (3, 1.0)], linear=True)
    f = _follower(_src("SlamSize", "CharacterSizeX") + _src("SlamSize", "CharacterSizeY") + _src("SlamBlur", "SoftnessX1") +
                  _src("SlamBlur", "SoftnessY1") + _src("SlamOp", "Opacity1"), delay=2.4, toggles=("TransformSize",))
    return _letters("ThLetterSlam", font, style, size, tracking, f, sz + blur + fade)


def focus_pull(font="Montserrat", style="Black", size=0.14, tracking=1.0) -> str:
    """Letters rack into focus one by one: blurred and slightly large, settling sharp."""
    fade = spline("FocusOp", [(0, 0.0), (8, 1.0)])
    blur = spline("FocusBlur", [(0, 40.0), (14, 0.0)])
    sz = spline("FocusSize", [(0, 1.5), (14, 1.0)])
    f = _follower(_src("FocusOp", "Opacity1") + _src("FocusBlur", "SoftnessX1") + _src("FocusBlur", "SoftnessY1") +
                  _src("FocusSize", "CharacterSizeX") + _src("FocusSize", "CharacterSizeY"), delay=1.8, toggles=("TransformSize",))
    return _letters("ThFocusPull", font, style, size, tracking, f, fade + blur + sz)


def _sine(name: str, base: float, amp: float, period: float, phase: float = 0.0, frames: int = 600) -> str:
    """A looping sine as a long linear spline (Follower delays splines per letter; expressions it ignores)."""
    import math
    step = max(period / 8, 1)
    keys, t = [], 0.0
    while t <= frames:
        keys.append((round(t, 3), base + amp * math.sin(2 * math.pi * t / period + phase)))
        t += step
    return spline(name, keys, linear=True)


def wave(font="Archivo Black", style="Regular", size=0.15, tracking=1.0) -> str:
    """A rolling wave runs through the letters: each letter tilts and rises in height in turn, looping."""
    fade = spline("WaveOp", [(0, 0.0), (3, 1.0)], linear=True)
    sp = fade + _sine("WaveTilt", 0, 9, 30) + _sine("WaveY", 1, 0.28, 30, 1.5) + _sine("WaveX", 1, 0.10, 30, 1.5)
    f = _follower(_src("WaveOp", "Opacity1") + _src("WaveTilt", "CharacterAngleZ") + _src("WaveY", "CharacterSizeY") + _src("WaveX", "CharacterSizeX"),
                  delay=2.2, toggles=("TransformRotation", "TransformSize"))
    return _letters("ThWave", font, style, size, tracking, f, sp)


def spin_in(font="Bebas Neue", style="Regular", size=0.19, tracking=1.0) -> str:
    ang = spline("SpinAng", [(0, 270.0), (12, 0.0)])
    sz = spline("SpinSize", [(0, 0.0), (7, 1.18), (12, 1.0)])
    fade = spline("SpinOp", [(0, 0.0), (2, 1.0)], linear=True)
    f = _follower(_src("SpinAng", "CharacterAngleZ") + _src("SpinSize", "CharacterSizeX") + _src("SpinSize", "CharacterSizeY") +
                  _src("SpinOp", "Opacity1"), delay=2, toggles=("TransformRotation", "TransformSize"))
    return _letters("ThSpinIn", font, style, size, tracking, f, ang + sz + fade)


def flip_up(font="Oswald", style="Bold", size=0.15, tracking=1.0) -> str:
    ang = spline("FlipAng", [(0, 90.0), (10, -8.0), (14, 0.0)])
    fade = spline("FlipOp", [(0, 0.0), (3, 1.0)], linear=True)
    f = _follower(_src("FlipAng", "CharacterAngleY") + _src("FlipOp", "Opacity1"), delay=2, toggles=("TransformRotation",))
    return _letters("ThFlipUp", font, style, size, tracking, f, ang + fade)


def elastic_pop(font="Bowlby One", style="Regular", size=0.15, tracking=1.0) -> str:
    sz = spline("ElasticSize", [(0, 0.0), (5, 1.45), (8, 0.88), (11, 1.1), (14, 0.98), (17, 1.0)])
    fade = spline("ElasticOp", [(0, 0.0), (1, 1.0)], linear=True)
    f = _follower(_src("ElasticSize", "CharacterSizeX") + _src("ElasticSize", "CharacterSizeY") + _src("ElasticOp", "Opacity1"),
                  delay=2, toggles=("TransformSize",))
    return _letters("ThElasticPop", font, style, size, tracking, f, sz + fade)


def scatter_in(font="Big Shoulders Display", style="Black", size=0.19, tracking=1.0) -> str:
    fade = spline("ScatOp", [(0, 0.0), (10, 1.0)])
    blur = spline("ScatBlur", [(0, 25.0), (12, 0.0)])
    ang = spline("ScatAng", [(0, 80.0), (14, 0.0)])
    f = _follower(_src("ScatOp", "Opacity1") + _src("ScatBlur", "SoftnessX1") + _src("ScatBlur", "SoftnessY1") + _src("ScatAng", "CharacterAngleZ"),
                  delay=3, order=4, toggles=("TransformRotation",))
    return _letters("ThScatterIn", font, style, size, tracking, f, fade + blur + ang)


def cinematic_tracking(font="Cinzel", style="Black", size=0.11) -> str:
    """Film-title reveal: wide tracking slowly closes in while the title fades up."""
    track = spline("TrackIn", [(0, 3.2), (60, 1.35)])
    fade = spline("TrackFade", [(0, 0.0), (22, 1.0)])
    extra = _src("TrackIn", "CharacterSpacing") + _src("TrackFade", "Opacity1")
    tools = track + fade + text_tool(extra, size=size, font=font, style=style)
    return wrap("ThCinematicTracking", tools, "Text", size=size)


def karaoke_words(font="Montserrat", style="Black", size=0.11) -> str:
    """Words light up one after another (Follower by word). Delay is frames per word."""
    rr = spline("KarRed", [(0, 1.0), (1, 1.0)], linear=True)
    f = ("\t\t\t\tFollower = StyledTextFollower {\n\t\t\t\t\tCtrlWZoom = false,\n\t\t\t\t\tInputs = {\n"
         "\t\t\t\t\t\tRange = Input { Value = 1, },\n\t\t\t\t\t\tDelay = Input { Value = 10, },\n"
         f"\t\t\t\t\t\tText = Input {{ Value = StyledText {{ Value = \"{SAMPLE} Highlighted Word By Word\" }}, }},\n"
         "\t\t\t\t\t\tBlue1 = Input { Value = 0.1, },\n\t\t\t\t\t\tGreen1 = Input { Value = 0.85, },\n\t\t\t\t\t\tRed1 = Input { Value = 1, },\n"
         "\t\t\t\t\t},\n\t\t\t\t},\n")
    text_in = "\t\t\t\t\t\tStyledText = Input { SourceOp = \"Follower\", Source = \"StyledText\", },\n"
    tools = f + text_tool(text_in, size=size, font=font, style=style)
    pub = _instance(8, "Follower", "Delay", "\t\t\t\t\tName = \"Frames Per Word\",\n")
    return wrap("ThKaraoke", tools, "Text", size=size, text_op="Follower", text_src="Text", extra_published=pub)


PRO = {
    "Letter Slam - Anton": letter_slam,
    "Focus Pull - Montserrat Black": focus_pull,
    "Wave - Archivo Black": wave,
    "Spin In - Bebas Neue": spin_in,
    "Flip Up - Oswald": flip_up,
    "Elastic Pop - Bowlby One": elastic_pop,
    "Scatter In - Big Shoulders": scatter_in,
    "Cinematic Tracking - Cinzel": cinematic_tracking,
    "Karaoke Words - Montserrat": karaoke_words,
}


def install(names=None, folder=None):
    ta.ANIMATIONS.update(PRO)
    return ta.install(names or list(PRO), folder)


# ---------------------------------------------------------------------------
# layered effects (several Fusion tools merged)
# ---------------------------------------------------------------------------

def _tool(kind: str, name: str, inputs: str, pos=(0, 0)) -> str:
    return (f"\t\t\t\t{name} = {kind} {{\n\t\t\t\t\tNameSet = true,\n\t\t\t\t\tInputs = {{\n{inputs}\t\t\t\t\t}},\n"
            f"\t\t\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},\n\t\t\t\t}},\n")


def _link(inp: str, op: str, src: str = "Output") -> str:
    return f"\t\t\t\t\t\t{inp} = Input {{ SourceOp = \"{op}\", Source = \"{src}\", }},\n"


def _mask(name: str, center_expr: str, width: str, height: str, angle: float = 0.0, soft: float = 0.0) -> str:
    inp = ("\t\t\t\t\t\tFilter = Input { Value = FuID { \"Fast Gaussian\" }, },\n\t\t\t\t\t\tMaskWidth = Input { Value = 1920, },\n"
           "\t\t\t\t\t\tMaskHeight = Input { Value = 1080, },\n\t\t\t\t\t\tPixelAspect = Input { Value = { 1, 1 }, },\n"
           "\t\t\t\t\t\tClippingMode = Input { Value = FuID { \"None\" }, },\n" + _expr("Center", center_expr, "{ 0.5, 0.5 }") +
           _expr("Width", width, "1") + _expr("Height", height, "1") +
           f"\t\t\t\t\t\tAngle = Input {{ Value = {angle}, }},\n\t\t\t\t\t\tSoftEdge = Input {{ Value = {soft}, }},\n")
    return _tool("RectangleMask", name, inp)


def _merge(name: str, bg: str, fg: str, mode: str = "Normal", extra: str = "") -> str:
    return _tool("Merge", name, _link("Background", bg) + _link("Foreground", fg) +
                 f"\t\t\t\t\t\tApplyMode = Input {{ Value = FuID {{ \"{mode}\" }}, }},\n" + extra)


def _move(name: str, source: str, center_expr: str = "", size: str = "") -> str:
    inp = _link("Input", source) + (_expr("Center", center_expr, "{ 0.5, 0.5 }") if center_expr else "") + (size or "")
    return _tool("Transform", name, inp)


# a repeatable pseudo-random value that holds for `hold` frames, in 0..1, per `seed`
def _noise(seed: float, hold: int = 2) -> str:
    return f"((math.sin(math.floor(time / {hold}) * {seed}) * 43758.5453) % 1)"


def glitch_rgb(font="Anton", style="Regular", size=0.17) -> str:
    """Digital glitch: red and cyan copies tear away in bursts, settling after a hard opening; random re-glitches."""
    n1, n2, n3 = _noise(12.9898), _noise(78.233), _noise(39.346)
    burst = f"(time < 9 or {n3} > 0.82)"
    dx = f"({burst} and ({n1} - 0.5) * 0.07 or 0)"
    dy = f"({burst} and ({n2} - 0.5) * 0.012 or 0)"
    main_text = text_tool("", size=size, font=font, style=style)
    link = ("\t\t\t\t\t\tFont = Input { SourceOp = \"Text\", Source = \"Font\", },\n\t\t\t\t\t\tStyle = Input { SourceOp = \"Text\", Source = \"Style\", },\n"
            "\t\t\t\t\t\tSize = Input { SourceOp = \"Text\", Source = \"Size\", },\n\t\t\t\t\t\tStyledText = Input { SourceOp = \"Text\", Source = \"StyledText\", },\n"
            "\t\t\t\t\t\tWidth = Input { Value = 1920, },\n\t\t\t\t\t\tHeight = Input { Value = 1080, },\n\t\t\t\t\t\tUseFrameFormatSettings = Input { Value = 1, },\n"
            "\t\t\t\t\t\tVerticalJustificationNew = Input { Value = 3, },\n\t\t\t\t\t\tHorizontalJustificationNew = Input { Value = 3, },\n")
    red = text_tool(link + "\t\t\t\t\t\tRed1 = Input { Value = 1, },\n\t\t\t\t\t\tGreen1 = Input { Value = 0.05, },\n\t\t\t\t\t\tBlue1 = Input { Value = 0.1, },\n", name="RedCopy", size=size, font=font, style=style)
    cyan = text_tool(link + "\t\t\t\t\t\tRed1 = Input { Value = 0, },\n\t\t\t\t\t\tGreen1 = Input { Value = 0.9, },\n\t\t\t\t\t\tBlue1 = Input { Value = 1, },\n", name="CyanCopy", size=size, font=font, style=style)
    tools = (main_text + red + cyan +
             _move("RedMove", "RedCopy", f"Point(0.5 - 0.004 - {dx}, 0.5 - {dy})") + _move("CyanMove", "CyanCopy", f"Point(0.5 + 0.004 + {dx}, 0.5 + {dy})") +
             _move("MainMove", "Text", f"Point(0.5 + {dx} * 0.3, 0.5)") +
             _merge("Fringe", "RedMove", "CyanMove", "Screen") + _merge("GlitchOut", "Fringe", "MainMove"))
    return wrap("ThGlitchRGB", tools, "GlitchOut", size=size)


def light_sweep(font="Montserrat", style="Black", size=0.13) -> str:
    """A bright diagonal glint glides across the title (and again every ~4 s)."""
    fade = spline("SweepFade", [(0, 0.0), (10, 1.0)])
    base = text_tool(_src("SweepFade", "Opacity1") + "\t\t\t\t\t\tRed1 = Input { Value = 0.78, },\n\t\t\t\t\t\tGreen1 = Input { Value = 0.78, },\n\t\t\t\t\t\tBlue1 = Input { Value = 0.8, },\n",
                     size=size, font=font, style=style)
    link = ("\t\t\t\t\t\tFont = Input { SourceOp = \"Text\", Source = \"Font\", },\n\t\t\t\t\t\tStyle = Input { SourceOp = \"Text\", Source = \"Style\", },\n"
            "\t\t\t\t\t\tSize = Input { SourceOp = \"Text\", Source = \"Size\", },\n\t\t\t\t\t\tStyledText = Input { SourceOp = \"Text\", Source = \"StyledText\", },\n"
            "\t\t\t\t\t\tWidth = Input { Value = 1920, },\n\t\t\t\t\t\tHeight = Input { Value = 1080, },\n\t\t\t\t\t\tUseFrameFormatSettings = Input { Value = 1, },\n"
            "\t\t\t\t\t\tVerticalJustificationNew = Input { Value = 3, },\n\t\t\t\t\t\tHorizontalJustificationNew = Input { Value = 3, },\n"
            "\t\t\t\t\t\tEffectMask = Input { SourceOp = \"Glint\", Source = \"Mask\", },\n")
    shine = text_tool(link, name="Shine", size=size, font=font, style=style)
    u = "math.min(((time - 14) % 100) / 34, 1)"
    glint = _mask("Glint", f"Point(-0.2 + 1.4 * {u}, 0.5)", "0.07", "1.6", angle=-18, soft=0.03)
    tools = fade + base + glint + shine + _merge("SweepOut", "Text", "Shine")
    return wrap("ThLightSweep", tools, "SweepOut", size=size)


def wipe_reveal(font="Bebas Neue", style="Regular", size=0.19) -> str:
    """Hard left-to-right wipe with a thin accent line drawing under the title."""
    u = "math.min(time / 16, 1)"
    e = f"({u} * {u} * (3 - 2 * {u}))"
    wipe = _mask("WipeMask", f"Point(0.5 - 0.5 * (1 - {e}) * 1.1, 0.5)", f"1.1 * {e}", "1")
    text = text_tool("\t\t\t\t\t\tEffectMask = Input { SourceOp = \"WipeMask\", Source = \"Mask\", },\n", size=size, font=font, style=style)
    line_u = "math.min(math.max(time - 6, 0) / 18, 1)"
    le = f"({line_u} * {line_u} * (3 - 2 * {line_u}))"
    line_mask = _mask("LineMask", f"Point(0.5, 0.405)", f"0.42 * {le}", "0.006")
    bar = _tool("Background", "Accent", "\t\t\t\t\t\tWidth = Input { Value = 1920, },\n\t\t\t\t\t\tHeight = Input { Value = 1080, },\n"
                "\t\t\t\t\t\tUseFrameFormatSettings = Input { Value = 1, },\n\t\t\t\t\t\tTopLeftRed = Input { Value = 1, },\n"
                "\t\t\t\t\t\tTopLeftGreen = Input { Value = 0.78, },\n\t\t\t\t\t\tTopLeftBlue = Input { Value = 0.1, },\n\t\t\t\t\t\tTopLeftAlpha = Input { Value = 1, },\n"
                "\t\t\t\t\t\tEffectMask = Input { SourceOp = \"LineMask\", Source = \"Mask\", },\n")
    tools = wipe + line_mask + text + bar + _merge("WipeOut", "Accent", "Text")
    return wrap("ThWipeReveal", tools, "WipeOut", size=size)


def mask_rise(font="Oswald", style="Bold", size=0.1) -> str:
    """Text slides up out from behind an invisible line, the classic editorial title reveal."""
    u = "math.min(time / 14, 1)"
    e = f"(1 - (1 - {u}) ^ 3)"
    text = text_tool("", size=size, font=font, style=style)
    move = _move("Rise", "Text", f"Point(0.5, 0.5 - 0.11 * (1 - {e}))")
    # mask: everything above the text's baseline stays visible
    mask = _mask("Baseline", "Point(0.5, 0.74)", "1", "0.62")
    clip = _tool("Transform", "RiseOut", _link("Input", "Rise") + "\t\t\t\t\t\tEffectMask = Input { SourceOp = \"Baseline\", Source = \"Mask\", },\n")
    return wrap("ThMaskRise", mask + text + move + clip, "RiseOut", size=size)


def lower_third(font="Montserrat", style="Black", size=0.05) -> str:
    """Name over role with an accent bar: bar draws, name wipes in, role follows. Two text fields."""
    def ease(delay, length):
        t = f"math.min(math.max(time - {delay}, 0) / {length}, 1)"
        return f"(({t}) * ({t}) * (3 - 2 * ({t})))"
    left = 0.075
    pos = f"\t\t\t\t\t\tCenter = Input {{ Value = {{ {left}, 0.215 }}, }},\n\t\t\t\t\t\tHorizontalJustificationNew = Input {{ Value = 0, }},\n"
    name = text_tool("\t\t\t\t\t\tEffectMask = Input { SourceOp = \"NameMask\", Source = \"Mask\", },\n" + pos, size=size, font=font, style=style, text="Name Here")
    role = text_tool("\t\t\t\t\t\tEffectMask = Input { SourceOp = \"RoleMask\", Source = \"Mask\", },\n" +
                     pos.replace("0.215", "0.158") + "\t\t\t\t\t\tSize = Input { Value = 0.032, },\n\t\t\t\t\t\tCharacterSpacing = Input { Value = 1.25, },\n"
                     "\t\t\t\t\t\tFont = Input { Value = \"Oswald\", },\n\t\t\t\t\t\tStyle = Input { Value = \"Bold\", },\n"
                     "\t\t\t\t\t\tRed1 = Input { Value = 0.85, },\n\t\t\t\t\t\tGreen1 = Input { Value = 0.85, },\n\t\t\t\t\t\tBlue1 = Input { Value = 0.85, },\n",
                     name="Role", size=0.032, font="Oswald", style="Bold", text="ROLE OR TITLE")
    name_mask = _mask("NameMask", f"Point(0.5 - 0.5 * (1 - {ease(8, 14)}) * 1.1 + 0.0, 0.215)", f"1.1 * {ease(8, 14)}", "0.16")
    role_mask = _mask("RoleMask", f"Point(0.5 - 0.5 * (1 - {ease(16, 14)}) * 1.1, 0.158)", f"1.1 * {ease(16, 14)}", "0.1")
    bar_mask = _mask("BarMask", f"Point({left - 0.016}, 0.19)", "0.006", f"0.17 * {ease(0, 12)}")
    bar = _tool("Background", "AccentBar", "\t\t\t\t\t\tWidth = Input { Value = 1920, },\n\t\t\t\t\t\tHeight = Input { Value = 1080, },\n"
                "\t\t\t\t\t\tUseFrameFormatSettings = Input { Value = 1, },\n\t\t\t\t\t\tTopLeftRed = Input { Value = 1, },\n"
                "\t\t\t\t\t\tTopLeftGreen = Input { Value = 0.78, },\n\t\t\t\t\t\tTopLeftBlue = Input { Value = 0.1, },\n\t\t\t\t\t\tTopLeftAlpha = Input { Value = 1, },\n"
                "\t\t\t\t\t\tEffectMask = Input { SourceOp = \"BarMask\", Source = \"Mask\", },\n")
    tools = (name_mask + role_mask + bar_mask + name + role + bar + _merge("L3a", "AccentBar", "Text") + _merge("L3Out", "L3a", "Role"))
    pub = _instance(8, "Role", "StyledText", "\t\t\t\t\tName = \"Role / Title\",\n") + _instance(9, "AccentBar", "TopLeftRed", "\t\t\t\t\tName = \"Accent Colour\",\n\t\t\t\t\tControlGroup = 9,\n\t\t\t\t\tDefault = 1,\n") + \
          _instance(10, "AccentBar", "TopLeftGreen", "\t\t\t\t\tControlGroup = 9,\n\t\t\t\t\tDefault = 0.78,\n") + _instance(11, "AccentBar", "TopLeftBlue", "\t\t\t\t\tControlGroup = 9,\n\t\t\t\t\tDefault = 0.1,\n")
    return wrap("ThLowerThird", tools, "L3Out", size=size, extra_published=pub)


PRO.pop("Flip Up - Oswald", None)
PRO.update({
    "Glitch RGB - Anton": glitch_rgb,
    "Light Sweep - Montserrat": light_sweep,
    "Wipe Reveal - Bebas Neue": wipe_reveal,
    "Mask Rise - Oswald": mask_rise,
    "Lower Third - Name and Role": lower_third,
})
