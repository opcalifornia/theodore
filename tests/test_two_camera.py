"""Two cameras, one look: locating the tight camera in the wide one, matching colour on shared content."""
import numpy as np

from theodore.color import creative, two_camera as tc


def _scene(h=90, w=160, seed=3):
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32) + 0.3
    for _ in range(30):                                         # blocks of different brightness / colour
        y, x = rng.integers(0, h - 20), rng.integers(0, w - 30)
        img[y:y + rng.integers(8, 20), x:x + rng.integers(10, 30)] = rng.uniform(0.1, 0.9, 3)
    return tc._blur(img[..., 0], 1)[..., None] * 0 + img


def test_locate_finds_a_zoomed_in_piece_of_the_wide_frame():
    wide = _scene()
    x0, y0, w, h = 50, 20, 64, 40                                # the "tight camera" sees this piece, upscaled
    tight = tc.resize(wide[y0:y0 + h, x0:x0 + w], 128, 80)
    score, (s, x, y, bw, bh) = tc.locate(wide, tight, width=160, scales=np.arange(0.3, 0.5, 0.025))
    assert score > 0.8
    assert abs(x * 160 - x0) <= 3 and abs(y * 90 - y0) <= 3 and abs(bw * 160 - w) <= 4


def test_fit_match_takes_a_tinted_darker_camera_onto_the_reference():
    rng = np.random.default_rng(1)
    wide = rng.uniform(0.05, 0.9, (20000, 3)).astype(np.float32)
    tight = np.clip(wide * np.array([0.8, 0.95, 1.1]) - 0.02, 0, 1)         # darker, blue-tinted copy of the same content
    m = tc.fit_match(tight, wide, hue_fixes=[])
    fixed = m(tight)
    assert np.abs(fixed.mean(0) - wide.mean(0)).max() < 0.02                  # white balance / exposure agree
    assert np.abs(fixed - wide).mean() < np.abs(tight - wide).mean() / 2
    assert (np.diff(m.curves, axis=1) >= -1e-9).all()                         # curves are monotone


def test_match_leaves_skin_hue_colour_alone_and_fixes_a_hue_family():
    skin = np.array([[0.55, 0.38, 0.30]], np.float32)                         # a warm brown
    m = tc.CameraMatch(np.linspace(0, 1, 5), np.tile(np.linspace(0, 1, 5), (3, 1)), np.linspace(0, .3, 4),
                       np.full(4, 0.5), [(12.0, 16.0, 0.5, 0.0)])
    out = m(skin)
    assert np.abs(out - skin).max() < 0.03                                    # chroma map squeezed 50% but skin is exempt
    red = np.array([[0.65, 0.05, 0.12]], np.float32)                          # a hot magenta-red
    m2 = tc.CameraMatch(m.xs, m.curves, m.cxs, np.ones(4), [(12.0, 16.0, 0.5, 0.0)])
    assert tc._lch(m2(red))[1][0] < tc._lch(red)[1][0] * 0.85                 # its chroma comes down


def test_look_cube_is_valid_and_keeps_neutral_greys_neutral(tmp_path):
    path = tc.write_look_cube(tmp_path / "look.cube", lambda g: g, size=17)
    rows = path.read_text().splitlines()
    assert "LUT_3D_SIZE 17" in rows[1] and len(rows) == 4 + 17 ** 3
    grey = np.linspace(0.05, 0.95, 10)[:, None].repeat(3, 1)
    out = creative.grade_display(grey, tc.HOLLYWOOD[0], tc.HOLLYWOOD[1])
    assert np.abs(out - out.mean(1, keepdims=True)).max() < 0.04              # greys stay (nearly) neutral
    assert (np.diff(out[:, 1]) > 0).all()                                      # and the tone curve is monotonic
