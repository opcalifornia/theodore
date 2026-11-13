import json
from fractions import Fraction

import pytest

from theodore.resolve import multicam as mc


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def media(path, *, tc="01:00:00:00", fps="24000/1001", duration=600.0,
          video=True, audio=True):
    return {
        "path": path,
        "fps": fps,
        "duration_seconds": duration,
        "start_timecode": tc,
        "has_video": video,
        "has_audio": audio,
        "audio_hash": "",
    }


def names(plan):
    return [g.multicam_clip_name for g in plan.groups]


def grouped_files(plan):
    return [sorted(a.name for a in g.angles) for g in plan.groups]


def skip_reason(plan, filename):
    for item in plan.skipped:
        if item["path"].endswith(filename):
            return item["reason"]
    return None


# --------------------------------------------------------------------------
# the core requirement: overlapping timecode groups, non-overlapping doesn't
# --------------------------------------------------------------------------

def test_two_overlapping_video_files_are_grouped():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:12", duration=598.0),
    ], "haylee")

    assert len(plan.groups) == 1
    assert names(plan) == ["haylee_multicam_1"]
    assert grouped_files(plan) == [["cam_a.mov", "cam_b.mov"]]
    assert plan.ambiguous == []
    assert plan.ungrouped == []


def test_three_mutually_overlapping_files_form_one_group():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=599.0),
        media("/m/cam_c.mov", tc="01:00:00:10", duration=597.0),
    ], "haylee")

    assert len(plan.groups) == 1
    assert grouped_files(plan) == [["cam_a.mov", "cam_b.mov", "cam_c.mov"]]


def test_non_overlapping_files_are_not_grouped():
    # Two consecutive hours -- no shared moment at all.
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="02:00:00:00", duration=600.0),
    ], "haylee")

    assert plan.groups == []
    assert sorted(plan.ungrouped) == ["/m/cam_a.mov", "/m/cam_b.mov"]
    assert any("overlap" in n for n in plan.notes)


def test_barely_touching_files_are_not_grouped():
    # cam_b starts 9 minutes into cam_a's 10 minutes: only ~10% overlap.
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:09:00:00", duration=600.0),
    ], "haylee")

    assert plan.groups == []


def test_short_clip_fully_inside_a_long_one_is_refused():
    # 10 minutes sitting inside 60 passes containment-of-shorter (100%) but
    # fails coincidence-of-union (~17%): 50 of those 60 minutes have no
    # second angle, so this is not one coincident recording.
    plan = mc.plan_multicam([
        media("/m/long_a.mov", tc="01:00:00:00", duration=3600.0),
        media("/m/short_b.mov", tc="01:20:00:00", duration=600.0),
    ], "haylee")

    containment, coincidence = mc.overlap_scores(*[
        mc._to_angle(e)[0] for e in [
            media("/m/long_a.mov", tc="01:00:00:00", duration=3600.0),
            media("/m/short_b.mov", tc="01:20:00:00", duration=600.0),
        ]
    ])
    assert containment >= mc.MIN_CONTAINMENT_OF_SHORTER  # containment alone would accept
    assert coincidence < mc.MIN_OVERLAP_OF_UNION         # coincidence rejects it
    assert plan.groups == []


# --------------------------------------------------------------------------
# 00:00:00:00 is the absence of a signal, never sync evidence
# --------------------------------------------------------------------------

def test_zero_timecode_files_are_never_grouped():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="00:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="00:00:00:00", duration=600.0),
    ], "haylee")

    assert plan.groups == []
    assert len(plan.skipped) == 2
    for f in ("cam_a.mov", "cam_b.mov"):
        assert "no-embedded-timecode" in skip_reason(plan, f)


def test_zero_timecode_file_is_not_grouped_with_a_timecoded_one():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="00:00:00:00", duration=600.0),
    ], "haylee")

    assert plan.groups == []
    assert skip_reason(plan, "cam_b.mov") is not None
    assert plan.ungrouped == ["/m/cam_a.mov"]


def test_zero_timecode_is_skipped_regardless_of_separator():
    # "00:00:00;00" is the same non-signal as "00:00:00:00".
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="00:00:00;00", fps="30000/1001", duration=600.0),
        media("/m/cam_b.mov", tc="00:00:00;00", fps="30000/1001", duration=600.0),
    ], "haylee")

    assert plan.groups == []
    assert len(plan.skipped) == 2


# --------------------------------------------------------------------------
# exclusions
# --------------------------------------------------------------------------

def test_audio_only_files_are_excluded():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:00", duration=600.0),
        media("/m/lav.wav", tc="01:00:00:00", fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")

    assert len(plan.groups) == 1
    assert grouped_files(plan) == [["cam_a.mov", "cam_b.mov"]]
    assert "audio-only" in skip_reason(plan, "lav.wav")


# --------------------------------------------------------------------------
# camera-to-external-audio sync recommendations
# --------------------------------------------------------------------------

def test_external_audio_recommended_for_every_camera_file():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=598.0),
        media("/m/lav.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")

    assert len(plan.external_audio) == 1
    rec = plan.external_audio[0]
    assert rec["path"] == "/m/lav.wav"
    assert rec["camera_files"] == ["/m/cam_a.mov", "/m/cam_b.mov"]
    assert rec["ambiguous"] is False
    assert rec["has_embedded_timecode"] is False
    assert rec["start_timecode"] is None


def test_external_audio_recommended_even_with_a_single_ungrouped_camera():
    # No multicam grouping possible with only one camera file -- the audio
    # still needs to be synced to it, so this must not depend on a group
    # having formed.
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/lav.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")

    assert plan.groups == []
    assert len(plan.external_audio) == 1
    assert plan.external_audio[0]["camera_files"] == ["/m/cam_a.mov"]


def test_no_external_audio_recommendation_without_any_camera_file():
    recs = mc.plan_audio_sync([
        media("/m/lav.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")
    assert recs == []


def test_no_external_audio_recommendation_without_any_audio_file():
    recs = mc.plan_audio_sync([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
    ], "haylee")
    assert recs == []


def test_audio_file_with_no_timecode_recommends_waveform_sync():
    recs = mc.plan_audio_sync([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/lav.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")
    assert len(recs) == 1
    assert "Based on Waveform" in recs[0]["note"]
    assert "Based on Timecode" not in recs[0]["note"]


def test_audio_file_with_real_timecode_mentions_timecode_sync_option():
    recs = mc.plan_audio_sync([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/boom.wav", tc="01:00:00:00", fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["has_embedded_timecode"] is True
    assert rec["start_timecode"] == "01:00:00:00"
    assert "Based on Timecode" in rec["note"]
    assert "Based on Waveform" in rec["note"]  # still offered as the no-jam-sync fallback


def test_multiple_external_audio_files_are_flagged_ambiguous_not_guessed():
    recs = mc.plan_audio_sync([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/lav1.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=300.0,
              video=False, audio=True),
        media("/m/lav2.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=300.0,
              video=False, audio=True),
    ], "haylee")

    assert len(recs) == 2
    assert {r["path"] for r in recs} == {"/m/lav1.wav", "/m/lav2.wav"}
    for rec in recs:
        assert rec["ambiguous"] is True
        assert rec["camera_files"] == ["/m/cam_a.mov"]
        assert "no signal to tell which belongs with which take" in rec["note"]


def test_audio_only_file_with_zero_or_missing_duration_is_ignored():
    recs = mc.plan_audio_sync([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/broken.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=0.0,
              video=False, audio=True),
    ], "haylee")
    assert recs == []


def test_format_plan_reports_external_audio():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/lav.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")
    text = mc.format_plan(plan)
    assert "External audio:  lav.wav" in text
    assert "cam_a.mov" in text
    assert "Auto Sync Audio" not in text or "Based on Waveform" in text


def test_external_audio_round_trips_through_json(tmp_path):
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/lav.wav", tc=mc.NO_TIMECODE_SENTINEL, fps="0/1", duration=600.0,
              video=False, audio=True),
    ], "haylee")
    out = mc.save_multicam(plan, tmp_path)
    data = json.loads(out.read_text())
    assert data["external_audio"][0]["path"] == "/m/lav.wav"
    assert mc.load_multicam(tmp_path)["external_audio"][0]["path"] == "/m/lav.wav"


def test_single_video_file_produces_no_groups():
    plan = mc.plan_multicam([media("/m/cam_a.mov", tc="01:00:00:00")], "haylee")

    assert plan.groups == []
    assert plan.ungrouped == ["/m/cam_a.mov"]
    assert any("single camera is not" in n for n in plan.notes)


def test_no_media_at_all():
    plan = mc.plan_multicam([], "haylee")
    assert plan.groups == []
    assert plan.notes


def test_unusable_entries_are_skipped_with_a_reason():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00"),
        media("/m/bad_tc.mov", tc="not-a-timecode"),
        media("/m/zero_len.mov", tc="01:00:00:00", duration=0.0),
        media("/m/no_rate.mov", tc="01:00:00:00", fps="0/1"),
        "this is not a dict",
    ], "haylee")

    assert plan.groups == []
    assert "unreadable start timecode" in skip_reason(plan, "bad_tc.mov")
    assert "nothing to overlap" in skip_reason(plan, "zero_len.mov")
    assert "not a real rate" in skip_reason(plan, "no_rate.mov")
    assert any("not an object" in s["reason"] for s in plan.skipped)


# --------------------------------------------------------------------------
# frame rates
# --------------------------------------------------------------------------

def test_differing_fps_files_are_compared_in_shared_label_seconds():
    # A 23.976 A-cam and a 25 fps B-cam, both jam-synced to 01:00:00:00.
    # Comparing raw frame counts would be meaningless (86400 vs 90000);
    # comparing real elapsed seconds would invent a 3.6s NTSC offset.
    # Label seconds put both at exactly 3600.
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", fps="24000/1001", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:00", fps="25", duration=600.0),
    ], "haylee")

    assert len(plan.groups) == 1
    assert plan.groups[0].mixed_frame_rates is True
    assert any("mixes frame rates" in n for n in plan.notes)
    assert "conform" in mc.format_plan(plan)


def test_label_seconds_put_same_timecode_at_same_instant_across_rates():
    a = mc._to_angle(media("/m/a.mov", tc="01:00:00:00", fps="24000/1001"))[0]
    b = mc._to_angle(media("/m/b.mov", tc="01:00:00:00", fps="25"))[0]

    assert a.start == b.start == Fraction(3600)
    assert isinstance(a.start, Fraction) and isinstance(b.start, Fraction)


def test_same_rate_group_is_not_flagged_mixed():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", fps="25", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:00", fps="25", duration=600.0),
    ], "haylee")

    assert plan.groups[0].mixed_frame_rates is False


def test_drop_frame_timecode_is_read_correctly():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00;00", fps="30000/1001", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00;10", fps="30000/1001", duration=599.0),
    ], "haylee")

    assert len(plan.groups) == 1


# --------------------------------------------------------------------------
# ambiguity: never guess silently
# --------------------------------------------------------------------------

def test_chain_overlap_is_reported_ambiguous_not_guessed():
    # A overlaps B, B overlaps C, A does not overlap C. A Resolve multicam
    # clip syncs ALL its angles, so {A,B,C} would be wrong -- and choosing
    # {A,B} over {B,C} would be a silent coin flip.
    # Each file runs 600s, staggered 90s apart. Neighbours overlap 510/600
    # (85%, over the bar); A vs C overlap only 420/600 (70%, under it).
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:01:30:00", duration=600.0),
        media("/m/cam_c.mov", tc="01:03:00:00", duration=600.0),
    ], "haylee")

    assert plan.groups == []
    assert plan.ambiguous, "the chain must be reported, not silently resolved"
    reported = {f for item in plan.ambiguous for f in item["files"]}
    assert reported == {"cam_a.mov", "cam_b.mov", "cam_c.mov"}
    assert all(item["competing_with"] for item in plan.ambiguous)
    assert "AMBIGUOUS" in mc.format_plan(plan)


def test_unambiguous_group_survives_alongside_an_unrelated_file():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=600.0),
        media("/m/later.mov", tc="05:00:00:00", duration=600.0),
    ], "haylee")

    assert grouped_files(plan) == [["cam_a.mov", "cam_b.mov"]]
    assert plan.ungrouped == ["/m/later.mov"]
    assert plan.ambiguous == []

    # every file the editor handed in must be accounted for in the report,
    # not silently dropped from it
    report = mc.format_plan(plan)
    for filename in ("cam_a.mov", "cam_b.mov", "later.mov"):
        assert filename in report
    assert "builds as a plain clip" in report


def test_two_independent_groups_are_numbered_by_start_time():
    plan = mc.plan_multicam([
        media("/m/late_a.mov", tc="05:00:00:00", duration=600.0),
        media("/m/late_b.mov", tc="05:00:00:05", duration=600.0),
        media("/m/early_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/early_b.mov", tc="01:00:00:05", duration=600.0),
    ], "haylee")

    assert names(plan) == ["haylee_multicam_1", "haylee_multicam_2"]
    assert grouped_files(plan) == [
        ["early_a.mov", "early_b.mov"],
        ["late_a.mov", "late_b.mov"],
    ]


def test_too_many_candidates_refuses_rather_than_hanging():
    entries = [
        media(f"/m/cam_{i}.mov", tc="01:00:00:00", duration=600.0)
        for i in range(mc._MAX_CANDIDATES_FOR_GROUPING + 1)
    ]
    plan = mc.plan_multicam(entries, "haylee")

    assert plan.groups == []
    assert any("more than this pass will group" in n for n in plan.notes)


# --------------------------------------------------------------------------
# naming + determinism
# --------------------------------------------------------------------------

def test_clip_names_are_deterministic_and_subject_scoped():
    entries = [
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=600.0),
    ]
    first = mc.plan_multicam(entries, "haylee")
    second = mc.plan_multicam(list(reversed(entries)), "haylee")

    assert names(first) == names(second) == ["haylee_multicam_1"]
    assert grouped_files(first) == grouped_files(second)


def test_clip_name_sanitizes_awkward_subject_ids():
    assert mc.clip_name_for("subject one/two", 1) == "subject_one_two_multicam_1"
    assert mc.clip_name_for("", 2) == "subject_multicam_2"


# --------------------------------------------------------------------------
# the on-disk contract builder.py reads
# --------------------------------------------------------------------------

def test_written_json_matches_the_builder_contract(tmp_path):
    from theodore.assembly import builder

    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=600.0),
    ], "haylee")
    out = mc.save_multicam(plan, tmp_path)

    assert out == tmp_path / "multicam.json"
    data = json.loads(out.read_text())

    # exact shape the contract requires
    assert isinstance(data["groups"], list)
    group = data["groups"][0]
    assert group["multicam_clip_name"] == "haylee_multicam_1"
    assert [a["path"] for a in group["angles"]] == ["/m/cam_a.mov", "/m/cam_b.mov"]

    # and the real consumer resolves both angles to that name
    loaded = builder.load_multicam(out)
    assert builder.multicam_clip_for_source(loaded, "/m/cam_a.mov") == "haylee_multicam_1"
    assert builder.multicam_clip_for_source(loaded, "/m/cam_b.mov") == "haylee_multicam_1"
    assert builder.multicam_clip_for_source(loaded, "/m/unrelated.mov") is None


def test_round_trip_load(tmp_path):
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=600.0),
    ], "haylee")
    mc.save_multicam(plan, tmp_path)

    assert mc.load_multicam(tmp_path)["groups"][0]["multicam_clip_name"] == "haylee_multicam_1"
    assert mc.load_multicam(tmp_path / "nope") is None


def test_empty_plan_still_writes_a_valid_file(tmp_path):
    plan = mc.plan_multicam([media("/m/only.mov", tc="01:00:00:00")], "haylee")
    out = mc.save_multicam(plan, tmp_path)

    data = json.loads(out.read_text())
    assert data["groups"] == []
    from theodore.assembly import builder
    assert builder.multicam_clip_for_source(builder.load_multicam(out), "/m/only.mov") is None


# --------------------------------------------------------------------------
# the optional Resolve-touching step
# --------------------------------------------------------------------------

class _FakeItem:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetClipProperty(self, key=None):
        return {"File Path": ""}


class _FakeFolder:
    def __init__(self, clips):
        self._clips = clips

    def GetClipList(self):
        return self._clips

    def GetSubFolderList(self):
        return []


class _FakeMediaPool:
    def __init__(self, names_):
        self._root = _FakeFolder([_FakeItem(n) for n in names_])

    def GetRootFolder(self):
        return self._root


class _FakeProject:
    def __init__(self, pool):
        self._pool = pool

    def GetMediaPool(self):
        return self._pool


def _handles(pool):
    from theodore.resolve.connection import ResolveHandles
    return ResolveHandles(resolve=None, project_manager=None,
                          project=_FakeProject(pool), timeline=None)


def test_check_multicam_clips_reports_existing_and_missing():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=600.0),
        media("/m/late_a.mov", tc="05:00:00:00", duration=600.0),
        media("/m/late_b.mov", tc="05:00:00:05", duration=600.0),
    ], "haylee")
    assert names(plan) == ["haylee_multicam_1", "haylee_multicam_2"]

    status = mc.check_multicam_clips(_handles(_FakeMediaPool(["haylee_multicam_1", "junk"])), plan)
    assert status["existing"] == ["haylee_multicam_1"]
    assert status["missing"] == ["haylee_multicam_2"]


def test_check_multicam_clips_errors_without_a_media_pool():
    plan = mc.plan_multicam([
        media("/m/cam_a.mov", tc="01:00:00:00", duration=600.0),
        media("/m/cam_b.mov", tc="01:00:00:05", duration=600.0),
    ], "haylee")

    with pytest.raises(mc.MulticamError):
        mc.check_multicam_clips(_handles(None), plan)
