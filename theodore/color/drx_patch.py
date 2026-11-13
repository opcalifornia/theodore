"""Patch the camera input of a DaVinci Resolve .drx node tree.

A .drx stores each grade as <Body>: one prefix byte, then a zstd frame of
protobuf. The first Color Space Transform's input is plain strings
(ALEXA3_COLORSPACE / LOGC_EI800_GAMMA). To retarget it we decode the protobuf
generically, swap the strings, and re-encode so every enclosing length is
rewritten. `roundtrip_ok` proves the codec is lossless before any patch is trusted.
"""
from __future__ import annotations

import re
from typing import List, Tuple

import zstandard

RED = {"ALEXA3_COLORSPACE": "RWG_COLORSPACE", "LOGC_EI800_GAMMA": "RED_LOG3G10_GAMMA"}


def _varint(b: bytes, i: int) -> Tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def _enc(n: int) -> bytes:
    out = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        out.append(c | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _parse(b: bytes):
    """-> list of (tag, kind, value); None if b is not clean protobuf."""
    items, i = [], 0
    try:
        while i < len(b):
            tag, i = _varint(b, i)
            wt, fn = tag & 7, tag >> 3
            if fn == 0:
                return None
            if wt == 0:
                v, i = _varint(b, i)
                items.append((tag, "v", v))
            elif wt == 1:
                items.append((tag, "r", b[i:i + 8]))
                i += 8
            elif wt == 5:
                items.append((tag, "r", b[i:i + 4]))
                i += 4
            elif wt == 2:
                n, i = _varint(b, i)
                if i + n > len(b):
                    return None
                blob = b[i:i + n]
                i += n
                sub = _parse(blob) if blob else None
                printable = bool(blob) and all(32 <= c < 127 for c in blob)
                items.append((tag, "m", sub) if (sub is not None and not printable) else (tag, "s", blob))
            else:
                return None
    except IndexError:
        return None
    return items


def _dump(items) -> bytes:
    out = bytearray()
    for tag, kind, v in items:
        out += _enc(tag)
        if kind == "v":
            out += _enc(v)
        elif kind == "r":
            out += v
        elif kind == "m":
            body = _dump(v)
            out += _enc(len(body)) + body
        else:
            out += _enc(len(v)) + v
    return bytes(out)


def _swap(items, mapping) -> int:
    n = 0
    for k, (tag, kind, v) in enumerate(items):
        if kind == "m":
            n += _swap(v, mapping)
        elif kind == "s" and v.decode("latin1") in mapping:
            items[k] = (tag, kind, mapping[v.decode("latin1")].encode())
            n += 1
    return n


_BODY = re.compile(r"(<Body>)([0-9a-f]{60,})(</Body>)")


def roundtrip_ok(text: str) -> bool:
    dctx = zstandard.ZstdDecompressor()
    for m in _BODY.finditer(text):
        raw = dctx.decompressobj().decompress(bytes.fromhex(m.group(2))[1:])
        parsed = _parse(raw)
        if parsed is None or _dump(parsed) != raw:
            return False
    return True


def retarget_input(text: str, mapping=RED) -> Tuple[str, int]:
    """Swap strings in every grade Body. Returns (new text, strings changed)."""
    dctx, cctx = zstandard.ZstdDecompressor(), zstandard.ZstdCompressor(level=3)
    total = 0

    def fix(m):
        nonlocal total
        data = bytes.fromhex(m.group(2))
        raw = dctx.decompressobj().decompress(data[1:])
        parsed = _parse(raw)
        if parsed is None:
            return m.group(0)
        n = _swap(parsed, mapping)
        if not n:
            return m.group(0)
        total += n
        return m.group(1) + (data[:1] + cctx.compress(_dump(parsed))).hex() + m.group(3)

    return _BODY.sub(fix, text), total
