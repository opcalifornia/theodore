"""`theodore agent-chat` -- the REPL the Resolve panel's chat box talks to."""
from __future__ import annotations

import json

from click.testing import CliRunner

from agent_fakes import ScriptedClient, call, connect_factory, make_resolve, reply, text
from theodore import cli as cli_module
from theodore.agent import loop
from theodore.cli import cli


def _wire(monkeypatch, client, resolve, project):
    connect = connect_factory(resolve, project)
    monkeypatch.setattr(cli_module.config, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli_module.anthropic, "Anthropic", lambda **kw: client)
    monkeypatch.setattr(cli_module.resolve_connection, "connect", connect)
    monkeypatch.setattr(cli_module, "_connect_or_die", connect)


def test_conversation_carries_across_messages(monkeypatch):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(reply(text("It's one 20 second clip.")), reply(text("Same one, yes.")))
    _wire(monkeypatch, client, resolve, project)

    out = CliRunner().invoke(cli, ["agent-chat"], input="what's on the timeline?\nand how long?\nexit\n")

    assert out.exit_code == 0, out.output
    assert "It's one 20 second clip." in out.output and "Same one, yes." in out.output
    second = client.requests[1]["messages"]
    assert second[0]["content"] == "what's on the timeline?"     # turn one is still in context
    assert second[-1]["content"] == "and how long?"


def test_each_message_gets_its_own_checkpoint(monkeypatch):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("add_marker", frame=86500)), reply(call("export_frame")), reply(text("one")),
        reply(call("add_marker", frame=86600)), reply(call("export_frame")), reply(text("two")))
    _wire(monkeypatch, client, resolve, project)

    out = CliRunner().invoke(cli, ["agent-chat"], input="mark a\nmark b\nexit\n")

    assert out.exit_code == 0, out.output
    assert len(project.timelines) == 3          # original + one checkpoint per instruction
    assert "Checkpoint:" in out.output


def test_confirmation_is_asked_over_stdin_and_a_no_is_respected(monkeypatch):
    resolve, project, tl = make_resolve()
    tl.markers[100] = {"color": "Blue"}
    client = ScriptedClient(reply(call("delete_marker", frame=86500)), reply(text("Left it.")))
    _wire(monkeypatch, client, resolve, project)

    out = CliRunner().invoke(cli, ["agent-chat"], input="delete the marker\nno\nexit\n")

    assert "[confirm]" in out.output and "reply yes or no" in out.output
    assert 100 in tl.markers


def test_confirmation_yes_proceeds(monkeypatch):
    resolve, project, tl = make_resolve()
    tl.markers[100] = {"color": "Blue"}
    client = ScriptedClient(
        reply(call("delete_marker", frame=86500)), reply(call("export_frame")), reply(text("Gone.")))
    _wire(monkeypatch, client, resolve, project)

    CliRunner().invoke(cli, ["agent-chat"], input="delete the marker\nyes\nexit\n")

    assert 100 not in tl.markers


def test_old_frames_are_dropped_from_carried_history():
    big = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    history = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                "content": [{"type": "text", "text": "{}"}, big]}]}]
    kept = loop._drop_old_images(history)
    inner = kept[0]["content"][0]["content"]
    assert all(b["type"] == "text" for b in inner) and "omitted" in inner[1]["text"]
    assert history[0]["content"][0]["content"][1] is big       # original untouched


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(cli_module.config, "ANTHROPIC_API_KEY", None)
    out = CliRunner().invoke(cli, ["agent-chat"], input="exit\n")
    assert out.exit_code != 0 and "ANTHROPIC_API_KEY" in out.output


class _Stream:
    def __init__(self, response, pieces):
        self._r, self._p = response, pieces

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return iter(self._p)

    def get_final_message(self):
        return self._r


class StreamingClient(ScriptedClient):
    """A client whose replies arrive in pieces, like the real SDK's."""

    def __init__(self, *responses, pieces=None):
        super().__init__(*responses)
        self.pieces = pieces or {}

    def stream(self, **kw):
        response = self.create(**kw)
        words = "".join(b.text for b in response.content if b.type == "text")
        return _Stream(response, self.pieces.get(words, [words[:4], words[4:]] if words else []))


def test_replies_stream_in_pieces_when_the_client_supports_it(monkeypatch):
    resolve, project, tl = make_resolve()
    client = StreamingClient(reply(text("Hello there")))
    _wire(monkeypatch, client, resolve, project)

    out = CliRunner().invoke(cli, ["agent-chat", "--json"], input="hi\nexit\n")

    events = [json.loads(l) for l in out.output.splitlines() if l.startswith("{")]
    deltas = [e["text"] for e in events if e["t"] == "delta"]
    assert len(deltas) == 2 and "".join(deltas) == "Hello there"
    kinds = [e["t"] for e in events]
    assert kinds.index("say_end") < kinds.index("done")
    assert "say" not in kinds                     # streamed once, not repeated in full


def test_json_mode_is_one_parseable_event_per_line(monkeypatch):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("get_timeline_info")), reply(text("One clip.")))
    _wire(monkeypatch, client, resolve, project)

    out = CliRunner().invoke(cli, ["agent-chat", "--json"], input="look\nexit\n")

    events = [json.loads(l) for l in out.output.splitlines()]      # every line parses
    kinds = [e["t"] for e in events]
    assert kinds[0] == "ready" and events[0]["project"] == "Test Project"
    assert {"tool", "result", "say", "done"} <= set(kinds)
    assert next(e for e in events if e["t"] == "tool")["name"] == "get_timeline_info"
    assert kinds[-1] == "done"


def test_json_mode_confirm_event_waits_for_an_answer(monkeypatch):
    resolve, project, tl = make_resolve()
    tl.markers[100] = {"color": "Blue"}
    client = ScriptedClient(reply(call("delete_marker", frame=86500)), reply(text("Left it.")))
    _wire(monkeypatch, client, resolve, project)

    out = CliRunner().invoke(cli, ["agent-chat", "--json"], input="delete it\nno\nexit\n")

    events = [json.loads(l) for l in out.output.splitlines()]
    assert any(e["t"] == "confirm" and "delete_marker" in e["text"] for e in events)
    assert 100 in tl.markers
