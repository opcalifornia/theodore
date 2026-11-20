"""The .drx codec must be lossless and only touch the strings it is asked to."""
import zstandard

from theodore.color import drx_patch as d


def _body(raw: bytes) -> str:
    return "<Body>" + (b"\x81" + zstandard.ZstdCompressor().compress(raw)).hex() + "</Body>"


def _raw() -> bytes:
    def s(tag, v):
        return bytes([tag, len(v)]) + v
    inner = s(0x2A, b"ALEXA3_COLORSPACE") + s(0x2A, b"LOGC_EI800_GAMMA") + b"\x08\x01" * 20
    return s(0x0A, inner) + s(0x12, inner)


def test_roundtrip_lossless_and_retarget():
    text = _body(_raw())
    assert d.roundtrip_ok(text)
    new, n = d.retarget_input(text)
    assert n == 4 and d.roundtrip_ok(new)
    raw = zstandard.ZstdDecompressor().decompressobj().decompress(bytes.fromhex(new[6:-7])[1:])
    assert b"RWG_COLORSPACE" in raw and b"RED_LOG3G10_GAMMA" in raw and b"ALEXA3" not in raw
