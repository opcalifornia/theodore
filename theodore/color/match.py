"""Match a look from any reference image -- a still from a film, a grade you
like -- and turn it into a 3D LUT for a shot.

How a colourist reads a reference, done numerically:

* Tone: the luma distribution is matched quantile by quantile (contrast,
  black level, highlight roll-off, lifted or crushed blacks), as a smooth
  monotonic curve with limited slope so nothing bands or clips harshly.
* Colour by tonal band: film looks are split-toned (teal shadows, warm
  highlights), so the chroma offset is matched separately in shadows, mids
  and highlights and blended smoothly between them.
* Saturation and chroma spread: a Monge-Kantorovich linear transfer on the
  chroma plane (Cb, Cr), with its gains clamped so skin can't go radioactive.

Each shot gets its own LUT computed from its own pixels, so different
cameras/exposures all land on the same look -- shot matching, not one LUT
pasted on everything. `strength` blends toward the untouched image.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

SAMPLE_W = 320
LUT_SIZE = 33
KR, KB = 0.2126, 0.0722  # Rec.709
KG = 1 - KR - KB
BANDS = (0.15, 0.5, 0.85)  # shadow / mid / highlight centres (luma)


def decode_image(path: str, width: int = SAMPLE_W, seek: Optional[float] = None) -> np.ndarray:
    """Any still or video frame -> float RGB (h, w, 3) in 0..1, via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error"]
    if seek is not None:
        cmd += ["-ss", f"{max(seek, 0):.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2:flags=area,format=rgb24",
            "-f", "rawvideo", "-"]
    res = subprocess.run(cmd, capture_output=True, timeout=120)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError(f"Could not read an image from {path}: {res.stderr.decode(errors='replace')[-300:]}")
    n = len(res.stdout) // 3
    height = n // width
    return np.frombuffer(res.stdout[:width * height * 3], dtype=np.uint8).reshape(height, width, 3) / 255.0


def to_ycc(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = KR * r + KG * g + KB * b
    return np.stack([y, (b - y) / (2 * (1 - KB)), (r - y) / (2 * (1 - KR))], axis=-1)


def from_ycc(ycc: np.ndarray) -> np.ndarray:
    y, cb, cr = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    r = y + 2 * (1 - KR) * cr
    b = y + 2 * (1 - KB) * cb
    g = (y - KR * r - KB * b) / KG
    return np.stack([r, g, b], axis=-1)


#: (quantile, how far the reference may move it): ends carry the style (black lift,
#: highlight roll-off); the middle is mostly *content*, so it may only move a little.
TONE_ANCHORS = ((0.01, 0.22), (0.1, 0.12), (0.5, 0.10), (0.9, 0.12), (0.99, 0.22))


def _monotone_curve(xs: np.ndarray, ys: np.ndarray, n: int = 256):
    """Fritsch-Carlson monotone cubic through the anchors, sampled densely."""
    d = np.diff(ys) / np.maximum(np.diff(xs), 1e-9)
    m = np.concatenate([[d[0]], (d[:-1] + d[1:]) / 2, [d[-1]]])
    for i in range(len(d)):
        if d[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            r = a * a + b * b
            if r > 9:
                t = 3 / np.sqrt(r)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    x = np.linspace(0, 1, n)
    k = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
    h = xs[k + 1] - xs[k]
    t = (x - xs[k]) / np.maximum(h, 1e-9)
    y = ((2 * t ** 3 - 3 * t ** 2 + 1) * ys[k] + (t ** 3 - 2 * t ** 2 + t) * h * m[k]
         + (-2 * t ** 3 + 3 * t ** 2) * ys[k + 1] + (t ** 3 - t ** 2) * h * m[k + 1])
    return x, np.clip(np.maximum.accumulate(y), 0, 1)


def _tone_curve(src_y: np.ndarray, ref_y: np.ndarray):
    """Match black point, white point, contrast and a limited mid shift --
    the grade, not the reference's subject matter."""
    qs = np.array([q for q, _ in TONE_ANCHORS])
    limits = np.array([lim for _, lim in TONE_ANCHORS])
    sx, ry = np.quantile(src_y, qs), np.quantile(ref_y, qs)
    sx = np.maximum.accumulate(sx + np.arange(len(sx)) * 1e-4)
    ty = sx + np.clip(ry - sx, -limits, limits)
    ty = np.maximum.accumulate(ty + np.arange(len(ty)) * 1e-4)
    # Extend to the ends, keeping the reference's black lift / white roll-off.
    x0, y0 = 0.0, float(np.clip(ty[0] - sx[0] * (ty[1] - ty[0]) / max(sx[1] - sx[0], 1e-6), 0, ty[0]))
    x1 = 1.0
    y1 = float(np.clip(ty[-1] + (1 - sx[-1]) * (ty[-1] - ty[-2]) / max(sx[-1] - sx[-2], 1e-6), ty[-1], 1))
    xs = np.concatenate([[x0], sx, [x1]])
    ys = np.concatenate([[y0], ty, [y1]])
    return _monotone_curve(xs, ys)


def _band_weights(y: np.ndarray) -> np.ndarray:
    w = np.stack([np.exp(-((y - c) / 0.22) ** 2) for c in BANDS], axis=-1)
    return w / (w.sum(axis=-1, keepdims=True) + 1e-9)


def _band_means(ycc: np.ndarray) -> np.ndarray:
    w = _band_weights(ycc[..., 0])
    return np.stack([(w[..., k:k + 1] * ycc[..., 1:]).sum(axis=0) / (w[..., k].sum() + 1e-9)
                     for k in range(len(BANDS))])  # (bands, 2)


def _sqrtm(m: np.ndarray) -> np.ndarray:
    vals, vecs = np.linalg.eigh(m)
    return vecs @ np.diag(np.sqrt(np.maximum(vals, 1e-12))) @ vecs.T


def _mkl(src_cov: np.ndarray, ref_cov: np.ndarray, lo: float = 0.45, hi: float = 2.2) -> np.ndarray:
    a = _sqrtm(src_cov)
    a_inv = np.linalg.inv(a)
    t = a_inv @ _sqrtm(a @ ref_cov @ a) @ a_inv
    vals, vecs = np.linalg.eigh((t + t.T) / 2)
    return vecs @ np.diag(np.clip(vals, lo, hi)) @ vecs.T


class LookModel:
    """Everything needed to map a source pixel onto the reference look."""

    def __init__(self, src: np.ndarray, ref: np.ndarray, strength: float = 0.85):
        s, r = to_ycc(src.reshape(-1, 3)), to_ycc(ref.reshape(-1, 3))
        self.strength = float(np.clip(strength, 0, 1))
        self.cx, self.cy = _tone_curve(s[:, 0], r[:, 0])
        s_mapped = s.copy()
        s_mapped[:, 0] = np.interp(s[:, 0], self.cx, self.cy)
        self.src_means = _band_means(s)
        self.ref_means = _band_means(r)
        s_c = s[:, 1:] - (_band_weights(s[:, 0]) @ self.src_means)
        r_c = r[:, 1:] - (_band_weights(r[:, 0]) @ self.ref_means)
        self.chroma = _mkl(np.cov(s_c.T) + 1e-6 * np.eye(2), np.cov(r_c.T) + 1e-6 * np.eye(2))

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        shape = rgb.shape
        ycc = to_ycc(rgb.reshape(-1, 3))
        y_new = np.interp(ycc[:, 0], self.cx, self.cy)
        c = ycc[:, 1:] - _band_weights(ycc[:, 0]) @ self.src_means
        c = c @ self.chroma.T + _band_weights(y_new) @ self.ref_means
        out = from_ycc(np.concatenate([y_new[:, None], c], axis=1))
        out = self.strength * out + (1 - self.strength) * rgb.reshape(-1, 3)
        return np.clip(out, 0, 1).reshape(shape)

    def describe(self) -> dict:
        mid = np.interp([0.1, 0.5, 0.9], self.cx, self.cy)
        sat = float(np.sqrt(np.linalg.det(self.chroma)))

        def tint(v):
            cb, cr = float(v[0]), float(v[1])
            if abs(cb) < 0.008 and abs(cr) < 0.008:
                return "neutral"
            warmth = cr - cb  # +red/-blue reads warm; +blue/-red reads cool
            hue = "warm/orange" if warmth > 0.01 else "teal/cool" if warmth < -0.01 else "green/magenta"
            return f"{hue} ({cb:+.3f} Cb, {cr:+.3f} Cr)"
        return {"shadows_in_0.1_out": round(float(mid[0]), 3), "mids_in_0.5_out": round(float(mid[1]), 3),
                "highlights_in_0.9_out": round(float(mid[2]), 3), "saturation_scale": round(sat, 2),
                "reference_shadow_tint": tint(self.ref_means[0]), "reference_highlight_tint": tint(self.ref_means[2])}


def write_cube(model: LookModel, path: Path, title: str = "Theodore look", size: int = LUT_SIZE) -> Path:
    g = np.linspace(0, 1, size)
    # .cube order: red changes fastest, then green, then blue.
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = model.apply(grid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        fh.writelines(f"{x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in out)
    return path


def frames_for_clip(path: str, start_s: float, end_s: float, count: int = 5) -> np.ndarray:
    span = max(end_s - start_s, 0.04)
    imgs = [decode_image(path, seek=start_s + span * (k + 0.5) / count) for k in range(count)]
    return np.concatenate([i.reshape(-1, 3) for i in imgs])


def preview(reference: str, source: str, lut: Path, out: Path, seek: float = 0.0) -> Path:
    """Reference | before | after, side by side, for checking the match."""
    h = 360
    filt = (f"[0]scale=-2:{h},setsar=1[a];[1]scale=-2:{h},setsar=1,split[b][c];"
            f"[c]lut3d=file='{lut}'[d];[a][b][d]hstack=3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", reference, "-ss", f"{seek:.3f}", "-i", source,
                    "-frames:v", "1", "-filter_complex", filt, str(out)], check=True, capture_output=True,
                   timeout=120)
    return out


def cache_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
