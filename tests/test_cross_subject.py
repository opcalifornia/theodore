"""Cross-subject assembly: one timeline cut from several interviews.

The point of the multi-subject registry was always "move haylee.q03 after
marcus.q04" -- an ordered list of prefixed ids where each id resolves
against ITS OWN subject's transcript, analysis, trims and source media.
These tests pin the three things that makes true:

  1. plan.build_multi_subject_plan measures each id against its own
     subject's utterances and start timecode.
  2. builder.build_timeline gives each clipInfo its own subject's
     mediaPoolItem and frame origin, and annotates each marker from its own
     subject's analysis.
  3. A frame-rate disagreement between subjects is refused before anything
     is created in Resolve, and cannot be routed around.

Fixture shape: haylee's dailies start at 01:00:00:00, marcus's card offload
starts at 00:00:00:00. That difference is what makes "the right origin was
subtracted for the right clip" observable rather than a coincidence.
"""
import json

import click
import pytest

from fake_resolve import (
    FakeAssemblyTimeline,
    FakeFolder,
    FakeMediaPoolItem,
    FakeProject,
)
from theodore import cli, registry
from theodore.assembly import builder
from theodore.assembly import plan as assembly_plan
from theodore.assembly.plan import SubjectSources
from theodore.resolve.connection import ResolveHandles

HAYLEE_SOURCE = "/media/haylee_a.mov"
MARCUS_SOURCE = "/media/marcus_a.mov"


def _transcript(source, start_tc, fps="24"):
    return {
        "source_file": source,
        "fps": fps,
        "start_timecode": start_tc,
        "speakers": {"0": "Subject", "1": "Interviewer"},
        "utterances": [
            {"id": "u001", "speaker": "1", "start": 0.0, "end": 2.0, "text": "Tell me.", "words": []},
            {"id": "u002", "speaker": "0", "start": 2.0, "end": 10.0, "text": "The first answer.", "words": []},
            {"id": "u003", "speaker": "0", "start": 12.5, "end": 20.0, "text": "The second answer.", "words": []},
        ],
    }


def _analysis(subject, labels):
    return {
        "segments": [
            {"id": f"{subject}.q01", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
             "question_text": "Tell me.", "question_label": labels[0], "answer_summary": "First."},
            {"id": f"{subject}.q02", "answer_start_utterance": "u003", "answer_end_utterance": "u003",
             "question_text": "And then?", "question_label": labels[1], "answer_summary": "Second."},
        ],
        "selects": [
            {"segment_id": f"{subject}.q01", "strength": 0.9, "clean_start_utterance": "u002",
             "clean_end_utterance": "u002", "best_line": "The first answer.", "issues": []},
            {"segment_id": f"{subject}.q02", "strength": 0.4, "clean_start_utterance": "u003",
             "clean_end_utterance": "u003", "best_line": "The second answer.", "issues": []},
        ],
        "themes": [{"id": "t1", "label": f"{subject} theme"}],
        "theme_assignments": {f"{subject}.q01": ["t1"]},
    }


HAYLEE_TRANSCRIPT = _transcript(HAYLEE_SOURCE, "01:00:00:00")   # frame 86400 @ 24
MARCUS_TRANSCRIPT = _transcript(MARCUS_SOURCE, "00:00:00:00")   # frame 0
HAYLEE_ANALYSIS = _analysis("haylee", ["Haylee opening", "Haylee turn"])
MARCUS_ANALYSIS = _analysis("marcus", ["Marcus opening", "Marcus turn"])


def sources(marcus_transcript=None):
    return {
        "haylee": SubjectSources(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {}),
        "marcus": SubjectSources(marcus_transcript or MARCUS_TRANSCRIPT, MARCUS_ANALYSIS, {}),
    }


# The interleave the whole feature exists for: haylee, marcus, haylee.
ORDER = ["haylee.q01", "marcus.q02", "haylee.q02"]


def make_project(**kwargs):
    """A pool holding BOTH interviews, filed in separate bins -- the normal
    documentary shape, and what proves the per-clip lookup picks correctly."""
    haylee_item = FakeMediaPoolItem(path=HAYLEE_SOURCE, start_tc="01:00:00:00")
    marcus_item = FakeMediaPoolItem(path=MARCUS_SOURCE, start_tc="00:00:00:00")
    root = FakeFolder(name="Master", subfolders=[
        FakeFolder(name="Haylee", clips=[haylee_item]),
        FakeFolder(name="Marcus", clips=[marcus_item]),
    ])
    project = FakeProject(root=root, **kwargs)
    return project, haylee_item, marcus_item


def handles_for(project):
    """The editor's real cut, already open -- built directly rather than via
    CreateEmptyTimeline so `created_timelines` stays a clean record of what
    a build itself made."""
    existing = FakeAssemblyTimeline("Editor's real cut")
    project.timelines.append(existing)
    project.current_timeline = existing
    return ResolveHandles(resolve=None, project_manager=None, project=project, timeline=existing), existing


# ---------------------------------------------------------------------------
# Planning across subjects
# ---------------------------------------------------------------------------

def test_each_id_is_measured_against_its_own_subjects_start_timecode():
    plan = assembly_plan.build_multi_subject_plan(sources(), ORDER, handle_frames=0)

    assert [c.segment_id for c in plan] == ORDER
    assert [c.subject for c in plan] == ["haylee", "marcus", "haylee"]

    # haylee's media starts at 01:00:00:00 (frame 86400); marcus's at zero.
    # u002 runs 2.0-10.0s, u003 12.5-20.0s, at 24fps.
    assert (plan[0].source_in_frame, plan[0].source_out_frame) == (86400 + 48, 86400 + 240)
    assert (plan[1].source_in_frame, plan[1].source_out_frame) == (300, 480)
    assert (plan[2].source_in_frame, plan[2].source_out_frame) == (86400 + 300, 86400 + 480)


def test_the_new_timeline_is_gapless_across_the_subject_change():
    plan = assembly_plan.build_multi_subject_plan(sources(), ORDER, handle_frames=0)
    assert plan[0].timeline_in_frame == 0
    for previous, current in zip(plan, plan[1:]):
        assert current.timeline_in_frame == previous.timeline_out_frame
    assert assembly_plan.total_runtime_frames(plan) == 192 + 180 + 180


def test_single_and_multi_subject_planning_agree_on_the_same_ids():
    """The two entry points share _clip_for, so a one-subject order planned
    either way must come out identical apart from the subject tag."""
    order = ["haylee.q01", "haylee.q02"]
    single = assembly_plan.build_plan(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {}, order, handle_frames=6)
    multi = assembly_plan.build_multi_subject_plan(sources(), order, handle_frames=6)
    assert [c.subject for c in multi] == ["haylee", "haylee"]
    for a, b in zip(single, multi):
        assert (a.segment_id, a.source_in_frame, a.source_out_frame,
                a.timeline_in_frame, a.timeline_out_frame) == (
            b.segment_id, b.source_in_frame, b.source_out_frame,
            b.timeline_in_frame, b.timeline_out_frame)


def test_planning_a_subject_with_no_sources_raises_rather_than_dropping_it():
    with pytest.raises(ValueError, match="dana"):
        assembly_plan.build_multi_subject_plan(sources(), ["haylee.q01", "dana.q07"])


def test_trims_are_taken_from_the_segments_own_subject():
    trimmed = {
        "haylee": SubjectSources(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {
            "haylee.q01": {"original_start": 2.0, "original_end": 10.0,
                           "trimmed_start": 3.0, "trimmed_end": 9.0, "cuts": []},
        }),
        "marcus": SubjectSources(MARCUS_TRANSCRIPT, MARCUS_ANALYSIS, {}),
    }
    plan = assembly_plan.build_multi_subject_plan(trimmed, ["haylee.q01", "marcus.q01"], handle_frames=0)
    assert plan[0].source_in_frame == 86400 + 72    # 3.0s, trimmed
    assert plan[1].source_in_frame == 48            # 2.0s, untrimmed -- marcus has no trims


# ---------------------------------------------------------------------------
# Building: the right media, per clip
# ---------------------------------------------------------------------------

def build_cross(project, order=ORDER, subject_sources=None, **kwargs):
    subject_sources = subject_sources or sources()
    plan = assembly_plan.build_multi_subject_plan(subject_sources, order, handle_frames=0)
    handles, existing = handles_for(project)
    kwargs.setdefault("project", "doc")
    kwargs.setdefault("subject", "haylee")
    kwargs.setdefault("mode", "v003")
    kwargs.setdefault("timestamp", "20250101_120000")
    result = builder.build_timeline(handles, plan, sources=subject_sources, **kwargs)
    return result, plan, existing


def test_each_clip_pulls_from_its_own_subjects_media_item():
    project, haylee_item, marcus_item = make_project()
    result, plan, existing = build_cross(project)

    infos = project.media_pool.appended_clip_infos[0]
    assert [info["mediaPoolItem"] for info in infos] == [haylee_item, marcus_item, haylee_item]
    assert result.clips_appended == 3
    # The editor's real cut is untouched, as ever.
    assert existing.tracks["video"][1] == []


def test_each_clips_frame_origin_is_its_own_subjects():
    """The bug this guards: subtracting haylee's 01:00:00:00 origin from
    marcus's frames would ask for an in-point an hour before his media."""
    project, _, _ = make_project()
    _, plan, _ = build_cross(project)

    infos = project.media_pool.appended_clip_infos[0]
    # haylee: absolute frames minus 86400. marcus: minus 0.
    assert infos[0]["startFrame"] == 48 and infos[0]["endFrame"] == 240
    assert infos[1]["startFrame"] == 300 and infos[1]["endFrame"] == 480
    assert infos[2]["startFrame"] == 300 and infos[2]["endFrame"] == 480
    for info, clip in zip(infos, plan):
        assert info["endFrame"] - info["startFrame"] == clip.duration_frames


def test_media_is_found_once_per_subject_not_once_per_clip():
    project, _, _ = make_project()
    result, _, _ = build_cross(project)
    # Three clips, two subjects, both already in the pool.
    assert sorted(result.reused_paths) == [HAYLEE_SOURCE, MARCUS_SOURCE]
    assert result.imported_paths == []


def test_markers_are_annotated_from_the_subject_each_clip_came_from():
    project, _, _ = make_project()
    result, plan, _ = build_cross(project)

    markers = result.timeline.markers
    assert result.markers_written == 3
    by_frame = {frame: data for frame, data in markers.items()}
    notes = [by_frame[clip.timeline_in_frame] for clip in plan]

    assert notes[0]["name"] == "01. Haylee opening"
    assert notes[1]["name"] == "02. Marcus turn"
    assert notes[2]["name"] == "03. Haylee turn"

    payloads = [json.loads(n["customData"]) for n in notes]
    assert [p["segment_id"] for p in payloads] == ORDER
    assert [p["subject"] for p in payloads] == ["haylee", "marcus", "haylee"]
    # Strengths come from the right analysis: q01 is 0.9, q02 is 0.4.
    assert [p["strength"] for p in payloads] == [0.9, 0.4, 0.4]
    # marcus's theme label, not haylee's, on marcus's marker.
    assert "marcus theme" not in notes[0]["note"]


def test_a_subject_whose_media_must_be_imported_is_imported_once(tmp_path):
    """marcus isn't in the pool yet; his file is on disk and gets imported,
    while haylee's existing pool clip is reused."""
    marcus_file = tmp_path / "marcus_a.mov"
    marcus_file.write_bytes(b"\0")
    haylee_item = FakeMediaPoolItem(path=HAYLEE_SOURCE, start_tc="01:00:00:00")
    root = FakeFolder(name="Master", clips=[haylee_item])
    project = FakeProject(root=root)
    # marcus's file really does start at zero; the import reads that.
    project.media_pool.import_start_tc_by_path[str(marcus_file)] = "00:00:00:00"

    subject_sources = {
        "haylee": SubjectSources(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {}),
        "marcus": SubjectSources(_transcript(str(marcus_file), "00:00:00:00"), MARCUS_ANALYSIS, {}),
    }
    result, _, _ = build_cross(project, subject_sources=subject_sources)

    assert result.imported_paths == [str(marcus_file)]
    assert result.reused_paths == [HAYLEE_SOURCE]
    assert project.media_pool.imported == [str(marcus_file)]


def test_dry_run_lists_every_subjects_source_file():
    plan = assembly_plan.build_multi_subject_plan(sources(), ORDER, handle_frames=0)
    text = builder.describe_build(plan, sources=sources(), project="doc", subject="haylee", mode="v003")
    assert "Subjects:      2 (haylee, marcus)" in text
    assert HAYLEE_SOURCE in text and MARCUS_SOURCE in text
    assert "Marcus turn" in text and "Haylee opening" in text


# ---------------------------------------------------------------------------
# Frame rates: refuse, don't guess
# ---------------------------------------------------------------------------

MARCUS_2997 = _transcript(MARCUS_SOURCE, "00:00:00:00", fps="29.97")


def test_mismatched_source_frame_rates_are_refused_before_anything_is_created():
    project, _, _ = make_project()
    mixed = sources(marcus_transcript=MARCUS_2997)
    plan = assembly_plan.build_multi_subject_plan(mixed, ORDER, handle_frames=0)
    handles, existing = handles_for(project)

    before = len(project.media_pool.created_timelines)
    with pytest.raises(builder.BuilderError, match="different frame rates"):
        builder.build_timeline(handles, plan, sources=mixed, project="doc", subject="haylee", mode="v003")

    # Nothing created, nothing imported, nothing appended, current timeline unmoved.
    assert len(project.media_pool.created_timelines) == before
    assert project.media_pool.imported == []
    assert project.media_pool.appended_clip_infos == []
    assert project.current_timeline is existing


def test_allow_fps_mismatch_does_not_unlock_a_mixed_rate_assembly():
    """allow_fps_mismatch is about conforming the whole assembly to a
    project rate on purpose. Two source rates inside one plan is incoherent
    at any project rate, so the flag must not reach it."""
    project, _, _ = make_project()
    mixed = sources(marcus_transcript=MARCUS_2997)
    plan = assembly_plan.build_multi_subject_plan(mixed, ORDER, handle_frames=0)
    handles, _ = handles_for(project)

    with pytest.raises(builder.BuilderError, match="different frame rates"):
        builder.build_timeline(handles, plan, sources=mixed, project="doc",
                               subject="haylee", mode="v003", allow_fps_mismatch=True)
    assert project.media_pool.created_timelines == []


def test_the_refusal_names_every_subject_and_its_rate():
    with pytest.raises(builder.BuilderError) as exc:
        builder.common_source_fps(sources(marcus_transcript=MARCUS_2997))
    message = str(exc.value)
    assert "haylee: 24" in message and "marcus: 29.97" in message
    assert "does not silently conform" in message


def test_a_decimal_expansion_of_the_same_rate_is_not_a_mismatch():
    same = {
        "haylee": SubjectSources(_transcript(HAYLEE_SOURCE, "01:00:00:00", fps="24000/1001"), HAYLEE_ANALYSIS, {}),
        "marcus": SubjectSources(_transcript(MARCUS_SOURCE, "00:00:00:00", fps="23.976"), MARCUS_ANALYSIS, {}),
    }
    assert builder.common_source_fps(same) == "24000/1001"


def test_a_dry_run_reports_the_same_refusal_the_build_would():
    mixed = sources(marcus_transcript=MARCUS_2997)
    plan = assembly_plan.build_multi_subject_plan(mixed, ORDER, handle_frames=0)
    with pytest.raises(builder.BuilderError, match="different frame rates"):
        builder.describe_build(plan, sources=mixed, project="doc", subject="haylee", mode="v003")


def test_only_the_subjects_actually_used_have_to_agree():
    """A loaded-but-unused subject can't misplace a cut, so it must not
    block a build either."""
    mixed = sources(marcus_transcript=MARCUS_2997)
    haylee_only = assembly_plan.build_multi_subject_plan(mixed, ["haylee.q01"], handle_frames=0)
    assert builder.common_source_fps(mixed, haylee_only) == "24"


# ---------------------------------------------------------------------------
# A cross-subject plan cannot be built through the single-subject path
# ---------------------------------------------------------------------------

def test_a_multi_subject_plan_refuses_a_single_transcript_build():
    project, _, _ = make_project()
    plan = assembly_plan.build_multi_subject_plan(sources(), ORDER, handle_frames=0)
    handles, _ = handles_for(project)

    with pytest.raises(builder.BuilderError, match="build_multi_subject_plan"):
        builder.build_timeline(handles, plan, transcript=HAYLEE_TRANSCRIPT, analysis=HAYLEE_ANALYSIS,
                               project="doc", subject="haylee", mode="v003")
    assert project.media_pool.created_timelines == []


def test_a_single_subject_plan_refuses_a_per_subject_build():
    project, _, _ = make_project()
    plan = assembly_plan.build_plan(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {}, ["haylee.q01"])
    handles, _ = handles_for(project)

    with pytest.raises(builder.BuilderError, match="single transcript"):
        builder.build_timeline(handles, plan, sources=sources(),
                               project="doc", subject="haylee", mode="v003")


def test_build_timeline_with_no_sources_at_all_is_an_error():
    project, _, _ = make_project()
    handles, _ = handles_for(project)
    plan = assembly_plan.build_plan(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {}, ["haylee.q01"])
    with pytest.raises(builder.BuilderError, match="No sources for this build"):
        builder.build_timeline(handles, plan, project="doc", subject="haylee", mode="v003")


# ---------------------------------------------------------------------------
# Multicam across subjects
# ---------------------------------------------------------------------------

def test_multicam_substitution_applies_per_source_file():
    project, haylee_item, _ = make_project()
    mc_item = FakeMediaPoolItem(path="", name="MC_MARCUS", expose_properties=False)
    project.media_pool.root.clips.append(mc_item)
    multicam = {"groups": [{"name": "M1", "multicam_clip_name": "MC_MARCUS",
                            "angles": [{"path": MARCUS_SOURCE, "label": "A"}]}]}

    result, _, _ = build_cross(project, multicam=multicam)

    infos = project.media_pool.appended_clip_infos[0]
    # Only marcus's clip is swapped for the multicam clip.
    assert [info["mediaPoolItem"] for info in infos] == [haylee_item, mc_item, haylee_item]
    assert result.multicam_clips_used == ["MC_MARCUS"]


def test_merge_multicam_folds_several_subjects_files_into_one():
    a = {"groups": [{"name": "A", "multicam_clip_name": "MC_A", "angles": [{"path": HAYLEE_SOURCE}]}]}
    b = {"groups": [{"name": "B", "multicam_clip_name": "MC_B", "angles": [{"path": MARCUS_SOURCE}]}]}
    merged = builder.merge_multicam([a, None, b])
    assert builder.multicam_clip_for_source(merged, HAYLEE_SOURCE) == "MC_A"
    assert builder.multicam_clip_for_source(merged, MARCUS_SOURCE) == "MC_B"
    assert builder.merge_multicam([None, {}]) is None


# ---------------------------------------------------------------------------
# CLI-level subject resolution: clear errors, never a partial build
# ---------------------------------------------------------------------------

def _project_on_disk(tmp_path, *, with_marcus=True, marcus_analysis=True):
    project_dir = tmp_path / "doc"
    reg = registry.load_project(project_dir, project_name="doc")
    registry.register_subject(reg, "haylee")
    haylee_dir = registry.subject_dir(project_dir, "haylee")
    (haylee_dir / "transcript.json").write_text(json.dumps(HAYLEE_TRANSCRIPT))
    (haylee_dir / "analysis.json").write_text(json.dumps(HAYLEE_ANALYSIS))
    if with_marcus:
        registry.register_subject(reg, "marcus")
        marcus_dir = registry.subject_dir(project_dir, "marcus")
        (marcus_dir / "transcript.json").write_text(json.dumps(MARCUS_TRANSCRIPT))
        if marcus_analysis:
            (marcus_dir / "analysis.json").write_text(json.dumps(MARCUS_ANALYSIS))
    registry.save_project(project_dir, reg)
    return project_dir, reg


def test_referenced_subjects_are_loaded_in_first_appearance_order(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path)
    loaded = cli._require_build_subjects(
        project_dir, reg, ORDER, "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
    )
    assert list(loaded) == ["haylee", "marcus"]
    assert loaded["marcus"][0]["source_file"] == MARCUS_SOURCE
    # The home subject's already-loaded pair is reused, not re-read.
    assert loaded["haylee"][0] is HAYLEE_TRANSCRIPT


def test_an_unregistered_subject_is_a_clear_error(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path)
    with pytest.raises(click.ClickException) as exc:
        cli._require_build_subjects(
            project_dir, reg, ["haylee.q01", "dana.q07"], "haylee",
            HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
        )
    assert "dana" in str(exc.value)
    assert "haylee, marcus" in str(exc.value)  # names what IS registered


def test_a_registered_subject_with_no_transcript_is_a_clear_error(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path, with_marcus=False)
    registry.register_subject(reg, "marcus")
    registry.save_project(project_dir, reg)
    with pytest.raises(click.ClickException, match="theodore transcribe"):
        cli._require_build_subjects(
            project_dir, reg, ORDER, "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
        )


def test_a_registered_subject_with_no_analysis_is_a_clear_error(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path, marcus_analysis=False)
    with pytest.raises(click.ClickException, match="theodore analyze"):
        cli._require_build_subjects(
            project_dir, reg, ORDER, "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
        )


def test_a_single_subject_sequence_loads_only_that_subject(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path)
    loaded = cli._require_build_subjects(
        project_dir, reg, ["haylee.q01", "haylee.q02"], "haylee",
        HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
    )
    assert list(loaded) == ["haylee"]


def test_unprefixed_ids_alone_stay_with_the_home_subject(tmp_path):
    """A pre-registry project's "s001" ids have no subject of their own;
    the only subject in play is the one named on the command line."""
    project_dir, reg = _project_on_disk(tmp_path)
    loaded = cli._require_build_subjects(
        project_dir, reg, ["s001", "s002"], "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
    )
    assert list(loaded) == ["haylee"]


def test_unprefixed_ids_mixed_with_another_subject_are_refused(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path)
    with pytest.raises(click.ClickException, match="unprefixed segment id"):
        cli._require_build_subjects(
            project_dir, reg, ["s001", "marcus.q01"], "haylee",
            HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
        )


def test_plan_for_sequence_routes_one_subject_down_the_single_transcript_path(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path)
    loaded = cli._require_build_subjects(
        project_dir, reg, ["haylee.q01"], "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
    )
    plan, source_kwargs = cli._plan_for_sequence(
        loaded, ["haylee.q01"], subject="haylee", transcript=HAYLEE_TRANSCRIPT,
        analysis=HAYLEE_ANALYSIS, trims={}, handle_frames=0,
    )
    assert set(source_kwargs) == {"transcript", "analysis"}
    assert plan[0].subject is None


def test_plan_for_sequence_routes_several_subjects_down_the_per_subject_path(tmp_path):
    project_dir, reg = _project_on_disk(tmp_path)
    loaded = cli._require_build_subjects(
        project_dir, reg, ORDER, "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
    )
    plan, source_kwargs = cli._plan_for_sequence(
        loaded, ORDER, subject="haylee", transcript=HAYLEE_TRANSCRIPT,
        analysis=HAYLEE_ANALYSIS, trims={}, handle_frames=0,
    )
    assert set(source_kwargs) == {"sources"}
    assert [c.subject for c in plan] == ["haylee", "marcus", "haylee"]


def test_plan_for_sequence_refuses_mixed_rates_as_a_click_error(tmp_path):
    """The CLI turns builder's refusal into a ClickException so the editor
    sees the reason, rather than a traceback or a failed Resolve call."""
    project_dir, reg = _project_on_disk(tmp_path)
    marcus_dir = registry.subject_dir(project_dir, "marcus")
    (marcus_dir / "transcript.json").write_text(json.dumps(MARCUS_2997))
    loaded = cli._require_build_subjects(
        project_dir, reg, ORDER, "haylee", HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS,
    )
    with pytest.raises(click.ClickException, match="different frame rates"):
        cli._plan_for_sequence(
            loaded, ORDER, subject="haylee", transcript=HAYLEE_TRANSCRIPT,
            analysis=HAYLEE_ANALYSIS, trims={}, handle_frames=0,
        )


def test_untrimming_a_cross_subject_list_keeps_every_clip():
    """A cross-subject version can now exist on disk, so `theodore untrim`
    has to follow it across subjects -- planning it against the home
    subject alone would silently drop the other subject's clips."""
    trimmed = {
        "haylee": SubjectSources(HAYLEE_TRANSCRIPT, HAYLEE_ANALYSIS, {
            "haylee.q01": {"original_start": 2.0, "original_end": 10.0,
                           "trimmed_start": 3.0, "trimmed_end": 9.0, "cuts": []},
        }),
        "marcus": SubjectSources(MARCUS_TRANSCRIPT, MARCUS_ANALYSIS, {}),
    }
    trimmed_plan = assembly_plan.build_multi_subject_plan(trimmed, ORDER, handle_frames=0)
    untrimmed_plan = assembly_plan.build_multi_subject_plan(sources(), ORDER, handle_frames=0)

    assert len(untrimmed_plan) == len(trimmed_plan) == 3
    assert untrimmed_plan[0].source_in_frame == 86400 + 48   # back to the full clean range
    assert trimmed_plan[0].source_in_frame == 86400 + 72
    assert (assembly_plan.total_runtime_frames(untrimmed_plan)
            > assembly_plan.total_runtime_frames(trimmed_plan))


def test_merged_base_trims_covers_every_involved_subject(tmp_path):
    from theodore.assembly import trim as assembly_trim

    project_dir, _ = _project_on_disk(tmp_path)
    assembly_trim.save_trims(
        {"haylee.q01": {"original_start": 2.0, "original_end": 10.0,
                        "trimmed_start": 3.0, "trimmed_end": 9.0, "cuts": []}},
        registry.subject_dir(project_dir, "haylee"),
    )
    assembly_trim.save_trims(
        {"marcus.q02": {"original_start": 12.5, "original_end": 20.0,
                        "trimmed_start": 13.0, "trimmed_end": 19.0, "cuts": []}},
        registry.subject_dir(project_dir, "marcus"),
    )
    merged = cli._merged_base_trims(project_dir, ["haylee", "marcus"])
    assert set(merged) == {"haylee.q01", "marcus.q02"}
