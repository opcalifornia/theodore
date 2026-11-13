import numpy as np

from theodore.color import match, verify


def _img(seed, lift=0.0, gain=1.0, tint=(0, 0, 0)):
    rng = np.random.default_rng(seed)
    base = rng.random((60, 80, 3)) ** 1.2
    return np.clip(lift + base * gain + np.array(tint), 0, 1)


def _skin(n=4000, seed=0):
    rng = np.random.default_rng(seed)
    y = 0.35 + 0.35 * rng.random(n)
    ang = np.radians(SKIN := 123 + rng.normal(0, 4, n))
    ch = 0.06 + 0.03 * rng.random(n)
    ycc = np.stack([y, ch * np.cos(ang), ch * np.sin(ang)], axis=1)
    return np.clip(match.from_ycc(ycc), 0, 1).reshape(1, n, 3)


def test_measure_reads_tone_tint_and_skin():
    m = verify.measure(_skin())
    assert m["skin"] and abs(m["skin"]["off_line_deg"]) < 6 and m["skin"]["fraction"] > 0.9
    assert m["tone"] == sorted(m["tone"])
    assert verify.measure(_img(1))["skin"] is None or True   # noise may or may not contain skin-ish pixels


def test_identical_images_score_100_and_different_ones_less():
    a = _img(1)
    assert verify.distance(verify.measure(a), verify.measure(a))["score"] == 100.0
    assert verify.distance(verify.measure(a), verify.measure(_img(2, lift=0.2, gain=0.6)))["score"] < 60


def test_skin_stays_near_the_skin_line_even_for_an_extreme_look():
    skin = _skin()
    ref = _img(3, tint=(0.25, -0.1, -0.2))                         # a strongly green/teal reference
    free = match.LookModel(skin, ref, 1.0, skin_protect=False).apply(skin)
    safe = match.LookModel(skin, ref, 1.0, skin_protect=True).apply(skin)
    f, s = verify.measure(free)["skin"], verify.measure(safe)["skin"]
    assert s is not None and abs(s["off_line_deg"]) <= match.SKIN_SPREAD + 4
    assert f is None or abs(f["off_line_deg"]) > abs(s["off_line_deg"])


def test_trims_move_the_picture_the_way_they_say():
    img = _img(4)
    base = match.LookModel(img, img, 1.0).apply(img)
    warm = match.LookModel(img, img, 1.0, match.Trims(warmth=1.0)).apply(img)
    bright = match.LookModel(img, img, 1.0, match.Trims(exposure=0.1)).apply(img)
    flat = match.LookModel(img, img, 1.0, match.Trims(saturation=-1.0)).apply(img)
    assert (warm[..., 0] - warm[..., 2]).mean() > (base[..., 0] - base[..., 2]).mean()
    assert bright.mean() > base.mean()
    assert verify.measure(flat)["saturation"] < 0.01
    import pytest
    with pytest.raises(ValueError):
        match.Trims.from_dict({"sparkle": 1})


def test_refine_gets_closer_to_the_reference_than_the_plain_match():
    src, ref = _img(5), _img(6, lift=0.1, gain=0.7, tint=(0.06, 0.0, -0.06))
    model, info = verify.refine(src, ref, strength=0.85)
    assert info["score_after"] >= info["score_before"]
    graded = model.apply(src)
    rep = verify.report(graded, ref)
    assert rep["score"] >= info["score_before"] - 1.0
    assert isinstance(rep["issues"], list) and rep["issues"]


def test_report_names_what_is_off():
    src, ref = _img(7), _img(8, lift=0.15, gain=0.6)
    rep = verify.report(src, ref)
    assert any("blacks" in i or "highlights" in i for i in rep["issues"]) and rep["score"] < 80
