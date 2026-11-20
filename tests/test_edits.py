import json

import pytest

from theodore import edits


def _ids(edit_list):
    return edit_list.segment_ids


def test_create_initial_seeds_v001(tmp_path):
    el = edits.create_initial(tmp_path, ["haylee.q01", "marcus.q04"])
    assert el.version == "v001"
    assert el.parent is None
    assert _ids(el) == ["haylee.q01", "marcus.q04"]
    assert (tmp_path / "edits" / "v001.json").exists()


def test_next_version_increments_from_highest_on_disk(tmp_path):
    edits.create_initial(tmp_path, ["a"])
    assert edits.next_version(tmp_path) == "v002"
    parent = edits.load(tmp_path, "v001")
    edits.derive(tmp_path, parent, parent.sequence, ["noop"])
    assert edits.next_version(tmp_path) == "v003"


def test_versions_are_immutable_save_refuses_overwrite(tmp_path):
    el = edits.create_initial(tmp_path, ["a"])
    with pytest.raises(edits.EditListError, match="Refusing to overwrite"):
        edits.save(tmp_path, el)


def test_derive_forks_without_touching_parent(tmp_path):
    parent = edits.create_initial(tmp_path, ["a", "b", "c"])
    child = edits.derive(
        tmp_path, parent,
        [edits.EditEntry("c"), edits.EditEntry("a")],
        ["move c to top", "drop b"],
    )
    assert child.version == "v002"
    assert child.parent == "v001"
    assert _ids(child) == ["c", "a"]
    # Parent on disk is untouched -- that's what makes revert safe.
    assert _ids(edits.load(tmp_path, "v001")) == ["a", "b", "c"]


def test_command_log_records_what_changed(tmp_path):
    parent = edits.create_initial(tmp_path, ["a", "b"])
    child = edits.derive(tmp_path, parent, [edits.EditEntry("b")], ["drop a, it rambles"])
    assert child.command_log == ["drop a, it rambles"]


def test_revert_forks_forward_rather_than_deleting_history(tmp_path):
    v1 = edits.create_initial(tmp_path, ["a", "b"])
    edits.derive(tmp_path, v1, [edits.EditEntry("b")], ["drop a"])
    reverted = edits.revert(tmp_path, "v001")

    assert reverted.version == "v003"  # a NEW version, not a rewind
    assert _ids(reverted) == ["a", "b"]
    # v002 still exists -- you can get back to what you reverted away from.
    assert edits.list_versions(tmp_path) == ["v001", "v002", "v003"]
    assert _ids(edits.load(tmp_path, "v002")) == ["b"]


def test_load_unknown_version_lists_available(tmp_path):
    edits.create_initial(tmp_path, ["a"])
    with pytest.raises(edits.EditListError, match="v001"):
        edits.load(tmp_path, "v099")


def test_parse_and_format_version():
    assert edits.parse_version("v007") == 7
    assert edits.format_version(7) == "v007"
    with pytest.raises(edits.EditListError):
        edits.parse_version("seven")


def test_list_versions_sorted_numerically_not_lexically(tmp_path):
    d = edits.edits_dir(tmp_path)
    for name in ("v001", "v002", "v010"):
        (d / f"{name}.json").write_text(json.dumps({"version": name, "parent": None, "sequence": []}))
    assert edits.list_versions(tmp_path) == ["v001", "v002", "v010"]


def test_round_trip_preserves_overrides_and_notes(tmp_path):
    el = edits.create_initial(tmp_path, ["a"])
    child = edits.derive(
        tmp_path, el,
        [edits.EditEntry("a", in_override=12.5, out_override=20.0, notes="start on the second sentence")],
        ["start a at the second sentence"],
    )
    reloaded = edits.load(tmp_path, child.version)
    entry = reloaded.entry_for("a")
    assert entry.in_override == 12.5
    assert entry.out_override == 20.0
    assert entry.notes == "start on the second sentence"


def test_diff_reports_added_removed_reordered(tmp_path):
    a = edits.create_initial(tmp_path, ["x", "y", "z"])
    b = edits.derive(tmp_path, a, [edits.EditEntry("z"), edits.EditEntry("x"), edits.EditEntry("w")], ["shuffle"])
    d = edits.diff(a, b)
    assert d["added"] == ["w"]
    assert d["removed"] == ["y"]
    assert d["reordered"] is True


def test_diff_not_reordered_when_only_membership_changed(tmp_path):
    a = edits.create_initial(tmp_path, ["x", "y", "z"])
    b = edits.derive(tmp_path, a, [edits.EditEntry("x"), edits.EditEntry("z")], ["drop y"])
    d = edits.diff(a, b)
    assert d["removed"] == ["y"]
    assert d["reordered"] is False  # x still precedes z


def test_diff_reports_override_changes(tmp_path):
    a = edits.create_initial(tmp_path, ["x"])
    b = edits.derive(tmp_path, a, [edits.EditEntry("x", in_override=5.0)], ["trim x"])
    d = edits.diff(a, b)
    assert d["overrides_changed"] == [{"segment_id": "x", "from": [None, None], "to": [5.0, None]}]


def test_format_diff_says_no_structural_change_when_identical(tmp_path):
    a = edits.create_initial(tmp_path, ["x"])
    b = edits.derive(tmp_path, a, list(a.sequence), ["revert to v001"])
    assert "(no structural change)" in edits.format_diff(edits.diff(a, b))


def test_current_version_helpers():
    project = {"current_edit_version": None}
    assert edits.current_version(project) is None
    edits.set_current_version(project, "v002")
    assert edits.current_version(project) == "v002"


def test_load_current_returns_none_when_unset(tmp_path):
    assert edits.load_current(tmp_path, {"current_edit_version": None}) is None


def test_load_current_loads_the_pointed_version(tmp_path):
    edits.create_initial(tmp_path, ["a"])
    el = edits.load_current(tmp_path, {"current_edit_version": "v001"})
    assert _ids(el) == ["a"]


# --- apply_overrides_to_trims: the bridge into assembly/plan.py ---

_BASE_TRIM = {
    "s001": {
        "segment_id": "s001", "original_start": 10.0, "original_end": 20.0,
        "trimmed_start": 11.0, "trimmed_end": 19.0, "cuts": [],
    }
}


def test_overrides_are_folded_into_trimmed_bounds(tmp_path):
    el = edits.EditList("v002", "v001", "", [], [edits.EditEntry("s001", in_override=12.0, out_override=18.0)])
    merged = edits.apply_overrides_to_trims(el, _BASE_TRIM)
    assert merged["s001"]["trimmed_start"] == 12.0
    assert merged["s001"]["trimmed_end"] == 18.0


def test_extend_override_widens_original_bounds_so_handles_can_reach(tmp_path):
    # An `extend` command must be able to reach PAST what the trim pass
    # proposed. plan.py clamps handles to original_start/original_end, so
    # those bounds have to widen with the override or the extend is a no-op.
    el = edits.EditList("v002", "v001", "", [], [edits.EditEntry("s001", in_override=8.0, out_override=22.0)])
    merged = edits.apply_overrides_to_trims(el, _BASE_TRIM)
    assert merged["s001"]["original_start"] == 8.0
    assert merged["s001"]["original_end"] == 22.0


def test_overrides_do_not_mutate_the_input_trims(tmp_path):
    el = edits.EditList("v002", "v001", "", [], [edits.EditEntry("s001", in_override=12.0)])
    edits.apply_overrides_to_trims(el, _BASE_TRIM)
    assert _BASE_TRIM["s001"]["trimmed_start"] == 11.0  # untouched


def test_entries_without_overrides_pass_through_unchanged(tmp_path):
    el = edits.EditList("v002", "v001", "", [], [edits.EditEntry("s001")])
    merged = edits.apply_overrides_to_trims(el, _BASE_TRIM)
    assert merged["s001"] == _BASE_TRIM["s001"]


def test_two_sided_override_without_a_trim_entry_synthesizes_one(tmp_path):
    el = edits.EditList("v002", "v001", "", [], [edits.EditEntry("s099", in_override=3.0, out_override=9.0)])
    merged = edits.apply_overrides_to_trims(el, {})
    assert merged["s099"]["trimmed_start"] == 3.0
    assert merged["s099"]["trimmed_end"] == 9.0
    assert merged["s099"]["original_start"] == 3.0


def test_one_sided_override_without_a_trim_entry_is_ignored_not_guessed(tmp_path):
    el = edits.EditList("v002", "v001", "", [], [edits.EditEntry("s099", in_override=3.0)])
    merged = edits.apply_overrides_to_trims(el, {})
    assert "s099" not in merged  # left for plan.py's own fallback rather than inventing an out point


# --- pending queue: theodore say accumulates here before a real version is minted ---

def test_load_pending_returns_none_when_absent(tmp_path):
    assert edits.load_pending(tmp_path) is None


def test_start_pending_seeds_from_current_version(tmp_path):
    base = edits.create_initial(tmp_path, ["a", "b"])
    pending = edits.start_pending(tmp_path, base)
    assert pending.version == edits.PENDING_VERSION_LABEL
    assert pending.parent == "v001"
    assert _ids(pending) == ["a", "b"]


def test_start_pending_with_no_base_is_empty(tmp_path):
    pending = edits.start_pending(tmp_path, None)
    assert pending.parent is None
    assert _ids(pending) == []


def test_pending_is_not_a_real_version(tmp_path):
    edits.create_initial(tmp_path, ["a"])
    edits.start_pending(tmp_path, edits.load(tmp_path, "v001"))
    # pending.json must not show up as a buildable/loadable version.
    assert edits.list_versions(tmp_path) == ["v001"]
    with pytest.raises(edits.EditListError):
        edits.load(tmp_path, edits.PENDING_VERSION_LABEL)


def test_save_and_reload_pending_round_trips(tmp_path):
    pending = edits.start_pending(tmp_path, None)
    pending.sequence = [edits.EditEntry("x", in_override=5.0)]
    pending.command_log = ["insert x"]
    edits.save_pending(tmp_path, pending)

    reloaded = edits.load_pending(tmp_path)
    assert _ids(reloaded) == ["x"]
    assert reloaded.command_log == ["insert x"]
    assert reloaded.entry_for("x").in_override == 5.0


def test_clear_pending_removes_it(tmp_path):
    edits.start_pending(tmp_path, None)
    edits.clear_pending(tmp_path)
    assert edits.load_pending(tmp_path) is None
    edits.clear_pending(tmp_path)  # idempotent -- clearing twice must not raise


def test_load_or_start_pending_reuses_an_existing_queue(tmp_path):
    first = edits.start_pending(tmp_path, None)
    first.command_log = ["already queued"]
    edits.save_pending(tmp_path, first)

    reused = edits.load_or_start_pending(tmp_path, {"current_edit_version": None})
    assert reused.command_log == ["already queued"]


def test_load_or_start_pending_seeds_fresh_from_current(tmp_path):
    edits.create_initial(tmp_path, ["a", "b"])
    pending = edits.load_or_start_pending(tmp_path, {"current_edit_version": "v001"})
    assert _ids(pending) == ["a", "b"]
    assert pending.command_log == []
