"""CapCut-style text animations as Fusion title templates: well-formed files, one per animation, same controls."""
import re

from theodore.resolve import text_animations as ta


def test_every_animation_is_a_well_formed_fusion_group_with_the_shared_controls():
    assert len(ta.ANIMATIONS) >= 8
    for name in ta.ANIMATIONS:
        t = ta.render(name)
        assert t.count("{") == t.count("}") and t.count("(") == t.count(")"), name
        assert t.lstrip().startswith("{\n\tTools = ordered() {") and "GroupOperator" in t and "MainOutput1" in t, name
        for control in ("StyledText", "Font", "Style", "Size", "Red1", "Green1", "Blue1"):
            assert f'Source = "{control}"' in t, (name, control)               # the same simple controls on every one
        assert "XYPath" not in t                                                # Resolve does not load path objects from files


def test_install_writes_one_separate_file_per_animation_with_a_clear_name(tmp_path):
    written = ta.install(folder=tmp_path)
    assert sorted(p.name for p in written) == sorted(f"Theodore - {n}.setting" for n in ta.ANIMATIONS)
    assert all(p.read_text().strip().endswith("}") for p in written)
    only = ta.install(["Pop In"], folder=tmp_path / "x")
    assert [p.name for p in only] == ["Theodore - Pop In.setting"]


def test_splines_have_ascending_keys_and_the_typewriter_reveals_by_expression():
    for name in ta.ANIMATIONS:
        for block in re.findall(r"KeyFrames = \{(.*?)\n\t+\}", ta.render(name), re.S):
            frames = [float(f) for f in re.findall(r"\[([0-9.]+)\] = \{", block)]
            assert frames == sorted(frames), name
    tw = ta.render("Typewriter")
    assert "Expression" in tw and "Src.StyledText.Value" in tw                 # the form Resolve was observed to evaluate
    assert 'Source = "StyledText"' in tw and 'SourceOp = "Src"' in tw          # the editor types into the hidden source


def test_spline_eases_between_keys_and_can_be_linear():
    s = ta.spline("S", [(0, 0.0), (10, 1.0)])
    assert "RH = { 3.33333, 0 }" in s and "LH = { 6.66667, 1 }" in s
    assert "Linear = true" in ta.spline("L", [(0, 0), (4, 1)], linear=True)
