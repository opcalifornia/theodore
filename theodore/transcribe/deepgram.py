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


def apply_speaker_names(transcript: dict, names: dict) -> dict:
    transcript["speakers"] = {k: names.get(k, v) for k, v in transcript["speakers"].items()}
    return transcript


def save_normalized(transcript: dict, project_dir: Path) -> Path:
    out = project_dir / "transcript.json"
    out.write_text(json.dumps(transcript, indent=2))
    return out
