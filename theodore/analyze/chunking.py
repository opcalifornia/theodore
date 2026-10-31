"""Splits a long transcript (or segment list) into overlapping chunks so
Claude passes stay within a reasonable context size, without missing
structure at chunk edges. Consumers de-duplicate anything that shows up in
two chunks by utterance/segment id."""
from __future__ import annotations

from theodore import config


def chunk_items(items: list) -> list[list]:
    size = config.MAX_UTTERANCES_PER_CHUNK
    overlap = config.CHUNK_UTTERANCE_OVERLAP
    if len(items) <= size:
        return [items]

    chunks = []
    start = 0
    while start < len(items):
        end = min(start + size, len(items))
        chunks.append(items[start:end])
        if end == len(items):
            break
        start = end - overlap
    return chunks
