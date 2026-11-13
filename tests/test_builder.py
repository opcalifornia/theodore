import json

import pytest

from fake_resolve import (
    FakeAssemblyTimeline,
    FakeFolder,
    FakeMediaPoolItem,
    FakeProject,
)
from theodore.assembly import builder
from theodore.assembly import plan as assembly_plan
from theodore.resolve import markers as resolve_markers
from theodore.resolve.connection import ResolveHandles

SOURCE = "/media/interview.mov"

TRANSCRIPT = {
    "source_file": SOURCE,
    "fps": "24",
    "start_timecode": "01:00:00:00",  # frame 86400
    "speakers": {"0": "Haylee", "1": "Interviewer"},
    "utterances": [
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 2.0, "text": "Tell me about it.", "words": []},
        {"id": "u002", "speaker": "0", "start": 2.0, "end": 10.0, "text": "It was a long time ago.", "words": []},
        {"id": "u003", "speaker": "1", "start": 10.5, "end": 12.0, "text": "And then?", "words": []},
        {"id": "u004", "speaker": "0", "start": 12.5, "end": 20.0, "text": "Then everything changed.", "words": []},
    ],
}

ANALYSIS = {
    "segments": [
        {
            "id": "haylee.q01", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
            "question_text": "Tell me about it.", "question_label": "The beginning",
            "answer_summary": "She sets the scene.",
        },
        {
            "id": "haylee.q02", "answer_start_utterance": "u004", "answer_end_utterance": "u004",
            "question_text": None, "question_label": "The turn",
            "answer_summary": "Everything changed.",
        },
    ],
    "selects": [
        {
            "segment_id": "haylee.q01", "strength": 0.9,
            "clean_start_utterance": "u002", "clean_end_utterance": "u002",
            "best_line": "It was a long time ago.", "issues": [],
        },
        {
            "segment_id": "haylee.q02", "strength": 0.3,
            "clean_start_utterance": "u004", "clean_end_utterance": "u004",
            "best_line": "Then everything changed.", "issues": ["mic bump"],
        },
    ],
    "themes": [{"id": "t1", "label": "Memory"}],
    "theme_assignments": {"haylee.q01": ["t1"]},
}

TRIMS = {
    "haylee.q01": {
        "original_start": 2.0, "original_end": 10.0,
        "trimmed_start": 3.0, "trimmed_end": 9.0, "cuts": [[5.0, 6.0]],
    },
    "haylee.q02": {
        "original_start": 12.5, "original_end": 20.0,
        "trimmed_start": 13.0, "trimmed_end": 19.0, "cuts": [],
    },
}

ORDER = ["haylee.q01", "haylee.q02"]


def make_plan(order=ORDER, trims=TRIMS, handle_frames=0, excluded=None):
    return assembly_plan.build_plan(
        TRANSCRIPT, ANALYSIS, trims, order, handle_frames=handle_frames, excluded=excluded,
    )


def make_project(**kwargs):
    """A project whose media pool already holds the interview, filed in a bin."""
    item = FakeMediaPoolItem(path=SOURCE)
    bin_folder = FakeFolder(name="Interviews", clips=[item])
    root = FakeFolder(name="Master", subfolders=[bin_folder])
    project = FakeProject(root=root, **kwargs)
    return project, item


def handles_for(project, existing_timeline=None):
    existing = existing_timeline or FakeAssemblyTimeline("Editor's real cut")
    project.timelines.append(existing)
    project.current_timeline = existing
    return ResolveHandles(resolve=None, project_manager=None, project=project, timeline=existing), existing


def build(project, plan=None, **kwargs):
    handles, existing = handles_for(project)
    kwargs.setdefault("project", "doc")
    kwargs.setdefault("subject", "haylee")
    kwargs.setdefault("mode", "chronological")
    kwargs.setdefault("transcript", TRANSCRIPT)
    kwargs.setdefault("analysis", ANALYSIS)
    kwargs.setdefault("trims", TRIMS)
    kwargs.setdefault("timestamp", "20250101_120000")
    result = builder.build_timeline(handles, plan if plan is not None else make_plan(), **kwargs)
    return result, existing


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def test_timeline_name_shape_and_sanitization():
    name = builder.timeline_name("Doc Project", "haylee/2", "narrative", timestamp="20250101_120000")
    assert name == "THEODORE_Doc_Project_haylee_2_narrative_20250101_120000"


def test_timeline_name_generates_a_timestamp_when_not_given():
    name = builder.timeline_name("p", "s", "m")
    assert name.startswith("THEODORE_p_s_m_")
    assert len(name.rsplit("_", 2)[-2]) == 8  # YYYYmmdd


# ---------------------------------------------------------------------------
# Dry run -- pure, touches nothing
# ---------------------------------------------------------------------------

def test_describe_build_lists_every_clip_with_timecodes():
    text = builder.describe_build(
        make_plan(), project="doc", subject="haylee", mode="chronological",
        transcript=TRANSCRIPT, analysis=ANALYSIS, handle_frames=12,
    )
    assert "DRY RUN" in text
    assert "THEODORE_doc_haylee_chronological_<timestamp>" in text
    # q01 trimmed_start 3.0s -> absolute frame 86400+72 -> 01:00:03:00
    assert "01:00:03:00" in text
    assert "haylee.q01" in text and "haylee.q02" in text
    assert "The beginning" in text
    assert "Handles:       12 frames each side" in text
    # Total runtime: 6s + 6s = 288 frames
    assert "288 frames" in text


def test_describe_build_includes_rationale_and_needs_no_resolve():
    text = builder.describe_build(
        make_plan(), project="doc", subject="haylee", mode="narrative",
        transcript=TRANSCRIPT, analysis=ANALYSIS,
        rationale="Open on the turn, then explain the beginning.",
    )
    assert "Rationale (narrative)" in text
    assert "Open on the turn" in text


def test_describe_build_handles_empty_plan():
    text = builder.describe_build(
        [], project="doc", subject="haylee", mode="strength", transcript=TRANSCRIPT,
    )
    assert "the plan is empty" in text


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_build_creates_a_new_timeline_and_never_touches_the_existing_one():
    project, _ = make_project()
    result, existing = build(project)

    assert result.timeline_name == "THEODORE_doc_haylee_chronological_20250101_120000"
    assert result.timeline is not existing
    assert result.timeline.GetName() == result.timeline_name
    # The editor's cut is untouched: no clips, no markers.
    assert existing.tracks["video"][1] == []
    assert existing.markers == {}
    assert len(project.media_pool.created_timelines) == 1


def test_build_appends_media_relative_in_out_frames():
    project, item = make_project()
    plan = make_plan(handle_frames=0)
    result, _ = build(project, plan)

    infos = project.media_pool.appended_clip_infos[0]
    assert len(infos) == 2
    # The clip's Start TC is 01:00:00:00 = frame 86400, so plan.py's absolute
    # source frames must come back down into the media's own 0-based space.
    assert infos[0]["startFrame"] == plan[0].source_in_frame - 86400
    assert infos[0]["endFrame"] == plan[0].source_out_frame - 86400
    assert infos[0]["startFrame"] == 72   # trimmed_start 3.0s @ 24fps
    assert infos[0]["endFrame"] == 216    # trimmed_end 9.0s @ 24fps
    assert all(info["mediaPoolItem"] is item for info in infos)
    assert result.clips_appended == 2 == result.clips_planned


def test_build_frame_origin_absolute_passes_plan_frames_through():
    project, _ = make_project()
    plan = make_plan(handle_frames=0)
    # A very long media item so the absolute frames are still in bounds.
    project.media_pool.root.subfolders[0].clips[0].frames = 200000
    build(project, plan, frame_origin="absolute")

    infos = project.media_pool.appended_clip_infos[0]
    assert infos[0]["startFrame"] == plan[0].source_in_frame == 86472


def test_build_frame_origin_media_uses_the_transcripts_start_timecode():
    project, _ = make_project()
    # Clip reports no usable Start TC; the transcript's value must be used.
    project.media_pool.root.subfolders[0].clips[0].start_tc = ""
    plan = make_plan(handle_frames=0)
    build(project, plan, frame_origin="media")
    infos = project.media_pool.appended_clip_infos[0]
    assert infos[0]["startFrame"] == plan[0].source_in_frame - 86400


def test_build_makes_the_new_timeline_current_and_repoints_the_handles():
    project, _ = make_project()
    handles, existing = handles_for(project)
    result = builder.build_timeline(
        handles, make_plan(), project="doc", subject="haylee", mode="chronological",
        transcript=TRANSCRIPT, analysis=ANALYSIS, trims=TRIMS, timestamp="20250101_120000",
    )
    assert project.current_timeline is result.timeline
    assert handles.timeline is result.timeline
    assert handles.timeline is not existing


def test_excluded_segments_do_not_reach_the_timeline():
    project, _ = make_project()
    plan = make_plan(excluded={"haylee.q01"})
    result, _ = build(project, plan)

    infos = project.media_pool.appended_clip_infos[0]
    assert len(infos) == 1
    assert result.clips_planned == 1
    segment_ids = [
        json.loads(m["customData"])["segment_id"] for m in result.timeline.markers.values()
    ]
    assert segment_ids == ["haylee.q02"]


# ---------------------------------------------------------------------------
# Failure detection -- Resolve reports these by return value, not exception
# ---------------------------------------------------------------------------

def test_create_empty_timeline_failure_raises_a_specific_error():
    project, _ = make_project()
    project.media_pool.create_timeline_returns = None
    with pytest.raises(builder.BuilderError, match="CreateEmptyTimeline"):
        build(project)


def test_append_returning_nothing_raises_rather_than_reporting_success():
    project, _ = make_project()
    project.media_pool.append_returns = []
    with pytest.raises(builder.BuilderError, match="appended nothing"):
        build(project)


def test_partial_append_is_caught_by_counting_the_timelines_own_items():
    project, _ = make_project()
    project.media_pool.drop_appends_after = 1  # only the first clip lands
    with pytest.raises(builder.BuilderError, match="holds 1 clip"):
        build(project)


def test_clamped_clip_duration_raises():
    project, _ = make_project()
    project.media_pool.duration_delta = -50  # Resolve shortened every edit
    with pytest.raises(builder.BuilderError, match="frames but the plan asked for"):
        build(project)


def test_inclusive_endframe_off_by_one_warns_but_still_builds():
    project, _ = make_project(inclusive_end=True)
    result, _ = build(project)
    assert result.clips_appended == 2
    assert any("inclusive" in w for w in result.warnings)


def test_refuses_to_append_when_resolve_did_not_switch_timelines():
    # The safety rule: AppendToTimeline writes to the CURRENT timeline. If
    # Resolve didn't switch, appending would write the assembly into the
    # editor's real cut.
    project, _ = make_project()
    project.honour_set_current_timeline = False
    project.set_current_timeline_returns = False

    original_create = project.media_pool.CreateEmptyTimeline

    def create_without_switching(name):
        timeline = original_create(name)
        project.current_timeline = None  # undo the fake's auto-switch
        return timeline

    project.media_pool.CreateEmptyTimeline = create_without_switching

    with pytest.raises(builder.BuilderError, match="Refusing to append"):
        build(project)
    # Nothing was appended anywhere.
    assert project.media_pool.appended_clip_infos == []


def test_empty_plan_raises():
    project, _ = make_project()
    with pytest.raises(builder.BuilderError, match="plan is empty"):
        build(project, [])


def test_missing_source_file_key_raises():
    project, _ = make_project()
    transcript = {k: v for k, v in TRANSCRIPT.items() if k != "source_file"}
    with pytest.raises(builder.BuilderError, match="no 'source_file'"):
        build(project, transcript=transcript)


def test_source_frames_before_the_media_start_raise():
    project, _ = make_project()
    # Media starts an hour LATER than the transcript claims, so every planned
    # frame lands before the start of the clip.
    project.media_pool.root.subfolders[0].clips[0].start_tc = "02:00:00:00"
    with pytest.raises(builder.BuilderError, match="before the start of the media"):
        build(project)


def test_unreadable_track_list_warns_instead_of_falsely_verifying():
    project, _ = make_project()
    original_create = project.media_pool.CreateEmptyTimeline

    def create_blind(name):
        timeline = original_create(name)
        timeline.has_get_item_list = False
        return timeline

    project.media_pool.CreateEmptyTimeline = create_blind
    result, _ = build(project)
    assert any("could NOT be verified" in w for w in result.warnings)


def test_unknown_frame_origin_is_a_programming_error():
    project, _ = make_project()
    with pytest.raises(ValueError, match="frame_origin"):
        build(project, frame_origin="nonsense")


# ---------------------------------------------------------------------------
# Frame rate guard
# ---------------------------------------------------------------------------

def test_frame_rate_mismatch_refuses_to_build():
    project, _ = make_project(timeline_fps="25")
    with pytest.raises(builder.BuilderError, match="frame rate"):
        build(project)
    assert project.media_pool.created_timelines == []


def test_frame_rate_mismatch_can_be_overridden_but_warns():
    project, _ = make_project(timeline_fps="25")
    result, _ = build(project, allow_fps_mismatch=True)
    assert any("frame rate" in w for w in result.warnings)
    assert result.clips_appended == 2


def test_decimal_expansion_of_an_ntsc_rate_is_not_a_mismatch():
    assert builder._fps_matches("23.976023976023978", "24000/1001")
    assert not builder._fps_matches("24", "23.976")


def test_unreadable_frame_rate_warns_and_proceeds():
    project, _ = make_project(timeline_fps="")
    result, _ = build(project)
    assert any("timelineFrameRate" in w for w in result.warnings)
    assert result.clips_appended == 2


# ---------------------------------------------------------------------------
# Find-or-import
# ---------------------------------------------------------------------------

def test_existing_media_pool_clip_is_reused_not_reimported():
    project, item = make_project()
    result, _ = build(project)
    assert project.media_pool.imported == []
    assert result.reused_paths == [SOURCE]
    assert all(info["mediaPoolItem"] is item for info in project.media_pool.appended_clip_infos[0])


def test_media_is_imported_when_not_already_in_the_pool(tmp_path):
    source = tmp_path / "interview.mov"
    source.write_bytes(b"not really a movie")
    transcript = {**TRANSCRIPT, "source_file": str(source)}

    project = FakeProject(root=FakeFolder(name="Master"))
    result, _ = build(project, transcript=transcript)

    assert project.media_pool.imported == [str(source)]
    assert result.imported_paths == [str(source)]
    assert result.clips_appended == 2


def test_import_failure_raises_a_specific_error(tmp_path):
    source = tmp_path / "interview.mov"
    source.write_bytes(b"x")
    project = FakeProject(root=FakeFolder(name="Master"))
    project.media_pool.import_returns = []
    with pytest.raises(builder.BuilderError, match="refused to import"):
        build(project, transcript={**TRANSCRIPT, "source_file": str(source)})


def test_media_missing_from_disk_and_from_the_pool_raises():
    project = FakeProject(root=FakeFolder(name="Master"))
    with pytest.raises(builder.BuilderError, match="not found on disk"):
        build(project)


def test_find_media_pool_item_walks_nested_bins():
    inner = FakeFolder(name="Day 2", clips=[FakeMediaPoolItem(path=SOURCE)])
    outer = FakeFolder(name="Interviews", subfolders=[inner])
    root = FakeFolder(name="Master", subfolders=[outer])
    project = FakeProject(root=root)
    found = builder.find_media_pool_item(project.GetMediaPool(), SOURCE)
    assert found is inner.clips[0]


def test_find_media_pool_item_falls_back_to_filename_match():
    item = FakeMediaPoolItem(path="/other/mount/interview.mov")
    project = FakeProject(root=FakeFolder(name="Master", clips=[item]))
    assert builder.find_media_pool_item(project.GetMediaPool(), SOURCE) is item


def test_find_media_pool_item_returns_none_when_absent():
    project = FakeProject(root=FakeFolder(name="Master", clips=[FakeMediaPoolItem(path="/x/other.mov")]))
    assert builder.find_media_pool_item(project.GetMediaPool(), SOURCE) is None


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

def test_markers_land_at_the_edit_points_with_theodore_custom_data():
    project, _ = make_project()
    plan = make_plan()
    result, _ = build(project, plan)

    timeline = result.timeline
    assert result.markers_written == 2
    assert sorted(timeline.markers) == [c.timeline_in_frame for c in plan]

    first = timeline.markers[0]
    data = json.loads(first["customData"])
    assert data["source"] == resolve_markers.MARKER_SOURCE_TAG
    assert data["segment_id"] == "haylee.q01"
    assert data["strength"] == 0.9
    assert data["theme_ids"] == ["t1"]
    assert data["order_index"] == 0
    assert data["source_in_frame"] == plan[0].source_in_frame
    assert data["timeline_out_frame"] == plan[0].timeline_out_frame
    assert first["duration"] == plan[0].duration_frames


def test_marker_colors_follow_the_established_scheme():
    project, _ = make_project()
    result, _ = build(project)
    colors = [m["color"] for _, m in sorted(result.timeline.markers.items())]
    # q01 strength 0.9 -> strong; q02 has question_text None -> volunteered.
    assert colors == [resolve_markers.COLOR_STRONG, resolve_markers.COLOR_VOLUNTEERED]


def test_marker_notes_summarize_the_trim():
    markers = builder.plan_edit_markers(make_plan(), TRANSCRIPT, ANALYSIS, trims=TRIMS)
    assert "Trim: -1.0s head, -1.0s tail, 1 interior cut" in markers[0].note


def test_narrative_rationale_lands_in_the_first_markers_note():
    project, _ = make_project()
    result, _ = build(project, mode="narrative", rationale="Start with the turn for a cold open.")

    first = result.timeline.markers[min(result.timeline.markers)]
    assert "Start with the turn for a cold open." in first["note"]
    assert json.loads(first["customData"])["rationale"] == "Start with the turn for a cold open."
    # ...and only on the first marker.
    others = [m for f, m in result.timeline.markers.items() if f != min(result.timeline.markers)]
    assert all("cold open" not in m["note"] for m in others)


def test_markers_can_be_skipped():
    project, _ = make_project()
    result, _ = build(project, write_markers=False)
    assert result.timeline.markers == {}
    assert result.markers_written == 0


def test_rejected_markers_are_counted_not_swallowed():
    project, _ = make_project()
    original_create = project.media_pool.CreateEmptyTimeline

    def create_rejecting(name):
        timeline = original_create(name)
        timeline.add_marker_returns = False
        return timeline

    project.media_pool.CreateEmptyTimeline = create_rejecting
    result, _ = build(project)
    assert result.markers_written == 0
    assert result.markers_failed == 2
    assert any("rejected by Resolve" in w for w in result.warnings)


def test_plan_edit_markers_is_pure_and_needs_no_resolve():
    markers = builder.plan_edit_markers(make_plan(), TRANSCRIPT, ANALYSIS, mode="strength")
    assert [m.timeline_frame for m in markers] == [0, 144]
    assert markers[0].name.startswith("01. ")
    assert markers[0].custom_data["assembly_mode"] == "strength"


def test_plan_edit_markers_tolerates_a_segment_missing_from_analysis():
    plan = make_plan()
    markers = builder.plan_edit_markers(plan, TRANSCRIPT, {"segments": [], "selects": []})
    assert len(markers) == 2
    assert "not found in analysis.json" in markers[0].note


def test_write_edit_markers_offsets_when_clips_start_after_the_timeline_start():
    timeline = FakeAssemblyTimeline("t", start_frame=86400)
    markers = builder.plan_edit_markers(make_plan(), TRANSCRIPT, ANALYSIS)
    stats = builder.write_edit_markers(timeline, markers, origin_frame=10)
    assert stats["written"] == 2
    assert sorted(timeline.markers) == [10, 154]


def test_write_edit_markers_skips_negative_frames():
    timeline = FakeAssemblyTimeline("t")
    markers = builder.plan_edit_markers(make_plan(), TRANSCRIPT, ANALYSIS)
    stats = builder.write_edit_markers(timeline, markers, origin_frame=-1000)
    assert stats["written"] == 0 and stats["failed"] == 2


# ---------------------------------------------------------------------------
# Audio-only sources
# ---------------------------------------------------------------------------

def test_audio_only_source_verifies_against_the_audio_track():
    project, _ = make_project(video=False, audio=True)
    result, _ = build(project)
    assert result.clips_appended == 2
    assert result.timeline.tracks["video"][1] == []
    assert len(result.timeline.tracks["audio"][1]) == 2


# ---------------------------------------------------------------------------
# Multicam (optional, best-effort)
# ---------------------------------------------------------------------------

MULTICAM = {
    "groups": [{
        "name": "C003",
        "multicam_clip_name": "MC_C003",
        "angles": [{"path": SOURCE, "label": "A"}, {"path": "/media/b_cam.mov", "label": "B"}],
    }],
}


def test_multicam_clip_is_preferred_when_it_is_in_the_media_pool():
    project, source_item = make_project()
    mc_item = FakeMediaPoolItem(path="", name="MC_C003", expose_properties=False)
    project.media_pool.root.clips.append(mc_item)

    result, _ = build(project, multicam=MULTICAM)

    infos = project.media_pool.appended_clip_infos[0]
    assert all(info["mediaPoolItem"] is mc_item for info in infos)
    assert result.multicam_clips_used == ["MC_C003"]


def test_multicam_clip_missing_from_the_pool_falls_back_with_a_warning():
    project, source_item = make_project()
    result, _ = build(project, multicam=MULTICAM)

    infos = project.media_pool.appended_clip_infos[0]
    assert all(info["mediaPoolItem"] is source_item for info in infos)
    assert result.multicam_clips_used == []
    assert any("MC_C003" in w for w in result.warnings)


def test_multicam_lookup_tolerates_junk():
    assert builder.multicam_clip_for_source(None, SOURCE) is None
    assert builder.multicam_clip_for_source({}, SOURCE) is None
    assert builder.multicam_clip_for_source({"groups": "nope"}, SOURCE) is None
    assert builder.multicam_clip_for_source({"groups": [{"angles": [{"path": SOURCE}]}]}, SOURCE) is None


def test_load_multicam_returns_none_when_absent_or_unparseable(tmp_path):
    assert builder.load_multicam(tmp_path / "nope.json") is None
    bad = tmp_path / "multicam.json"
    bad.write_text("{not json")
    assert builder.load_multicam(bad) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps(MULTICAM))
    assert builder.load_multicam(good)["groups"][0]["name"] == "C003"


# ---------------------------------------------------------------------------
# untrim
# ---------------------------------------------------------------------------

def test_build_untrimmed_plan_ignores_trims_and_matches_an_empty_trims_build():
    untrimmed = builder.build_untrimmed_plan(TRANSCRIPT, ANALYSIS, ORDER, handle_frames=0)
    trimmed = make_plan(handle_frames=0)
    assert untrimmed == assembly_plan.build_plan(TRANSCRIPT, ANALYSIS, {}, ORDER, handle_frames=0)
    # The untrimmed cut is longer than the trimmed one.
    assert assembly_plan.total_runtime_frames(untrimmed) > assembly_plan.total_runtime_frames(trimmed)
    assert untrimmed[0].source_in_frame == 86400 + 48  # u002 starts at 2.0s, untrimmed


def test_format_result_summarizes_a_build():
    project, _ = make_project()
    result, _ = build(project)
    text = builder.format_result(result)
    assert "THEODORE_doc_haylee_chronological_20250101_120000" in text
    assert "clips:    2/2" in text
    assert "markers:  2 written" in text
