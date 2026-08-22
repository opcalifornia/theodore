from theodore.assembly import plan

_TRANSCRIPT = {
    "fps": "24",
    "start_timecode": "01:00:00:00",  # frame 86400
    "utterances": [
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 2.0},
        {"id": "u002", "speaker": "0", "start": 2.0, "end": 10.0},
        {"id": "u003", "speaker": "1", "start": 10.5, "end": 12.0},
        {"id": "u004", "speaker": "0", "start": 12.5, "end": 20.0},
    ],
}

_ANALYSIS = {
    "segments": [
        {"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002"},
        {"id": "s002", "answer_start_utterance": "u004", "answer_end_utterance": "u004"},
    ],
    "selects": [],
}

_TRIMS = {
    "s001": {"original_start": 2.0, "original_end": 10.0, "trimmed_start": 3.0, "trimmed_end": 9.0, "cuts": []},
    "s002": {"original_start": 12.5, "original_end": 20.0, "trimmed_start": 13.0, "trimmed_end": 19.0, "cuts": []},
}


def test_build_plan_places_clips_back_to_back_from_zero():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    assert len(result) == 2
    assert result[0].timeline_in_frame == 0
    assert result[0].timeline_out_frame == result[1].timeline_in_frame
    assert result[1].timeline_in_frame == result[0].duration_frames


def test_build_plan_source_frames_are_absolute_with_source_start_timecode():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001"], handle_frames=0)
    clip = result[0]
    # 01:00:00:00 @ 24fps = frame 86400; trimmed_start 3.0s = +72 frames = 86472.
    assert clip.source_in_frame == 86400 + 72
    assert clip.source_out_frame == 86400 + 9 * 24  # trimmed_end 9.0s = +216 frames


def test_build_plan_applies_handles_symmetrically():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001"], handle_frames=12)
    clip = result[0]
    # trimmed_start 3.0s (frame 86472) minus 12-frame handle = 86460;
    # trimmed_end 9.0s (frame 86616) plus 12-frame handle = 86628.
    assert clip.source_in_frame == 86460
    assert clip.source_out_frame == 86628


def test_build_plan_handles_clamped_to_original_bounds_not_beyond():
    # original_start=2.0s (frame 86448); trimmed_start=3.0s (frame 86472) is
    # only 24 frames in. A 48-frame handle must clamp at the original bound,
    # not overshoot into whatever precedes it in the source.
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001"], handle_frames=48)
    clip = result[0]
    assert clip.source_in_frame == 86400 + 48  # clamped to original_start (2.0s), not 86472-48


def test_build_plan_excludes_requested_segments():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0, excluded={"s001"})
    assert [c.segment_id for c in result] == ["s002"]
    assert result[0].timeline_in_frame == 0  # excluded clip doesn't leave a gap


def test_build_plan_falls_back_to_selects_clean_range_without_trim_entry():
    analysis = {
        "segments": [{"id": "s003", "answer_start_utterance": "u002", "answer_end_utterance": "u002"}],
        "selects": [{"segment_id": "s003", "clean_start_utterance": "u002", "clean_end_utterance": "u002"}],
    }
    result = plan.build_plan(_TRANSCRIPT, analysis, {}, ["s003"], handle_frames=0)
    assert len(result) == 1
    # u002 spans 2.0-10.0s -> frame 86400+48 to 86400+240.
    assert result[0].source_in_frame == 86448
    assert result[0].source_out_frame == 86640


def test_build_plan_skips_unresolvable_segment_ids():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s999"], handle_frames=0)
    assert result == []


def test_build_plan_never_produces_zero_or_negative_duration():
    trims = {"s001": {"original_start": 5.0, "original_end": 5.0, "trimmed_start": 5.0, "trimmed_end": 5.0, "cuts": []}}
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, trims, ["s001"], handle_frames=0)
    assert result[0].duration_frames >= 1


def test_total_runtime_frames_matches_last_clip_out():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    assert plan.total_runtime_frames(result) == result[-1].timeline_out_frame


def test_total_runtime_frames_empty_plan_is_zero():
    assert plan.total_runtime_frames([]) == 0


def test_clip_for_segment_finds_and_returns_none_for_missing():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    assert plan.clip_for_segment(result, "s002") is result[1]
    assert plan.clip_for_segment(result, "s999") is None


def test_target_duration_order_no_op_when_already_under_target():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    # both clips are 144 frames (6s @ 24fps) each, 288 total.
    order, dropped = plan.target_duration_order(["s001", "s002"], result, {}, target_frames=1000)
    assert order == ["s001", "s002"]
    assert dropped == []


def test_target_duration_order_drops_weakest_first():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    selects_by_id = {"s001": {"strength": 0.9}, "s002": {"strength": 0.2}}
    order, dropped = plan.target_duration_order(["s001", "s002"], result, selects_by_id, target_frames=200)
    assert dropped == ["s002"]
    assert order == ["s001"]


def test_target_duration_order_treats_unscored_as_zero_strength():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    selects_by_id = {"s001": {"strength": 0.9}}  # s002 unscored -- should drop before s001
    order, dropped = plan.target_duration_order(["s001", "s002"], result, selects_by_id, target_frames=200)
    assert dropped == ["s002"]
    assert order == ["s001"]


def test_target_duration_order_drops_everything_if_target_unreachable():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    order, dropped = plan.target_duration_order(["s001", "s002"], result, {}, target_frames=1)
    assert order == []
    assert dropped == ["s001", "s002"]


def test_target_duration_order_preserves_relative_order_of_kept_segments():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    # both equally weak (unscored); target still fits both -- nothing dropped.
    order, dropped = plan.target_duration_order(["s002", "s001"], result, {}, target_frames=288)
    assert order == ["s002", "s001"]
    assert dropped == []


def test_target_duration_order_zero_or_negative_target_is_a_no_op():
    result = plan.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=0)
    order, dropped = plan.target_duration_order(["s001", "s002"], result, {}, target_frames=0)
    assert order == ["s001", "s002"]
    assert dropped == []
