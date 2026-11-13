"""Look matching: sane on synthetic images (no Resolve, no files)."""
from __future__ import annotations

import numpy as np

from theodore.color import match


def _img(seed, lift=0.0, gain=1.0, tint=(0, 0, 0)):
    rng = np.random.default_rng(seed)
    base = rng.random((60, 80, 3)) ** 1.2
    return np.clip(lift + base * gain + np.array(tint), 0, 1)


def test_matching_an_image_to_itself_changes_almost_nothing():
    img = _img(1)
    out = match.LookModel(img, img, strength=1.0).apply(img)
    assert np.abs(out - img).mean() < 0.03


def test_moves_toward_a_lifted_warm_reference_without_blowing_out():
    src, ref = _img(2), _img(3, lift=0.12, gain=0.75, tint=(0.05, 0.01, -0.05))
    out = match.LookModel(src, ref, strength=1.0).apply(src)
    assert np.quantile(out.mean(axis=-1), 0.02) > np.quantile(src.mean(axis=-1), 0.02)   # blacks lifted
    assert out[..., 0].mean() - out[..., 2].mean() > src[..., 0].mean() - src[..., 2].mean()  # warmer
    assert np.quantile(out.mean(axis=-1), 0.5) < 0.8   # mids not pushed to white


def test_tone_curve_is_monotonic_and_cube_is_well_formed(tmp_path):
    model = match.LookModel(_img(4), _img(5, lift=0.1), strength=0.8)
    assert np.all(np.diff(model.cy) >= 0)
    cube = match.write_cube(model, tmp_path / "look.cube", size=9)
    lines = cube.read_text().splitlines()
    assert "LUT_3D_SIZE 9" in lines and len([l for l in lines if l[:1].isdigit()]) == 9 ** 3
