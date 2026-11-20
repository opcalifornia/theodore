"""Make two different cameras look like one production, then give them one beautiful look.

A colourist matches cameras on something both of them see. Here that is the scene itself: the second camera's
frame is a zoomed-in piece of the first camera's frame, so the two can be compared on the SAME objects.

  1. locate     find where the tight camera's picture sits inside the wide one (edge cross-correlation, any scale)
  2. match      from those aligned pixels, fit per-channel curves (exposure, white balance, black point, contrast),
                a chroma map (the tight camera's extra tint in near-neutrals) and hue-family fixes (where the two
                makers' colour science render the same object differently)
  3. look       one shared display-referred look (theodore.color.creative.grade_display)
  4. bake       a 3D LUT per camera: what Resolve hands node 1 in, the finished picture out

Everything is numpy; nothing here talks to Resolve.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from theodore.color import creative

# ---------------------------------------------------------------------------
# 1. locate
# ---------------------------------------------------------------------------


def resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ys = (np.arange(h) + 0.5) * img.shape[0] / h - 0.5
    xs = (np.arange(w) + 0.5) * img.shape[1] / w - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, img.shape[0] - 2)
    x0 = np.clip(np.floor(xs).astype(int), 0, img.shape[1] - 2)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    if img.ndim == 3:
        fy, fx = fy[..., None], fx[..., None]
    a, b = img[y0][:, x0], img[y0][:, x0 + 1]
    c, d = img[y0 + 1][:, x0], img[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _blur(a: np.ndarray, k: int = 2) -> np.ndarray:
    for _ in range(k):
        a = (a + np.roll(a, 1, 0) + np.roll(a, -1, 0)) / 3
        a = (a + np.roll(a, 1, 1) + np.roll(a, -1, 1)) / 3
    return a


def edges(luma: np.ndarray) -> np.ndarray:
    l = _blur(luma, 2)
    gy, gx = np.zeros_like(l), np.zeros_like(l)
    gy[1:-1] = l[2:] - l[:-2]
    gx[:, 1:-1] = l[:, 2:] - l[:, :-2]
    return np.hypot(gx, gy)


def ncc(big: np.ndarray, small: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation map of `small` over `big` (FFT)."""
    H, W = big.shape
    h, w = small.shape
    t0 = small - small.mean()
    nt = np.sqrt((t0 ** 2).sum()) + 1e-9
    F = np.fft.rfft2(big, (H + h, W + w))
    T = np.fft.rfft2(t0[::-1, ::-1], (H + h, W + w))
    corr = np.fft.irfft2(F * T, (H + h, W + w))[h - 1:H, w - 1:W]
    ii = np.pad(big, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ii2 = np.pad(big ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    box = lambda I: (I[h:, w:] - I[:-h, w:] - I[h:, :-w] + I[:-h, :-w])[: corr.shape[0], : corr.shape[1]]
    n = h * w
    var = np.maximum(box(ii2) - box(ii) ** 2 / n, 1e-9)
    return corr / (nt * np.sqrt(var))


def locate(wide: np.ndarray, tight: np.ndarray, width: int = 320, scales=None) -> tuple[float, tuple]:
    """(score, (scale, x, y, w, h)): where `tight` sits inside `wide`, as fractions of the wide frame."""
    luma = lambda im: im @ np.array([0.2126, 0.7152, 0.0722])
    r = resize(wide, width, int(width * wide.shape[0] / wide.shape[1]))
    er = edges(luma(r))
    best = (-1.0, None)
    for s in (scales if scales is not None else np.arange(0.24, 0.62, 0.02)):
        w = int(width * s)
        h = int(w * tight.shape[0] / tight.shape[1])
        if h >= r.shape[0] - 2 or w < 8:
            continue
        m = ncc(er, edges(luma(resize(tight, w, h))))
        y, x = np.unravel_index(np.argmax(m), m.shape)
        if m[y, x] > best[0]:
            best = (float(m[y, x]), (float(s), x / width, y / r.shape[0], w / width, h / r.shape[0]))
    return best


def aligned_pairs(wide: np.ndarray, tight: np.ndarray, box: tuple, margin: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """The same scene content from both cameras: (tight pixels, wide pixels), edges trimmed."""
    _, x, y, w, h = box
    H, W, _ = wide.shape
    x0, y0, x1, y1 = int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)
    wp = wide[y0:y1, x0:x1]
    tp = resize(tight, wp.shape[1], wp.shape[0])
    mh, mw = int(wp.shape[0] * margin), int(wp.shape[1] * margin)
    return tp[mh:-mh or None, mw:-mw or None].reshape(-1, 3), wp[mh:-mh or None, mw:-mw or None].reshape(-1, 3)


# ---------------------------------------------------------------------------
# 2. match
# ---------------------------------------------------------------------------

def _lch(disp: np.ndarray):
    lab = creative.to_oklab(np.clip(disp, 0, 1).reshape(-1, 3) ** 2.4)
    return lab[:, 0], np.hypot(lab[:, 1], lab[:, 2]), np.arctan2(lab[:, 2], lab[:, 1])


def _from_lch(L, C, h) -> np.ndarray:
    return np.clip(creative.from_oklab(np.stack([L, C * np.cos(h), C * np.sin(h)], 1)), 0, 1) ** (1 / 2.4)


def _smooth_curve(y: np.ndarray, k: int = 7) -> np.ndarray:
    return np.convolve(np.pad(y, k // 2, mode="edge"), np.ones(k) / k, "valid")


# where two makers' colour science render the SAME object differently (centre deg in OKLab, width, chroma x, hue shift)
DEFAULT_HUE_FIXES = [(12.0, 16.0, 0.70, 7.0), (135.0, 28.0, 0.60, -1.0), (172.0, 22.0, 0.74, 0.0)]


@dataclass
class CameraMatch:
    """Takes the tight camera's display-referred picture onto the wide camera's."""
    xs: np.ndarray                  # curve x grid (0..1)
    curves: np.ndarray              # (3, n) per-channel
    cxs: np.ndarray                 # chroma grid
    cratio: np.ndarray              # chroma multiplier at each chroma
    hue_fixes: list

    def __call__(self, disp: np.ndarray) -> np.ndarray:
        shape = disp.shape
        x = np.clip(disp.reshape(-1, 3), 0, 1)
        out = np.stack([np.interp(x[:, c], self.xs, self.curves[c]) for c in range(3)], 1)
        L, C, h = _lch(out)
        hd = np.degrees(h) % 360
        skin = np.exp(-(((hd - 52 + 180) % 360 - 180) ** 2) / 28 ** 2)                  # skin hues keep their colour
        C = C * (1 + (np.interp(C, self.cxs, self.cratio) - 1) * (1 - 0.92 * skin))
        mult, shift = np.ones_like(C), np.zeros_like(C)
        for c, w, cm, hs in self.hue_fixes:
            b = np.exp(-(((hd - c + 180) % 360 - 180) / w) ** 2) * np.clip((C - 0.02) / 0.04, 0, 1)
            mult *= 1 + (cm - 1) * b
            shift += hs * b
        return _from_lch(L, C * mult, np.radians(hd + shift)).reshape(shape)


def fit_match(tight: np.ndarray, wide: np.ndarray, hue_fixes: Optional[list] = None, n: int = 257) -> CameraMatch:
    """Fit a CameraMatch from aligned pixel pairs (N,3) in display space."""
    qs = np.linspace(0, 1, 401)
    xs = np.linspace(0, 1, n)
    curves = []
    for c in range(3):
        aq = np.quantile(tight[:, c], qs) + np.arange(len(qs)) * 1e-7
        rq = _smooth_curve(np.quantile(wide[:, c], qs))
        y = np.interp(xs, aq, rq)
        y = np.where(xs < aq[0], rq[0] + (xs - aq[0]), y)
        y = np.where(xs > aq[-1], rq[-1] + (xs - aq[-1]), y)
        curves.append(np.maximum.accumulate(np.clip(y, 0, 1)))
    curves = np.stack(curves)
    stage1 = np.stack([np.interp(tight[:, c], xs, curves[c]) for c in range(3)], 1)
    cq = np.linspace(0, 1, 201)
    ca = np.quantile(_lch(stage1)[1], cq) + np.arange(201) * 1e-9
    cr = np.quantile(_lch(wide)[1], cq)
    cxs = np.linspace(0, 0.30, 301)
    ratio = np.clip(np.where(cxs > 1e-4, _smooth_curve(np.interp(cxs, ca, cr), 9) / np.maximum(cxs, 1e-4), 1.0), 0.45, 1.15)
    ratio = 1 + (ratio - 1) * (1 - np.clip((cxs - 0.07) / 0.06, 0, 1))                   # rich colours left alone
    return CameraMatch(xs, curves, cxs, ratio, list(hue_fixes if hue_fixes is not None else DEFAULT_HUE_FIXES))


# ---------------------------------------------------------------------------
# 3 + 4. the look, and the LUTs
# ---------------------------------------------------------------------------

#: rich, cinematic, expensive: deep clean blacks, a soft S, warm luminous skin, cream whites, restrained colour
HOLLYWOOD = (
    creative.Intent(vibrance=0.36, saturation=1.07, density=0.20, green_shift=-6.0, green_richness=0.25, blue_depth=0.12,
                    skin_hold=0.85, shadow_cool=0.011, highlight_warm=0.016, skin_chroma=0.38, skin_glow=0.030,
                    cyan_tame=0.5, highlight_clean=0.6),
    creative.Tone(exposure=0.03, contrast=0.21, black=0.018, knee=0.80, rolloff=1.8),
)


def black_point(rgb: np.ndarray, black_in: float) -> np.ndarray:
    """Pull a camera's lifted floor down (display space)."""
    return np.clip((rgb - black_in) / (1 - black_in), 0, 1)


def write_look_cube(path: Path, pre: Callable[[np.ndarray], np.ndarray], look=HOLLYWOOD, size: int = 65,
                    title: str = "Theodore two-camera look") -> Path:
    intent, tone = look
    return creative.write_cube_display(Path(path), lambda g: pre(g.reshape(-1, 3)).reshape(-1, 3), intent, tone, size=size, title=title)
