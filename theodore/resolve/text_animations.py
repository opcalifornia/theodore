"""CapCut-style text animations, rebuilt as native Fusion title templates.

Each animation is ONE ordinary Fusion `.setting` file in Resolve's user Titles folder, so it shows up in the
Effects Library like any built-in title and lives there for good. They are plain text, built from stock Fusion
tools (Text+, Transform, Blur, Glow, BezierSpline), so there is no proprietary asset to corrupt and nothing to
relink. Every template publishes the same simple controls: Text, Font, Style, Size, Colour, Speed.

Animations are "in" animations (CapCut's Entrance tab) that finish and hold, so the title can be any length.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

TITLES_DIR = Path.home() / "Library" / "Application Support" / "Blackmagic Design" / "DaVinci Resolve" / "Fusion" / "Templates" / "Edit" / "Titles"
PREFIX = "Theodore - "


def _num(v: float) -> str:
    return f"{v:.6g}"


def spline(name: str, keys: list, color=(179, 28, 244), linear: bool = False) -> str:
    """keys: [(frame, value)]. Flat tangents give a smooth ease-in/ease-out between keys."""
    rows = []
    for i, (t, v) in enumerate(keys):
        lh = rh = ""
        if i > 0:
            lh = f", LH = {{ {_num(t - (t - keys[i - 1][0]) / 3)}, {_num(v)} }}"
        if i < len(keys) - 1:
            rh = f", RH = {{ {_num(t + (keys[i + 1][0] - t) / 3)}, {_num(v)} }}"
        flags = ", Flags = { Linear = true }" if linear else ""
        rows.append(f"\t\t\t\t\t\t[{_num(t)}] = {{ {_num(v)}{lh}{rh}{flags} }}")
    return (f"\t\t\t\t{name} = BezierSpline {{\n\t\t\t\t\tSplineColor = {{ Red = {color[0]}, Green = {color[1]}, Blue = {color[2]} }},\n"
            f"\t\t\t\t\tNameSet = true,\n\t\t\t\t\tKeyFrames = {{\n" + ",\n".join(rows).replace("\t\t\t\t\t\t", "\t\t\t\t\t\t") + "\n\t\t\t\t\t}\n\t\t\t\t},\n")


def xy_path(name: str, xkeys: list, ykeys: list) -> str:
    return (f"\t\t\t\t{name} = XYPath {{\n\t\t\t\t\tShowKeyPoints = false,\n\t\t\t\t\tInputs = {{\n"
            f"\t\t\t\t\t\tX = Input {{ SourceOp = \"{name}X\", Source = \"Value\", }},\n"
            f"\t\t\t\t\t\tY = Input {{ SourceOp = \"{name}Y\", Source = \"Value\", }},\n\t\t\t\t\t}},\n\t\t\t\t}},\n"
            + spline(name + "X", xkeys, (255, 70, 70)) + spline(name + "Y", ykeys, (70, 255, 70)))


def _instance(n: int, op: str, src: str, extra: str = "") -> str:
    return f"\t\t\t\tInput{n} = InstanceInput {{\n\t\t\t\t\tSourceOp = \"{op}\",\n\t\t\t\t\tSource = \"{src}\",\n{extra}\t\t\t\t}},\n"


def text_tool(extra_inputs: str = "", size: float = 0.115, text: str = "Your Text") -> str:
    """The visible Text+. If extra_inputs already sets StyledText (an expression), no default text is written."""
    t = "\t\t\t\t\t\t"
    lines = [
        "\t\t\t\tText = TextPlus {\n\t\t\t\t\tNameSet = true,\n\t\t\t\t\tInputs = {\n",
        f"{t}Width = Input {{ Value = 1920, }},\n", f"{t}Height = Input {{ Value = 1080, }},\n",
        f"{t}UseFrameFormatSettings = Input {{ Value = 1, }},\n",
        f"{t}[\"Gamut.SLogVersion\"] = Input {{ Value = FuID {{ \"SLog2\" }}, }},\n",
    ]
    if "StyledText = Input" not in extra_inputs:
        lines.append(f"{t}StyledText = Input {{ Value = \"{text}\", }},\n")
    lines += [
        f"{t}Font = Input {{ Value = \"Open Sans\", }},\n", f"{t}Style = Input {{ Value = \"Bold\", }},\n",
        f"{t}Size = Input {{ Value = {size}, }},\n",
        f"{t}VerticalJustificationNew = Input {{ Value = 3, }},\n", f"{t}HorizontalJustificationNew = Input {{ Value = 3, }},\n",
        extra_inputs,
        "\t\t\t\t\t},\n\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 0, 0 } },\n\t\t\t\t},\n",
    ]
    return "".join(lines)


def transform_tool(inputs: str, source: str = "Text") -> str:
    return (f"\t\t\t\tMove = Transform {{\n\t\t\t\t\tNameSet = true,\n\t\t\t\t\tInputs = {{\n{inputs}"
            f"\t\t\t\t\t\tInput = Input {{ SourceOp = \"{source}\", Source = \"Output\", }},\n"
            "\t\t\t\t\t},\n\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 150, 0 } },\n\t\t\t\t},\n")


def wrap(name: str, tools: str, out_op: str, size: float = 0.115, text_op: str = "Text") -> str:
    """The published controls every animation shares, around the animation's own tools."""
    published = (_instance(1, text_op, "StyledText", "\t\t\t\t\tName = \"Text\",\n") + _instance(2, "Text", "Font", "\t\t\t\t\tControlGroup = 2,\n") +
                 _instance(3, "Text", "Style", "\t\t\t\t\tControlGroup = 2,\n") + _instance(4, "Text", "Size", f"\t\t\t\t\tDefault = {size},\n") +
                 _instance(5, "Text", "Red1", "\t\t\t\t\tName = \"Text Colour\",\n\t\t\t\t\tControlGroup = 5,\n\t\t\t\t\tDefault = 1,\n") +
                 _instance(6, "Text", "Green1", "\t\t\t\t\tControlGroup = 5,\n\t\t\t\t\tDefault = 1,\n") +
                 _instance(7, "Text", "Blue1", "\t\t\t\t\tControlGroup = 5,\n\t\t\t\t\tDefault = 1,\n"))
    return ("{\n\tTools = ordered() {\n\t\t" + name + " = GroupOperator {\n\t\t\tCtrlWZoom = false,\n\t\t\tInputs = ordered() {\n" + published +
            "\t\t\t},\n\t\t\tOutputs = {\n\t\t\t\tMainOutput1 = InstanceOutput {\n\t\t\t\t\tSourceOp = \"" + out_op + "\",\n\t\t\t\t\tSource = \"Output\",\n\t\t\t\t},\n\t\t\t},\n"
            "\t\t\tViewInfo = GroupInfo { Pos = { 0, 0 } },\n\t\t\tTools = ordered() {\n" + tools + "\t\t\t},\n\t\t},\n\t},\n}\n")


# ---------------------------------------------------------------------------
# the animations (frames at the title's own rate; ~24 fps feel)
# ---------------------------------------------------------------------------

def pop_in() -> str:
    tools = (spline("PopSize", [(0, 0.0), (6, 1.16), (10, 0.96), (13, 1.0)]) + spline("PopFade", [(0, 0.0), (3, 1.0)], linear=True) +
             text_tool("\t\t\t\t\t\tOpacity1 = Input { SourceOp = \"PopFade\", Source = \"Value\", },\n") +
             transform_tool("\t\t\t\t\t\tSize = Input { SourceOp = \"PopSize\", Source = \"Value\", },\n"))
    return wrap("ThPopIn", tools, "Move")


def _center(expr: str) -> str:
    """A Transform whose Center follows an expression (frames are the title's own, `time` = current frame)."""
    return f"\t\t\t\t\t\tCenter = Input {{ Value = {{ 0.5, 0.5 }}, Expression = \"{expr}\", }},\n"


def _fade(name: str, frames: int) -> tuple[str, str]:
    return spline(name, [(0, 0.0), (frames, 1.0)], linear=True), f"\t\t\t\t\t\tOpacity1 = Input {{ SourceOp = \"{name}\", Source = \"Value\", }},\n"


def slide_up() -> str:
    u = "math.min(time / 14, 1)"
    fade, fade_in = _fade("RiseFade", 8)
    tools = fade + text_tool(fade_in) + transform_tool(_center(f"Point(0.5, 0.40 + 0.10 * ({u} * {u} * (3 - 2 * {u})))"))
    return wrap("ThSlideUp", tools, "Move")


def typewriter() -> str:
    """The editor types into `Src`; the visible Text shows one more character every frame so any length ends in 30."""
    src = ("\t\t\t\tSrc = TextPlus {\n\t\t\t\t\tNameSet = true,\n\t\t\t\t\tInputs = {\n"
           "\t\t\t\t\t\tWidth = Input { Value = 1920, },\n\t\t\t\t\t\tHeight = Input { Value = 1080, },\n"
           "\t\t\t\t\t\tUseFrameFormatSettings = Input { Value = 1, },\n"
           "\t\t\t\t\t\tStyledText = Input { Value = \"Your Text\", },\n\t\t\t\t\t},\n"
           "\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 0, 100 } },\n\t\t\t\t},\n")
    expr = ("\t\t\t\t\t\tStyledText = Input { Value = \"\", Expression = \"string.sub(Src.StyledText.Value, 1, "
            "math.floor(time * string.len(Src.StyledText.Value) / 30 + 0.999))\", },\n")
    return wrap("ThTypewriter", src + text_tool(expr, text=""), "Text", text_op="Src")


def zoom_punch() -> str:
    fade, fade_in = _fade("PunchFade", 3)
    tools = (spline("PunchSize", [(0, 2.4), (6, 0.93), (9, 1.03), (12, 1.0)]) + fade + text_tool(fade_in) +
             transform_tool("\t\t\t\t\t\tSize = Input { SourceOp = \"PunchSize\", Source = \"Value\", },\n"))
    return wrap("ThZoomPunch", tools, "Move")


def bounce_drop() -> str:
    """Falls from above (7 frames), hits, then bounces three times with decaying height."""
    fade, fade_in = _fade("DropFade", 2)
    off = "(time < 7 and 0.40 * (1 - (time / 7) ^ 2) or 0.13 * math.exp(-(time - 7) / 5) * math.abs(math.sin((time - 7) * 0.55)))"
    tools = fade + text_tool(fade_in) + transform_tool(_center(f"Point(0.5, 0.5 + {off})"))
    return wrap("ThBounceDrop", tools, "Move")


def shake_impact() -> str:
    fade, fade_in = _fade("ShakeFade", 2)
    jitter = ("Point(0.5 + 0.014 * math.exp(-time / 9) * math.sin(time * 5.1), "
              "0.5 + 0.011 * math.exp(-time / 9) * math.cos(time * 6.3))")
    tools = (spline("ShakeSize", [(0, 1.55), (4, 0.95), (8, 1.0)]) + fade + text_tool(fade_in) +
             transform_tool("\t\t\t\t\t\tSize = Input { SourceOp = \"ShakeSize\", Source = \"Value\", },\n" + _center(jitter)))
    return wrap("ThShakeImpact", tools, "Move")


def blur_reveal() -> str:
    fade, fade_in = _fade("BlurFade", 12)
    blur = ("\t\t\t\tSoften = Blur {\n\t\t\t\t\tNameSet = true,\n\t\t\t\t\tInputs = {\n"
            "\t\t\t\t\t\tXBlurSize = Input { SourceOp = \"BlurAmount\", Source = \"Value\", },\n"
            "\t\t\t\t\t\tInput = Input { SourceOp = \"Text\", Source = \"Output\", },\n\t\t\t\t\t},\n"
            "\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 150, 0 } },\n\t\t\t\t},\n")
    tools = spline("BlurAmount", [(0, 42.0), (16, 0.0)]) + fade + text_tool(fade_in) + blur
    return wrap("ThBlurReveal", tools, "Soften")


def neon_flicker() -> str:
    flicker = [(0, 0.0), (3, 0.95), (5, 0.35), (7, 1.0), (9, 0.5), (12, 0.9), (14, 0.65), (17, 1.0), (20, 0.92), (24, 1.0)]
    glow = ("\t\t\t\tNeon = Glow {\n\t\t\t\t\tNameSet = true,\n\t\t\t\t\tInputs = {\n"
            "\t\t\t\t\t\tXGlowSize = Input { Value = 6, },\n"
            "\t\t\t\t\t\tGlow = Input { Value = 0.45, },\n"
            "\t\t\t\t\t\tBlend = Input { SourceOp = \"NeonPower\", Source = \"Value\", },\n"
            "\t\t\t\t\t\tInput = Input { SourceOp = \"Text\", Source = \"Output\", },\n\t\t\t\t\t},\n"
            "\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 150, 0 } },\n\t\t\t\t},\n")
    tools = (spline("NeonOn", [(k, v) for k, v in flicker], linear=True) +
             spline("NeonPower", [(k, v) for k, v in flicker], linear=True) +
             text_tool("\t\t\t\t\t\tOpacity1 = Input { SourceOp = \"NeonOn\", Source = \"Value\", },\n") + glow)
    return wrap("ThNeonFlicker", tools, "Neon")


ANIMATIONS = {"Pop In": pop_in, "Slide Up": slide_up, "Typewriter": typewriter, "Zoom Punch": zoom_punch, "Bounce Drop": bounce_drop,
              "Shake Impact": shake_impact, "Blur Reveal": blur_reveal, "Neon Flicker": neon_flicker}


def render(name: str) -> str:
    return ANIMATIONS[name]()


def install(names: Optional[list] = None, folder: Optional[Path] = None) -> list[Path]:
    """Write each animation as its own .setting file into the user's Fusion Titles folder."""
    folder = Path(folder or TITLES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name in names or list(ANIMATIONS):
        path = folder / f"{PREFIX}{name}.setting"
        path.write_text(render(name), encoding="utf-8")
        out.append(path)
    return out
