"""Is the grade where it needs to be? Measure it, and close the gap.

`measure` reads a picture the way a colourist reads scopes:
  tone     luma at the 1/10/50/90/99th percentiles (blacks, shadows, mids, highs, peak)
  tint     average colour cast in shadows, mids and highlights (Cb, Cr)
  colour   overall saturation
  skin     how much of the frame is skin, and how far its hue sits from the skin line

`distance` sets two measurements side by side and `refine` trims the look --
exposure, contrast, saturation, warmth, tint -- until the graded picture is as
close to the reference as it can get, without letting skin leave the skin line.
The tone mids are weighted lightly: they are mostly the subject, not the grade.
"""
from __future__ import annotations

import numpy as np

from theodore.color import match

QS = (0.01, 0.1, 0.5, 0.9, 0.99)
QW = np.array([1.0, 1.0, 0.5, 1.0, 1.0])
SAMPLE = 20000
SKIN_LIMIT = 20.0      # degrees off the skin line before refine pushes back


def _sample(rgb: np.ndarray, n: int = SAMPLE) -> np.ndarray:
    flat = rgb.reshape(-1, 3)
    if len(flat) <= n:
        return flat
    return flat[np.linspace(0, len(flat) - 1, n).astype(int)]


def measure(rgb: np.ndarray) -> dict:
    ycc = match.to_ycc(_sample(rgb))
    y = ycc[:, 0]
    bands = match._band_means(ycc)
    chroma = np.hypot(ycc[:, 1], ycc[:, 2])
    w = match.skin_weight(ycc)
    out = {
        "tone": [round(float(v), 4) for v in np.quantile(y, QS)],
        "tint": [[round(float(a), 4), round(float(b), 4)] for a, b in bands],
        "saturation": round(float(chroma.mean()), 4),
        "skin": None,
    }
    frac = float((w > 0.4).mean())
    if frac > 0.01:
        sel = w > 0.4
        cb, cr = float(ycc[sel, 1].mean()), float(ycc[sel, 2].mean())
        ang = float(np.degrees(np.arctan2(cr, cb)) % 360)
        out["skin"] = {"fraction": round(frac, 3), "angle_deg": round(ang, 1),
                       "off_line_deg": round(float((ang - match.SKIN_ANGLE + 180) % 360 - 180), 1)}
    return out


def distance(a: dict, b: dict) -> dict:
    """How far `a` is from `b`: parts and a 0-100 score (100 = identical)."""
    tone = float((np.abs(np.array(a["tone"]) - np.array(b["tone"])) * QW).sum() / QW.sum())
    tint = float(np.mean([np.hypot(*(np.array(x) - np.array(y))) for x, y in zip(a["tint"], b["tint"])]))
    sat = abs(float(np.log((a["saturation"] + 1e-4) / (b["saturation"] + 1e-4))))
    total = 2.0 * tone + 6.0 * tint + 0.5 * sat
    return {"tone": round(tone, 4), "tint": round(tint, 4), "saturation": round(sat, 3),
            "score": round(100 * float(np.exp(-3 * total)), 1), "_total": total}


def report(graded: np.ndarray, ref: np.ndarray) -> dict:
    g, r = measure(graded), measure(ref)
    d = distance(g, r)
    say = []
    if d["tone"] > 0.04:
        diffs = np.array(g["tone"]) - np.array(r["tone"])
        say.append("blacks " + ("lifted too much" if diffs[0] > 0.03 else "crushed vs the reference" if diffs[0] < -0.03 else "match")
                   + "; highlights " + ("too bright" if diffs[4] > 0.03 else "too dim" if diffs[4] < -0.03 else "match"))
    if d["tint"] > 0.012:
        for name, x, y in zip(("shadow", "mid", "highlight"), g["tint"], r["tint"]):
            dcb, dcr = x[0] - y[0], x[1] - y[1]
            if np.hypot(dcb, dcr) > 0.012:
                say.append(f"{name} tint is {'warmer' if dcr - dcb > 0 else 'cooler'} than the reference")
    if d["saturation"] > 0.2:
        say.append("more colourful than the reference" if g["saturation"] > r["saturation"] else "less colourful than the reference")
    skin = g["skin"]
    if skin and abs(skin["off_line_deg"]) > SKIN_LIMIT:
        say.append(f"skin is {abs(skin['off_line_deg']):.0f} degrees off the skin line "
                   f"({'too orange/red' if skin['off_line_deg'] < 0 else 'too green/yellow'})")
    return {"score": d["score"], "parts": {k: d[k] for k in ("tone", "tint", "saturation")},
            "graded": g, "reference": r, "issues": say or ["close to the reference"]}


_STEPS = {"exposure": 0.04, "contrast": 0.15, "saturation": 0.2, "warmth": 0.3, "tint": 0.3}


def _objective(src: np.ndarray, ref_m: dict, build) -> float:
    graded = build().apply(src)
    m = measure(graded)
    total = distance(m, ref_m)["_total"]
    skin = m["skin"]
    if skin and abs(skin["off_line_deg"]) > SKIN_LIMIT:
        total += 0.02 * (abs(skin["off_line_deg"]) - SKIN_LIMIT)
    return total


def refine(src: np.ndarray, ref: np.ndarray, strength: float = 0.85, skin_protect: bool = True,
           rounds: int = 4) -> tuple["match.LookModel", dict]:
    """Coordinate descent on the trims; returns the best model and before/after scores."""
    s, r = _sample(src), _sample(ref)
    ref_m = measure(r)
    base = match.LookModel(s, r, strength, skin_protect=skin_protect)
    before = distance(measure(base.apply(s)), ref_m)["score"]
    trims = match.Trims()
    best = _objective(s, ref_m, lambda: match.LookModel(s, r, strength, trims, skin_protect))
    steps = dict(_STEPS)
    for _ in range(rounds):
        improved = False
        for name in _STEPS:
            for sign in (1, -1):
                cand = match.Trims(**{**trims.to_dict(), "shadow_tint": (0.0, 0.0), "highlight_tint": (0.0, 0.0),
                                      name: getattr(trims, name) + sign * steps[name]})
                val = _objective(s, ref_m, lambda c=cand: match.LookModel(s, r, strength, c, skin_protect))
                if val < best - 1e-5:
                    best, trims, improved = val, cand, True
                    break
        if not improved:
            steps = {k: v / 2 for k, v in steps.items()}
    model = match.LookModel(src, ref, strength, trims, skin_protect)
    after = distance(measure(model.apply(s)), ref_m)["score"]
    return model, {"score_before": before, "score_after": after, "trims": trims.to_dict()}
