"""The bridge script duplicates the wire protocol -- keep the copies honest.

``resolve_scripts/Theodore Bridge.py`` deliberately does not import
``theodore.resolve.bridge_protocol``. It runs inside Resolve's embedded
Python interpreter, which has none of Theodore's dependencies installed and
a different interpreter version, so the script is self-contained by design:
one file, standard library only, nothing to go wrong at import time in the
one environment we cannot debug remotely.

The cost of that choice is two copies of the codec. This module is the
guard rail that makes the choice safe -- if the copies ever drift, the build
fails here instead of silently corrupting data on the user's machine.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from theodore.resolve import bridge_protocol as proto

BRIDGE_SCRIPT = Path(__file__).resolve().parent.parent / "resolve_scripts" / "Theodore Bridge.py"


def _load_bridge_module():
    spec = importlib.util.spec_from_file_location("theodore_bridge_script_parity", BRIDGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_bridge_module()


@pytest.mark.parametrize("name", ["PROTOCOL_VERSION", "REF_KEY", "TYPE_KEY", "MAP_KEY", "ROOT_REF"])
def test_protocol_constants_match(name):
    assert getattr(script, name) == getattr(proto, name), (
        f"{name} differs between the bridge script and bridge_protocol.py"
    )


class _Obj:
    """Stands in for a Resolve object: not a primitive, not a container."""


@pytest.mark.parametrize("value", [
    None, True, False, 0, 1, -5, 3.5, "", "text",
    [], [1, "two", None],
    {},
    {1200: {"color": "Blue"}, 2400: {"color": "Red"}},   # int-keyed, like GetMarkers()
    {"a": [1, {2: "b"}]},
    [[1, 2], [3, 4]],
])
def test_both_codecs_encode_plain_values_identically(value):
    refs_a, refs_b = [], []
    encoded_script = script.encode_value(value, lambda o: refs_a.append(o) or len(refs_a))
    encoded_proto = proto.encode_value(value, lambda o: refs_b.append(o) or len(refs_b))
    assert encoded_script == encoded_proto


def test_both_codecs_round_trip_int_keyed_dicts():
    """The case that would corrupt markers if the copies disagreed."""
    original = {1200: {"color": "Blue"}, 2400: {"color": "Red"}}

    encoded = script.encode_value(original, lambda o: 1)
    assert proto.decode_value(encoded, lambda r, t=None: None) == original

    encoded = proto.encode_value(original, lambda o: 1)
    assert script.decode_value(encoded, lambda r, t=None: None) == original


def test_both_codecs_encode_opaque_objects_as_typed_refs():
    obj = _Obj()
    encoded_script = script.encode_value(obj, lambda o: 7)
    encoded_proto = proto.encode_value(obj, lambda o: 7)

    assert encoded_script == encoded_proto
    assert encoded_script == {proto.REF_KEY: 7, proto.TYPE_KEY: "_Obj"}


def test_both_codecs_resolve_refs_through_the_same_callback_signature():
    encoded = {proto.REF_KEY: 3, proto.TYPE_KEY: "Timeline"}
    seen = []

    def capture(ref, type_name):
        seen.append((ref, type_name))
        return "resolved"

    assert script.decode_value(encoded, capture) == "resolved"
    assert proto.decode_value(encoded, capture) == "resolved"
    assert seen == [(3, "Timeline"), (3, "Timeline")]


def test_bools_are_not_flattened_to_ints_by_either_codec():
    # bool is an int subclass, so a careless isinstance chain turns True
    # into 1 -- and Theodore checks Resolve return values with `is False`.
    for codec in (script.encode_value, proto.encode_value):
        assert codec(True, lambda o: 1) is True
        assert codec(False, lambda o: 1) is False


def test_handshake_location_matches():
    assert str(script._handshake_path()) == str(proto.handshake_path())


def test_handshake_location_honours_the_same_override(monkeypatch, tmp_path):
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "custom"))
    assert str(script._handshake_path()) == str(proto.handshake_path())
    assert str(tmp_path / "custom") in str(proto.handshake_path())
