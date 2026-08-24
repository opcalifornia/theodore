"""Deepgram Nova-3 transcription client + normalization to Theodore's
transcript.json schema.

filler_words is turned on deliberately: v1 doesn't act on them, but Phase 6's
pacing pass needs them present in the stored transcript, and we don't want
to re-transcribe (and re-bill) later just to get them.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

from theodore import config
from theodore.ingest.audio import content_hash
from theodore.transcribe import cache

logger = logging.getLogger("theodore.transcribe.deepgram")

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

REQUEST_PARAMS = {
    "model": "nova-3",
    "diarize": "true",
    "punctuate": "true",
    "smart_format": "true",
    "utterances": "true",
    "paragraphs": "true",
    "filler_words": "true",
}

MAX_RETRIES = 4


class TranscriptionError(RuntimeError):
    pass


def _post_with_retry(audio_bytes: bytes, mimetype: str) -> dict:
    api_key = config.require_deepgram_key()
    headers = {"Authorization": f"Token {api_key}", "Content-Type": mimetype}

    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                DEEPGRAM_URL, params=REQUEST_PARAMS, headers=headers,
                data=audio_bytes, timeout=600,
            )
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if resp.status_code == 200:
                return resp.json()
            last_exc = TranscriptionError(f"Deepgram returned {resp.status_code}: {resp.text[:500]}")
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise last_exc  # not worth retrying a 4xx that isn't a rate limit

        if attempt < MAX_RETRIES - 1:
            wait = 2 ** attempt
            logger.warning(
                "Deepgram request failed (attempt %d/%d): %s -- retrying in %ds",
                attempt + 1, MAX_RETRIES, last_exc, wait,
            )
            time.sleep(wait)

    raise TranscriptionError(f"Deepgram failed after {MAX_RETRIES} attempts: {last_exc}") from last_exc


def transcribe_audio(wav_path: Path, project_dir: Path, force: bool = False) -> dict:
    """Returns the raw Deepgram response, using the on-disk cache keyed on
    audio content hash unless force=True."""
    digest = content_hash(wav_path)
    if not force:
        cached = cache.load_cached(project_dir, digest)
        if cached is not None:
            logger.info("Transcript cache hit for %s -- not re-billing Deepgram", wav_path.name)
            return cached

    logger.info("Sending %s to Deepgram Nova-3 (%d bytes)", wav_path.name, wav_path.stat().st_size)
    audio_bytes = wav_path.read_bytes()
    raw = _post_with_retry(audio_bytes, "audio/wav")

    (project_dir / "transcript_raw.json").write_text(json.dumps(raw, indent=2))
    cache.store(project_dir, digest, raw)
    return raw


def _word_dict(w: dict) -> dict:
    return {
        "word": w.get("punctuated_word", w.get("word")),
        "start": w["start"],
        "end": w["end"],
        "confidence": w.get("confidence", 0.0),
    }


def _utterance_from_words(idx: int, speaker: str, words: list) -> dict:
    return {
        "id": f"u{idx:03d}",
        "speaker": speaker,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": " ".join(w.get("punctuated_word", w.get("word", "")) for w in words),
        "words": [_word_dict(w) for w in words],
    }


def normalize(raw: dict, source_file: str, fps: str, start_timecode: str) -> dict:
    """Deepgram's raw response -> Theodore's transcript.json schema."""
    utterances_raw = raw.get("results", {}).get("utterances")

    speakers: dict[str, str] = {}
    utterances = []

    if utterances_raw:
        for i, u in enumerate(utterances_raw, start=1):
            speaker = str(u.get("speaker", 0))
            speakers.setdefault(speaker, f"Speaker {speaker}")
            utterances.append({
                "id": f"u{i:03d}",
                "speaker": speaker,
                "start": u["start"],
                "end": u["end"],
                "text": u.get("transcript", ""),
                "words": [_word_dict(w) for w in u.get("words", [])],
            })
    else:
        # Fallback if Deepgram didn't return utterances for some reason:
        # build one utterance per contiguous same-speaker run of words from
        # the primary transcript alternative.
        alt = raw["results"]["channels"][0]["alternatives"][0]
        words = alt.get("words", [])
        run: list = []
        cur_speaker: Optional[str] = None
        idx = 0
        for w in words:
            sp = str(w.get("speaker", 0))
            if cur_speaker is not None and sp != cur_speaker and run:
                idx += 1
                utterances.append(_utterance_from_words(idx, cur_speaker, run))
                speakers.setdefault(cur_speaker, f"Speaker {cur_speaker}")
                run = []
            cur_speaker = sp
            run.append(w)
        if run:
            idx += 1
            utterances.append(_utterance_from_words(idx, cur_speaker, run))
            speakers.setdefault(cur_speaker, f"Speaker {cur_speaker}")

    return {
        "source_file": source_file,
        "fps": fps,
        "start_timecode": start_timecode,
        "speakers": speakers,
        "utterances": utterances,
    }


def _offset_utterance(u: dict, offset: float, source_index: int) -> dict:
    """A copy of `u` with its own and its words' start/end shifted by
    `offset` seconds, plus source_index/source_start/source_end recording
    where it really came from. Words are offset too, not just the
    utterance -- trim.py resolves sentence boundaries from word-level
    timestamps, and a merged utterance whose words still carried their
    original per-file times would silently desync the moment anything
    downstream trims mid-utterance."""
    return {
        **u,
        "start": u["start"] + offset,
        "end": u["end"] + offset,
        "source_index": source_index,
        "source_start": u["start"],
        "source_end": u["end"],
        "words": [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in u.get("words", [])],
    }


def merge_normalized_transcripts(entries: list[tuple[dict, float]]) -> dict:
    """Merge several single-file normalize()d transcripts, in the given
    order, into one continuous transcript spanning all of them -- for a
    subject whose audio was recorded across multiple separate files (an
    external recorder stopped and restarted through a session, the normal
    case for a lav/boom mic rather than one continuous camera file).

    `entries` is `[(transcript, duration_seconds), ...]`, already in the
    order the files were actually recorded -- this function trusts that
    order rather than re-deriving it.

    Each file's utterance start/end (seconds relative to THAT file) is
    offset by the cumulative duration of every file before it, so the
    merged utterance list reads as one continuous conversation -- exactly
    what analyze/segmenter.py needs, since it works purely off utterance
    start/end/text with no notion of "file" at all. Every utterance also
    keeps source_index/source_start/source_end: its original file and
    un-offset local time, since anything that eventually places a clip on
    a real timeline needs the real file and real local offset, not the
    virtual merged one -- deliberately not resolved here.

    Speaker ids are assumed consistent across files: Deepgram's "Speaker
    0"/"Speaker 1" labels for file N are treated as the same real people
    as "Speaker 0"/"Speaker 1" in file N+1. Deepgram diarizes each file
    independently with no cross-file voice matching, so this is the only
    practical default without building real cross-recording speaker
    identification -- true for the ordinary case of one consistent
    interviewer/interviewee pair recorded on the same setup throughout a
    session. `_prompt_speaker_names()` shows a text sample per speaker so
    a human catches it if that assumption is ever wrong for a subject.
    """
    if not entries:
        raise ValueError("merge_normalized_transcripts requires at least one (transcript, duration) entry")

    first = entries[0][0]

    if len(entries) == 1:
        # Not just an optimization: keeps the single-file case's on-disk
        # shape exactly what it always was (plus the new "sources" field),
        # so nothing already relying on transcript["source_file"]/["fps"]
        # for a single-source subject needs to change to keep working.
        transcript, duration = entries[0]
        return {
            **transcript,
            "sources": [{
                "path": transcript["source_file"], "fps": transcript["fps"],
                "start_timecode": transcript["start_timecode"], "duration_seconds": duration,
            }],
            "utterances": [_offset_utterance(u, 0.0, 0) for u in transcript["utterances"]],
        }

    sources: list[dict] = []
    speakers: dict[str, str] = {}
    utterances: list[dict] = []
    offset = 0.0
    next_id = 1
    for source_index, (transcript, duration) in enumerate(entries):
        sources.append({
            "path": transcript["source_file"], "fps": transcript["fps"],
            "start_timecode": transcript["start_timecode"], "duration_seconds": duration,
        })
        for speaker_id, name in transcript["speakers"].items():
            speakers.setdefault(speaker_id, name)
        for u in transcript["utterances"]:
            merged = _offset_utterance(u, offset, source_index)
            merged["id"] = f"u{next_id:03d}"
            utterances.append(merged)
            next_id += 1
        offset += duration

    return {
        "source_file": first["source_file"],
        "sources": sources,
        "fps": first["fps"],
        "start_timecode": first["start_timecode"],
        "speakers": speakers,
        "utterances": utterances,
    }


def apply_speaker_names(transcript: dict, names: dict) -> dict:
    transcript["speakers"] = {k: names.get(k, v) for k, v in transcript["speakers"].items()}
    return transcript


def save_normalized(transcript: dict, project_dir: Path) -> Path:
    out = project_dir / "transcript.json"
    out.write_text(json.dumps(transcript, indent=2))
    return out
