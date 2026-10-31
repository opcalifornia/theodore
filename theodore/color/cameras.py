"""Camera colour science: from what a camera recorded to a proper display image.

A look is matched on a *display* image -- the same kind of picture as the
reference still -- never on flat log pixels. So each clip is first rendered
the way its manufacturer intends: ARRI LogC3, RED Log3G10/RWG, Sony S-Log3,
Blackmagic Film... through the official conversion LUTs Resolve ships with,
and ARRI LogC4 (no LUT bundled) through ARRI's published LogC4 curve and AWG4
primaries plus a filmic display rendering.

Which camera and curve a clip is comes from the metadata Resolve reads
(Camera Manufacturer, Gamma Notes, Color Space Notes, ISO...).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import numpy as np

LUT_ROOT = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")

OFFICIAL_LUTS = {
    "arri_logc3": "ARRI/2130-1_ARRI_LogC3_2.4_Rec709_D65_17.cube",
    "red_log3g10": "RED/RWG_Log3G10_to_REC709_BT1886_with_LOW_CONTRAST_and_R_3_Soft_size_33.cube",
    "sony_slog3": "Sony/SLog3SGamut3.CineToLC-709TypeA.cube",
    "bmd_film_gen5": "Blackmagic Design/Blackmagic Gen 5 Film to Video.cube",
}


# ---------------------------------------------------------------------------
# .cube LUTs
# ---------------------------------------------------------------------------

@lru_cache(maxsize=16)
def load_cube(path: str) -> np.ndarray:
    size, rows, dmin, dmax = None, [], np.zeros(3), np.ones(3)
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.upper().startswith("TITLE"):
            continue
        up = line.upper()
        if up.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
        elif up.startswith("DOMAIN_MIN"):
            dmin = np.array([float(v) for v in line.split()[1:4]])
        elif up.startswith("DOMAIN_MAX"):
            dmax = np.array([float(v) for v in line.split()[1:4]])
        elif up.startswith("LUT_1D") or up.startswith("LUT_3D_INPUT_RANGE"):
            continue
        else:
            parts = line.split()
            if len(parts) == 3:
                try:
                    rows.append([float(v) for v in parts])
                except ValueError:
                    pass
    if not size or len(rows) != size ** 3:
        raise ValueError(f"Not a 3D .cube LUT: {path}")
    # Red fastest -> table[b, g, r]
    return np.array(rows, dtype=np.float32).reshape(size, size, size, 3)


def apply_cube(rgb: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Trilinear lookup. rgb: (..., 3) in 0..1."""
    n = table.shape[0]
    x = np.clip(rgb, 0, 1) * (n - 1)
    i0 = np.clip(np.floor(x).astype(int), 0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                out = out + w * table[b0 + db, g0 + dg, r0 + dr]
    return out


# ---------------------------------------------------------------------------
# ARRI LogC4 (no LUT ships with Resolve): ARRI's published curve + AWG4
# ---------------------------------------------------------------------------

def _rgb_to_xyz(prims, white=(0.3127, 0.3290)) -> np.ndarray:
    def xyz(x, y):
        return np.array([x / y, 1.0, (1 - x - y) / y])
    m = np.stack([xyz(*p) for p in prims], axis=1)
    s = np.linalg.solve(m, xyz(*white))
    return m * s


AWG4 = ((0.7347, 0.2653), (0.1424, 0.8576), (0.0991, -0.0308))
REC709 = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))
AWG4_TO_709 = np.linalg.inv(_rgb_to_xyz(REC709)) @ _rgb_to_xyz(AWG4)


def logc4_to_linear(e: np.ndarray) -> np.ndarray:
    a = (2 ** 18 - 16) / 117.45
    b = (1023 - 95) / 1023
    c = 95 / 1023
    s = (7 * np.log(2) * 2 ** (7 - 14 * c / b)) / (a * b)
    t = (2 ** (14 * (-c / b) + 6) - 64) / a
    return np.where(e >= 0, (2 ** (14 * (e - c) / b + 6) - 64) / a, e * s + t)


def filmic(lin: np.ndarray, exposure: float = 0.85) -> np.ndarray:
    """Scene linear -> display (Rec.709/BT.1886, gamma 2.4) with a soft
    film-like shoulder, so highlights roll off instead of clipping."""
    x = np.maximum(lin * exposure, 0)
    y = (x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)
    return np.clip(y, 0, 1) ** (1 / 2.4)


def arri_logc4_to_display(rgb: np.ndarray) -> np.ndarray:
    lin = logc4_to_linear(rgb) @ AWG4_TO_709.T
    return filmic(lin)


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def detect(props: dict, path: str = "") -> dict:
    """{'key', 'label'} for a media pool clip's properties."""
    p = {k: str(v) for k, v in (props or {}).items()}
    maker = p.get("Camera Manufacturer", "").lower()
    cam = p.get("Camera Type", "")
    gamma = " ".join([p.get("Gamma Notes", ""), p.get("Input Gamma", ""), p.get("Color Space Notes", ""),
                      p.get("Input Color Space", "")]).lower()
    codec = p.get("Video Codec", "").lower()
    if "arri" in maker or "alexa" in cam.lower() or "amira" in cam.lower():
        if "c4" in gamma or "gamut 4" in gamma:
            return {"key": "arri_logc4", "label": f"{cam or 'ARRI'} -- LogC4 / AWG4"}
        if "logc" in gamma.replace(" ", "") or "wide gamut" in gamma:
            return {"key": "arri_logc3", "label": f"{cam or 'ARRI'} -- LogC3 / AWG3 (ISO {p.get('ISO', '?')})"}
    if "red" == maker or path.lower().endswith(".r3d") or "redcode" in codec or "r3d" in codec:
        return {"key": "red_log3g10", "label": f"{cam or 'RED'} -- Log3G10 / RED Wide Gamut"}
    if "sony" in maker or "s-log3" in gamma or "slog3" in gamma:
        return {"key": "sony_slog3", "label": f"{cam or 'Sony'} -- S-Log3 / S-Gamut3.Cine"}
    if "blackmagic" in maker or "braw" in codec or "film gen 5" in gamma:
        return {"key": "bmd_film_gen5", "label": f"{cam or 'Blackmagic'} -- Film Gen 5"}
    return {"key": "display", "label": f"{cam or 'Unknown camera'} -- already display-referred (Rec.709)"}


def to_display(key: str) -> Callable[[np.ndarray], np.ndarray]:
    """The function that turns this camera's recorded values into a display image."""
    if key == "display":
        return lambda rgb: rgb
    if key == "arri_logc4":
        return arri_logc4_to_display
    rel = OFFICIAL_LUTS.get(key)
    path = LUT_ROOT / rel if rel else None
    if path and path.exists():
        table = load_cube(str(path))
        return lambda rgb: np.clip(apply_cube(rgb, table), 0, 1)
    return lambda rgb: rgb


def describe_lens(props: dict) -> Optional[str]:
    lens = str((props or {}).get("Lens Type") or "").strip()
    return lens or None
