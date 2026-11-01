"""Grade from intent, not from a reference: "alive, healthy, colour that pops but looks expensive".

The pipeline, in the order a colourist would think about it:

  1. Decode the camera exactly (ARRI LogC3/LogC4 curves + wide-gamut -> Rec.709 primaries), to scene-linear.
  2. Expose: one gain, from the shot's own log-average brightness, so every clip lands in the same place.
  3. Tone on LUMINANCE, not per channel (a per-channel curve shifts hues and thins colour): a soft
     filmic shoulder, a little S-curve, blacks that stay open, whites that stay clean.
  4. Colour in OKLCH (perceptual: lightness, chroma, hue stay independent):
       vibrance   boost scales with how muted the colour is -- naturals bloom, already-saturated
                  colours are left alone, so nothing goes neon; none in highlights or deep shadow
       density    saturated colours also get a touch darker (what makes colour look rich and
                  printed, not glowing)
       hue        greens pulled toward fresh yellow-green and deepened, blues deepened, skin held
       split      cool, quiet shadows; warm, clean highlights -- sunlight, not an orange cast
     Neutrals (a grey coat, white fur) have no chroma, so none of this touches them.
  5. Encode to Rec.709 / BT.1886 (gamma 2.4).

Smooth functions only, baked into a 65-point 3D LUT, so there is nothing to band.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from theodore.color import cameras

# ---------------------------------------------------------------------------
# camera decode (scene-linear Rec.709 primaries)
# ---------------------------------------------------------------------------

_LC3 = dict(cut=0.010591, a=5.555556, b=0.052272, c=0.247190, d=0.385537, e=5.367655, f=0.092809)  # EI 800
AWG3_TO_709 = np.array([[1.617523, -0.537287, -0.080237],
                        [-0.070573, 1.334613, -0.264040],
                        [-0.021102, -0.226954, 1.248056]])


def logc3_to_linear(t: np.ndarray) -> np.ndarray:
    p = _LC3
    return np.where(t > p["e"] * p["cut"] + p["f"], (10 ** ((t - p["d"]) / p["c"]) - p["b"]) / p["a"],
                    (t - p["f"]) / p["e"])


def to_linear(key: str) -> Callable[[np.ndarray], np.ndarray]:
    """Recorded values -> scene-linear in Rec.709 primaries (18% grey = 0.18)."""
    if key == "arri_logc3":
        return lambda rgb: logc3_to_linear(rgb) @ AWG3_TO_709.T
    if key == "arri_logc4":
        return lambda rgb: cameras.logc4_to_linear(rgb) @ cameras.AWG4_TO_709.T
    to_disp = cameras.to_display(key)           # other cameras: via the maker's own LUT, then undo the 2.4 gamma
    return lambda rgb: np.clip(to_disp(rgb), 0, 1) ** 2.4


# ---------------------------------------------------------------------------
# OKLab
# ---------------------------------------------------------------------------

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_M1I, _M2I = np.linalg.inv(_M1), np.linalg.inv(_M2)


def to_oklab(lin: np.ndarray) -> np.ndarray:
    return np.cbrt(np.maximum(lin, 0) @ _M1.T) @ _M2.T


def from_oklab(lab: np.ndarray) -> np.ndarray:
    return ((lab @ _M2I.T) ** 3) @ _M1I.T


def _smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def _bump(h, centre, width):
    return np.exp(-(((h - centre + 180) % 360 - 180) / width) ** 2)


# ---------------------------------------------------------------------------
# the look
# ---------------------------------------------------------------------------

@dataclass
class Intent:
    key_luma: float = 0.16        # target log-average scene brightness (18% grey is 0.18)
    max_stops: float = 1.0        # never re-expose more than this
    warmth: float = 0.035         # sunlit white balance bias (+warm)
    contrast: float = 0.10        # S-curve about the middle
    shoulder: float = 0.9         # highlight roll-off (1 = filmic default)
    black_lift: float = 0.012     # open blacks (display units)
    vibrance: float = 0.55        # boost for muted colours
    saturation: float = 1.04      # overall
    density: float = 0.22         # darkening of saturated colours
    green_shift: float = -9.0     # degrees toward yellow-green
    green_richness: float = 0.30
    blue_depth: float = 0.12
    skin_hold: float = 0.6        # how much to protect skin-hue colours from vibrance (0..1)
    shadow_cool: float = 0.010    # chroma of the shadow tint (hue ~ teal-blue)
    highlight_warm: float = 0.013 # chroma of the highlight tint (hue ~ amber)
    skin_chroma: float = 0.0      # extra richness in skin-hue colours (fraction)
    skin_glow: float = 0.0        # lightness lift in skin, so faces catch the light (OKLab L)
    cyan_tame: float = 0.0        # calm bright cyan/aqua light sources so they do not glare (fraction of chroma)
    highlight_clean: float = 0.0  # keep bright near-neutral areas (sheer curtains, white shirts) clean of stray tint

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS = {
    "vibrant_organic": Intent(),
    "rich_natural": Intent(vibrance=0.35, density=0.18, contrast=0.08, warmth=0.02, green_richness=0.2),
    "golden_warm": Intent(warmth=0.07, highlight_warm=0.02, vibrance=0.45, green_shift=-12.0),
    "clean_commercial": Intent(vibrance=0.4, contrast=0.12, warmth=0.0, shadow_cool=0.006, highlight_warm=0.006),
}


def auto_gain(lin_pixels: np.ndarray, intent: Intent) -> float:
    """Gain putting this shot's log-average brightness on the look's key (within +/- max_stops)."""
    y = np.maximum(lin_pixels.reshape(-1, 3) @ np.array([0.2126, 0.7152, 0.0722]), 1e-4)
    mean = float(np.exp(np.mean(np.log(y))))
    g = intent.key_luma / mean
    lim = 2 ** intent.max_stops
    return float(np.clip(g, 1 / lim, lim))


def _tone(y: np.ndarray, it: Intent) -> np.ndarray:
    """Luminance in (scene-linear), display-linear out. Filmic shoulder + gentle S, open blacks."""
    x = np.maximum(y, 0)
    s = it.shoulder
    out = (x * (2.51 * x + 0.03)) / (x * (2.43 * s * x + 0.59) + 0.14)
    out = np.clip(out, 0, 1)
    d = out ** (1 / 2.4)                                  # work the contrast curve in display space
    d = d + it.contrast * 0.5 * np.sin(2 * np.pi * (d - 0.0)) * -1 * (1 / (2 * np.pi)) * 2 * 0 + \
        it.contrast * (_smooth(d, 0.0, 1.0) - d) * 1.0     # S: pulls shadows down, lifts highlights, pivot ~0.5
    d = it.black_lift + (1 - it.black_lift) * np.clip(d, 0, 1)
    return np.clip(d, 0, 1) ** 2.4


def grade_linear(lin: np.ndarray, intent: Intent, gain: float = 1.0) -> np.ndarray:
    """Scene-linear Rec.709 in -> display-encoded Rec.709 (gamma 2.4) out, 0..1."""
    shape = lin.shape
    rgb = np.maximum(lin.reshape(-1, 3), 0) * gain
    rgb = rgb * np.array([1 + intent.warmth, 1.0, 1 - intent.warmth * 1.4])      # sunlit white balance
    Y = rgb @ np.array([0.2126, 0.7152, 0.0722])
    Yd = _tone(Y, intent)
    ratio = Yd / np.maximum(Y, 1e-6)
    ratio = np.where(Y < 1e-6, 1.0, ratio)
    disp = np.clip(rgb * ratio[:, None], 0, None)                                  # keep colour ratios: no hue shifts
    return _oklch_color(disp, intent).reshape(shape)


def _oklch_color(disp: np.ndarray, intent: Intent) -> np.ndarray:
    """Display-linear Rec.709 (N,3) in -> display-encoded (gamma 2.4) out: the colour half of the grade."""
    lab = to_oklab(disp)
    L = lab[:, 0]
    C = np.hypot(lab[:, 1], lab[:, 2])
    h = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360

    window = _smooth(L, 0.18, 0.45) * (1 - _smooth(L, 0.82, 0.97))                  # none in deep shadow / highlights
    skin = _bump(h, 55, 20) * _smooth(C, 0.015, 0.05) * _smooth(L, 0.3, 0.5)
    green = _bump(h, 140, 30) * _smooth(C, 0.02, 0.06)
    blue = _bump(h, 255, 35) * _smooth(C, 0.02, 0.06)

    boost = 1 + intent.vibrance * np.exp(-(C / 0.10) ** 2) * window * (1 - intent.skin_hold * skin)
    boost = boost * (1 + intent.green_richness * green * window) * (1 + intent.blue_depth * blue * window)
    C2 = C * boost * intent.saturation
    h2 = h + intent.green_shift * green * (1 - skin)
    L2 = L - intent.density * np.maximum(C2 - 0.07, 0) * window                      # saturated colours sit deeper
    L2 = L2 - 0.045 * green * window - 0.03 * blue * window
    skin_m = _bump(h, 52, 24) * _smooth(C, 0.02, 0.05) * _smooth(L, 0.22, 0.42) * (1 - _smooth(L, 0.86, 0.96))
    C2 = C2 * (1 + intent.skin_chroma * skin_m)
    L2 = L2 + intent.skin_glow * skin_m

    a2, b2 = C2 * np.cos(np.radians(h2)), C2 * np.sin(np.radians(h2))
    sh = 1 - _smooth(L2, 0.12, 0.45)                                                 # cool quiet shadows
    sh = sh * (1 - 0.85 * skin_m)                                                    # the cool tint never goes on skin
    C2 = C2 * (1 - intent.highlight_clean * _smooth(L2, 0.62, 0.80) * (1 - _smooth(C2, 0.03, 0.09)))
    C2 = C2 * (1 - intent.cyan_tame * _bump(h, 205, 32) * _smooth(L2, 0.5, 0.78))     # tame glaring cyan lights
    a2, b2 = C2 * np.cos(np.radians(h2)), C2 * np.sin(np.radians(h2))
    hi = _smooth(L2, 0.62, 0.92) * (1 - _smooth(L2, 0.985, 1.0))                     # warm clean highlights
    a2 = a2 + sh * intent.shadow_cool * np.cos(np.radians(235)) + hi * intent.highlight_warm * np.cos(np.radians(75))
    b2 = b2 + sh * intent.shadow_cool * np.sin(np.radians(235)) + hi * intent.highlight_warm * np.sin(np.radians(75))

    out = from_oklab(np.stack([L2, a2, b2], axis=1))
    out = np.clip(out, 0, 1)                                                          # (soft gamut handling below)
    return np.clip(out, 0, 1) ** (1 / 2.4)


def write_cube(path: Path, camera_key: str, intent: Intent, gain: float, size: int = 65,
               title: str = "Theodore creative grade") -> Path:
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = grade_linear(to_linear(camera_key)(grid), intent, gain)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        fh.writelines(f"{x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in out)
    return path


# ---------------------------------------------------------------------------
# display-referred entry: footage a camera/Resolve has ALREADY turned into Rec.709
# ---------------------------------------------------------------------------

@dataclass
class Tone:
    """Contrast shaping in DISPLAY space, on luminance only (hues and chroma ride along untouched)."""
    exposure: float = 0.0      # stops, applied before the curve
    contrast: float = 0.16     # S-curve strength about mid grey
    black_in: float = 0.0      # input black point: how much of the camera's lifted floor to pull down
    black: float = 0.030       # output black level: open enough to hold detail, deep enough to feel rich
    knee: float = 0.76         # where highlights start to roll off
    rolloff: float = 1.6       # how softly they roll (higher = softer, more headroom kept)
    wb: tuple = (1.0, 1.0, 1.0)  # linear RGB white-balance multipliers
    veil: float = 0.0          # a faint warm light in the shadows, scene-linear (0.001 is already clearly visible)
    veil_tint: tuple = (1.0, 0.85, 0.70)


def _display_tone(Yd: np.ndarray, t: Tone) -> np.ndarray:
    """Display-encoded luminance in -> display-encoded luminance out."""
    x = np.clip((np.clip(Yd, 0, 1) - t.black_in) / (1 - t.black_in), 0, 1)
    x = x + t.contrast * (_smooth(x, 0.0, 1.0) - x)                   # S: deeper shadows, brighter upper mids
    over = np.maximum(x - t.knee, 0) / max(1 - t.knee, 1e-6)
    soft = np.tanh(over * t.rolloff) / np.tanh(t.rolloff)
    x = np.where(x > t.knee, t.knee + (1 - t.knee) * soft, x)         # shoulder: whites stay clean, never clip
    return t.black + (1 - t.black) * np.clip(x, 0, 1)


def grade_display(disp: np.ndarray, intent: Intent, tone: Optional[Tone] = None) -> np.ndarray:
    """Display-encoded Rec.709 (gamma 2.4) in -> graded display-encoded out, 0..1. Same colour science as
    grade_linear, but the tone is shaped in display space because the picture is already display-referred."""
    tone = tone or Tone()
    shape = disp.shape
    lin = np.clip(disp.reshape(-1, 3), 0, 1) ** 2.4
    lin = lin * (2.0 ** tone.exposure) * np.array(tone.wb)
    Y = lin @ np.array([0.2126, 0.7152, 0.0722])
    Yd_in = np.clip(Y, 0, 1) ** (1 / 2.4)
    Yd_out = _display_tone(Yd_in, tone)
    Y_out = Yd_out ** 2.4
    ratio = np.where(Y > 1e-6, Y_out / np.maximum(Y, 1e-6), 1.0)
    graded = np.clip(lin * ratio[:, None], 0, None)                   # luminance-ratio: hue and chroma preserved
    out = _oklch_color(graded, intent)
    if tone.veil:
        lin_out = np.clip(out, 0, 1) ** 2.4
        Yo = np.clip(lin_out @ np.array([0.2126, 0.7152, 0.0722]), 0, 1)
        lin_out = lin_out + tone.veil * np.array(tone.veil_tint) * ((1 - Yo) ** 2.5)[:, None]   # fades out by mid grey
        out = np.clip(lin_out, 0, 1) ** (1 / 2.4)
    return out.reshape(shape)


def write_cube_display(path: Path, pre: Callable[[np.ndarray], np.ndarray], intent: Intent, tone: Tone,
                       size: int = 65, title: str = "Theodore display grade") -> Path:
    """A 3D LUT whose input is what Resolve hands node 1 (`pre` turns it into display Rec.709, e.g. ARRI's own
    LogC3 LUT; identity for footage already in Rec.709) and whose output is the finished look."""
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = grade_display(np.clip(pre(grid), 0, 1), intent, tone)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        fh.writelines(f"{x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in out)
    return path
