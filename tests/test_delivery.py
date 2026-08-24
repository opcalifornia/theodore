"""Tests for analyze/delivery.py.

Two categories: pure Python logic (word-timestamp features, baselines,
descriptors, divergence -- no audio needed), and real acoustic extraction
against tests/fixtures/speech_a.wav / speech_b.wav, two short clips of
actual synthesized speech (espeak-ng), concatenated in-memory with
parselmouth so no ffmpeg or committed "combined" file is needed. These are
real formant-synthesized voice, not sine tones -- close enough to real
speech to validate that pitch/jitter/shimmer/intensity extraction actually
produces sane numbers, not just that the code runs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import parselmouth
import pytest

from theodore.analyze import delivery

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# Word-timestamp-derived features -- pure Python, no audio
# --------------------------------------------------------------------------

def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_speaking_rate_wps():
    words = [_w("a", 0.0, 0.2), _w("b", 0.2, 0.4), _w("c", 0.4, 0.6), _w("d", 0.6, 1.0)]
    # 4 words over (1.0 - 0.0) = 1.0s -> 4.0 wps
    assert delivery.speaking_rate_wps(words) == 4.0


def test_speaking_rate_wps_needs_at_least_two_words():
    assert delivery.speaking_rate_wps([_w("a", 0.0, 0.2)]) is None
    assert delivery.speaking_rate_wps([]) is None


def test_pause_structure_counts_gaps_over_threshold():
    words = [_w("a", 0.0, 0.2), _w("b", 0.2, 0.4), _w("c", 1.0, 1.2), _w("d", 1.2, 1.4)]
    result = delivery.pause_structure(words, threshold=0.3)
    assert result == {"count": 1, "total_seconds": pytest.approx(0.6)}


def test_pause_structure_no_gaps():
    words = [_w("a", 0.0, 0.2), _w("b", 0.21, 0.4)]
    assert delivery.pause_structure(words, threshold=0.3) == {"count": 0, "total_seconds": 0}


def test_onset_delay_positive_gap():
    assert delivery.onset_delay_seconds(question_end=10.0, answer_start=12.3) == 2.3


def test_onset_delay_none_without_a_question():
    assert delivery.onset_delay_seconds(question_end=None, answer_start=5.0) is None


def test_onset_delay_negative_gap_is_none_not_negative():
    # The answer overlaps/interrupts the question -- not a "delay".
    assert delivery.onset_delay_seconds(question_end=10.0, answer_start=9.5) is None


# --------------------------------------------------------------------------
# Baselines, descriptors, divergence -- pure Python
# --------------------------------------------------------------------------

def test_compute_baselines_groups_by_speaker_and_needs_two_samples():
    utterance_features = {
        "u001": {"speaker": "0", "pitch_mean_hz": 100.0, "pitch_range_hz": None, "intensity_mean_db": None,
                  "jitter_local": None, "shimmer_local": None, "speaking_rate_wps": None},
        "u002": {"speaker": "0", "pitch_mean_hz": 120.0, "pitch_range_hz": None, "intensity_mean_db": None,
                  "jitter_local": None, "shimmer_local": None, "speaking_rate_wps": None},
        "u003": {"speaker": "1", "pitch_mean_hz": 200.0, "pitch_range_hz": None, "intensity_mean_db": None,
                  "jitter_local": None, "shimmer_local": None, "speaking_rate_wps": None},
    }
    baselines = delivery.compute_baselines(utterance_features)
    assert baselines["0"]["pitch_mean_hz"]["mean"] == 110.0
    assert baselines["0"]["pitch_mean_hz"]["n"] == 2
    # Speaker "1" has only one sample for pitch_mean_hz -- no baseline for it.
    assert "pitch_mean_hz" not in baselines["1"]


def test_describe_segment_delivery_includes_available_measures():
    features = {
        "speaking_rate_wps": 2.1, "pitch_range_hz": 20.0, "pause_count": 4,
        "onset_delay_seconds": 2.3, "intensity_trend": "rising",
    }
    baseline = {
        "speaking_rate_wps": {"mean": 3.2, "std": 0.5},
        "pitch_range_hz": {"mean": 50.0, "std": 5.0},
    }
    text = delivery.describe_segment_delivery("haylee.q03", features, baseline)
    assert "Segment haylee.q03 delivery profile:" in text
    assert "Speaking rate: 2.1 words/sec (34% below baseline of 3.2)" in text
    assert "Pitch range: narrow" in text
    assert "Pauses: 4 internal pause" in text
    assert "Onset delay: 2.3s" in text
    assert "Energy: rising" in text


def test_describe_segment_delivery_omits_missing_baseline_measures():
    text = delivery.describe_segment_delivery("s001", {"speaking_rate_wps": 2.0}, {})
    assert "Speaking rate" not in text
    assert "not enough data" in text


def test_describe_segment_delivery_steady_energy_is_not_called_out():
    features = {"intensity_trend": "steady"}
    text = delivery.describe_segment_delivery("s001", features, {})
    assert "Energy" not in text


def test_divergence_score_zero_at_baseline_mean():
    features = {"pitch_mean_hz": 100.0}
    baseline = {"pitch_mean_hz": {"mean": 100.0, "std": 10.0}}
    assert delivery.divergence_score(features, baseline) == 0.0


def test_divergence_score_scales_with_distance_from_baseline():
    baseline = {"pitch_mean_hz": {"mean": 100.0, "std": 10.0}}
    near = delivery.divergence_score({"pitch_mean_hz": 105.0}, baseline)
    far = delivery.divergence_score({"pitch_mean_hz": 150.0}, baseline)
    assert near < far


def test_divergence_score_none_when_nothing_to_compare():
    assert delivery.divergence_score({"pitch_mean_hz": 100.0}, {}) is None


def test_rank_by_divergence_sorts_descending_and_skips_unscored():
    delivery_data = {
        "segments": {
            "s001": {"divergence": 0.5, "descriptor": "d1"},
            "s002": {"divergence": 2.1, "descriptor": "d2"},
            "s003": {"divergence": None, "descriptor": "d3"},
        }
    }
    analysis = {"segments": [
        {"id": "s001", "question_label": "First"},
        {"id": "s002", "question_label": "Second"},
        {"id": "s003", "question_label": "Third"},
    ]}
    ranked = delivery.rank_by_divergence(delivery_data, analysis)
    assert [r["segment_id"] for r in ranked] == ["s002", "s001"]
    assert ranked[0]["label"] == "Second"


# --------------------------------------------------------------------------
# aggregate_segment_features: multi-utterance segments, onset fallback
# --------------------------------------------------------------------------

_TRANSCRIPT = {
    "utterances": [
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 1.0, "text": "Q", "words": []},
        {"id": "u002", "speaker": "0", "start": 1.5, "end": 3.0, "text": "A1", "words": []},
        {"id": "u003", "speaker": "0", "start": 3.1, "end": 5.0, "text": "A2", "words": []},
    ],
}


def test_aggregate_segment_features_averages_across_utterance_range():
    utterance_features = {
        "u002": {"speaker": "0", "pitch_mean_hz": 100.0, "pitch_range_hz": 10.0, "intensity_mean_db": 60.0,
                  "jitter_local": 0.01, "shimmer_local": 0.1, "speaking_rate_wps": 3.0,
                  "pause_count": 1, "pause_total_seconds": 0.5, "intensity_trend": "steady",
                  "onset_delay_seconds": 0.5},
        "u003": {"speaker": "0", "pitch_mean_hz": 120.0, "pitch_range_hz": 20.0, "intensity_mean_db": 62.0,
                  "jitter_local": 0.02, "shimmer_local": 0.12, "speaking_rate_wps": 4.0,
                  "pause_count": 2, "pause_total_seconds": 0.3, "intensity_trend": "rising",
                  "onset_delay_seconds": None},
    }
    segment = {"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u003"}
    agg = delivery.aggregate_segment_features(segment, utterance_features, _TRANSCRIPT)

    assert agg["pitch_mean_hz"] == 110.0  # mean of 100, 120
    assert agg["pause_count"] == 3  # summed, not averaged
    assert agg["pause_total_seconds"] == pytest.approx(0.8)
    assert agg["intensity_trend"] == "rising"  # from the LAST utterance in range
    assert agg["onset_delay_seconds"] == 0.5  # from the FIRST utterance in range


def test_aggregate_segment_features_falls_back_to_question_utterance():
    utterance_features = {
        "u002": {"speaker": "0", "pitch_mean_hz": 100.0, "pitch_range_hz": None, "intensity_mean_db": None,
                  "jitter_local": None, "shimmer_local": None, "speaking_rate_wps": None,
                  "pause_count": None, "pause_total_seconds": None, "intensity_trend": None,
                  "onset_delay_seconds": None},  # no onset computed at the utterance level
    }
    segment = {
        "id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
        "question_start_utterance": "u001",
    }
    agg = delivery.aggregate_segment_features(segment, utterance_features, _TRANSCRIPT)
    # u001 ends at 1.0, u002 starts at 1.5 -> 0.5s onset delay, resolved via the fallback.
    assert agg["onset_delay_seconds"] == 0.5


def test_aggregate_segment_features_returns_none_for_unresolvable_range():
    segment = {"id": "s001", "answer_start_utterance": "u999", "answer_end_utterance": "u999"}
    assert delivery.aggregate_segment_features(segment, {}, _TRANSCRIPT) is None


# --------------------------------------------------------------------------
# save/load round trip
# --------------------------------------------------------------------------

def test_save_and_load_delivery_round_trips(tmp_path):
    data = {"utterances": {"u001": {"speaker": "0"}}, "baselines": {}, "segments": {}}
    delivery.save_delivery(data, tmp_path)
    assert delivery.load_delivery(tmp_path) == data


def test_load_delivery_returns_none_when_missing(tmp_path):
    assert delivery.load_delivery(tmp_path) is None


# --------------------------------------------------------------------------
# Real acoustic extraction against actual synthesized speech
# --------------------------------------------------------------------------

def _load_combined_sound() -> parselmouth.Sound:
    """speech_a.wav + 0.2s of silence + speech_b.wav, concatenated in
    memory via parselmouth/numpy -- no ffmpeg or committed composite file
    needed."""
    a = parselmouth.Sound(str(FIXTURES / "speech_a.wav"))
    b = parselmouth.Sound(str(FIXTURES / "speech_b.wav"))
    assert a.sampling_frequency == b.sampling_frequency
    sr = a.sampling_frequency
    silence = np.zeros(int(0.2 * sr))
    combined = np.concatenate([a.values[0], silence, b.values[0]])
    return parselmouth.Sound(combined, sampling_frequency=sr)


@pytest.fixture(scope="module")
def combined_sound():
    return _load_combined_sound()


def test_extract_acoustic_features_on_real_speech_produces_sane_values(combined_sound):
    features = delivery.extract_acoustic_features(combined_sound, 0.0, combined_sound.duration)
    # Human speech F0 is roughly 60-400Hz; espeak's default voice included.
    assert 50 < features["pitch_mean_hz"] < 400
    assert features["pitch_range_hz"] > 0
    assert features["intensity_mean_db"] > 0
    # Jitter/shimmer are small positive fractions for real voiced speech.
    assert 0 < features["jitter_local"] < 0.1
    assert 0 < features["shimmer_local"] < 0.5


def test_extract_acoustic_features_empty_window_returns_all_none(combined_sound):
    features = delivery.extract_acoustic_features(combined_sound, 1.0, 1.0)
    assert all(v is None for v in features.values())


def test_extract_acoustic_features_window_past_end_is_clamped_not_crashed(combined_sound):
    features = delivery.extract_acoustic_features(combined_sound, combined_sound.duration - 0.1, combined_sound.duration + 5.0)
    # Should still extract from the valid tail, not raise.
    assert features["intensity_mean_db"] is not None or features["pitch_mean_hz"] is None


def test_extract_acoustic_features_very_short_window_does_not_crash(combined_sound):
    # Too short for a reliable pitch period -- must degrade to None, not raise.
    features = delivery.extract_acoustic_features(combined_sound, 0.5, 0.505)
    assert isinstance(features, dict)


def test_intensity_trend_on_real_speech_returns_a_valid_label(combined_sound):
    trend = delivery.intensity_trend(combined_sound, 0.0, 3.0)
    assert trend in ("rising", "falling", "steady", None)


def test_analyze_utterances_end_to_end_on_real_speech(combined_sound, tmp_path):
    a = parselmouth.Sound(str(FIXTURES / "speech_a.wav"))
    b_start = a.duration + 0.2

    wav_path = tmp_path / "combined.wav"
    combined_sound.save(str(wav_path), "WAV")

    transcript = {
        "utterances": [
            {"id": "u001", "speaker": "1", "start": 0.0, "end": 0.0, "text": "", "words": []},
            {
                "id": "u002", "speaker": "0", "start": 0.0, "end": a.duration,
                "text": "speech a",
                "words": [
                    {"word": "It", "start": 0.0, "end": 0.3},
                    {"word": "was.", "start": 0.3, "end": a.duration},
                ],
            },
            {
                "id": "u003", "speaker": "0", "start": b_start, "end": combined_sound.duration,
                "text": "speech b",
                "words": [
                    {"word": "Then", "start": b_start, "end": b_start + 0.3},
                    {"word": "again.", "start": b_start + 0.3, "end": combined_sound.duration},
                ],
            },
        ],
    }

    features = delivery.analyze_utterances(wav_path, transcript)
    assert features["u002"]["speaker"] == "0"
    assert features["u002"]["pitch_mean_hz"] is not None
    assert features["u002"]["speaking_rate_wps"] == pytest.approx(2 / a.duration)
    # u003 immediately follows u002 with the SAME speaker -- not an "answer
    # opening a turn", so no onset delay is computed for it.
    assert features["u003"]["onset_delay_seconds"] is None

    baselines = delivery.compute_baselines(features)
    assert "pitch_mean_hz" in baselines["0"]
    assert baselines["0"]["pitch_mean_hz"]["n"] == 2

    analysis = {"segments": [
        {"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Q?"},
        {"id": "s002", "answer_start_utterance": "u003", "answer_end_utterance": "u003", "question_text": None},
    ]}
    full = delivery.analyze_delivery(wav_path, transcript, analysis)
    assert set(full["segments"].keys()) == {"s001", "s002"}
    assert "delivery profile" in full["segments"]["s001"]["descriptor"]
    assert full["segments"]["s001"]["divergence"] is not None


# --------------------------------------------------------------------------
# Multi-file subjects -- each utterance reads its OWN source file at its
# OWN file-relative time, not one Sound object read at merged-second
# offsets (which would silently read the wrong audio, or the wrong file
# entirely, for anything past the first source).
# --------------------------------------------------------------------------

def test_analyze_utterances_accepts_a_plain_string_path(combined_sound, tmp_path):
    wav_path = tmp_path / "combined.wav"
    combined_sound.save(str(wav_path), "WAV")
    transcript = {"utterances": [
        {"id": "u001", "speaker": "0", "start": 0.0, "end": combined_sound.duration, "words": []},
    ]}
    features = delivery.analyze_utterances(str(wav_path), transcript)
    assert features["u001"]["pitch_mean_hz"] is not None


def test_analyze_utterances_multi_file_reads_each_utterance_from_its_own_source():
    a = parselmouth.Sound(str(FIXTURES / "speech_a.wav"))
    b = parselmouth.Sound(str(FIXTURES / "speech_b.wav"))

    transcript = {
        "utterances": [
            {
                "id": "u001", "speaker": "0", "start": 0.0, "end": a.duration,
                "source_index": 0, "source_start": 0.0, "source_end": a.duration,
                "words": [{"word": "It", "start": 0.0, "end": 0.3}, {"word": "was.", "start": 0.3, "end": a.duration}],
            },
            {
                # In the MERGED conversation this immediately follows u001,
                # but it's really a separate file (source_index 1), so its
                # file-relative time resets near 0 -- exactly the shape a
                # real second-take recorder file produces.
                "id": "u002", "speaker": "0",
                "start": a.duration + 0.2, "end": a.duration + 0.2 + b.duration,
                "source_index": 1, "source_start": 0.0, "source_end": b.duration,
                "words": [{"word": "Then", "start": a.duration + 0.2, "end": a.duration + 0.5}],
            },
        ],
    }

    features = delivery.analyze_utterances(
        {0: FIXTURES / "speech_a.wav", 1: FIXTURES / "speech_b.wav"}, transcript,
    )

    assert features["u001"]["pitch_mean_hz"] is not None
    assert features["u002"]["pitch_mean_hz"] is not None
    # Proves u002 really read file B's audio (not file A's, and not
    # merged-offset garbage past file A's own duration): its features
    # match extracting directly from speech_b.wav at its own start/end.
    direct = delivery.extract_acoustic_features(b, 0.0, b.duration)
    assert features["u002"]["pitch_mean_hz"] == pytest.approx(direct["pitch_mean_hz"])


def test_analyze_utterances_missing_source_file_degrades_gracefully():
    transcript = {"utterances": [
        {"id": "u001", "speaker": "0", "start": 0.0, "end": 1.0,
         "source_index": 5, "source_start": 0.0, "source_end": 1.0, "words": []},
    ]}
    features = delivery.analyze_utterances({0: FIXTURES / "speech_a.wav"}, transcript)
    assert features["u001"]["pitch_mean_hz"] is None
    assert features["u001"]["intensity_trend"] is None


def test_onset_delay_still_uses_merged_time_across_a_source_boundary():
    transcript = {
        "utterances": [
            {"id": "u001", "speaker": "1", "start": 0.0, "end": 2.0,
             "source_index": 0, "source_start": 0.0, "source_end": 2.0, "words": []},
            # Merged: a 3s gap after u001 (5.0 - 2.0). File-relative:
            # starts at 0.0 in its own (second) file -- if onset delay
            # wrongly used file-relative time it would compute a negative
            # gap and return None instead of the real ~3s pause.
            {"id": "u002", "speaker": "0", "start": 5.0, "end": 6.0,
             "source_index": 1, "source_start": 0.0, "source_end": 1.0, "words": []},
        ],
    }
    features = delivery.analyze_utterances(
        {0: FIXTURES / "speech_a.wav", 1: FIXTURES / "speech_b.wav"}, transcript,
    )
    assert features["u002"]["onset_delay_seconds"] == pytest.approx(3.0)


def test_analyze_utterances_old_transcript_without_source_metadata_defaults_to_source_zero(combined_sound, tmp_path):
    wav_path = tmp_path / "combined.wav"
    combined_sound.save(str(wav_path), "WAV")
    # No source_index/source_start/source_end at all -- an old, single-file
    # transcript predating multi-file merging.
    transcript = {"utterances": [
        {"id": "u001", "speaker": "0", "start": 0.0, "end": combined_sound.duration, "words": []},
    ]}
    features = delivery.analyze_utterances({0: wav_path}, transcript)
    assert features["u001"]["pitch_mean_hz"] is not None
