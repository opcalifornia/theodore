"""The operator loop and its tools, against a fake Resolve and a scripted
Claude. What is verified here is everything we control: that edits are
checkpointed, that verification can't be skipped, that only deleting and
rendering ask, that undo works, and that nothing destructive is reachable
through the escape hatch. What this can't tell us is whether the real model
plans well -- that needs a real run."""
from __future__ import annotations

import json

import pytest

from agent_fakes import (
    ScriptedClient, call, connect_factory, make_resolve, reply, text, PNG,
)
from theodore.agent import loop, memory, safety, tools
from theodore.agent.tools import AgentContext


def _ctx(resolve, project, tmp_path, confirm=lambda d: False, **kw):
    return AgentContext(connect=connect_factory(resolve, project), confirm=confirm,
                        project_name="Test Project", memory_root=tmp_path / "mem",
                        scratch_dir=tmp_path / "scratch", **kw)


def _results(client, request_index):
    """tool_result payloads in the user turn the model saw at a given request."""
    last = client.requests[request_index]["messages"][-1]["content"]
    return [json.loads(b["content"][0]["text"]) for b in last if b.get("type") == "tool_result"]


def test_read_only_run_makes_no_checkpoint(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("get_timeline_info")), reply(text("One clip, 20 seconds.")))
    ctx = _ctx(resolve, project, tmp_path)

    r = loop.run_agent("what's on the timeline?", ctx, client)

    assert r.final_text == "One clip, 20 seconds."
    assert r.checkpoints == [] and r.mutated is False
    assert len(project.timelines) == 1
    assert _results(client, 1)[0]["track_item_counts"]["video1"] == 1


def test_first_edit_is_checkpointed_once_and_original_stays_current(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("add_marker", frame=86500, name="a")),
        reply(call("add_marker", frame=86600, name="b")),
        reply(call("export_frame", frame=86500)),
        reply(text("Added two markers.")))
    ctx = _ctx(resolve, project, tmp_path)

    r = loop.run_agent("mark two spots", ctx, client)

    assert len(r.checkpoints) == 1                       # once per instruction, not per edit
    assert len(project.timelines) == 2
    assert project.current_timeline is tl                # still editing the real one
    assert set(tl.markers) == {100, 200}
    first = _results(client, 1)[0]
    assert first["checkpoint_created"].startswith(safety.CHECKPOINT_PREFIX)
    assert first["state_after"]["marker_count"] == 1     # read-back computed by code
    assert "checkpoint_created" not in _results(client, 2)[0]


def test_edit_is_refused_if_no_checkpoint_can_be_made(tmp_path):
    resolve, project, tl = make_resolve()
    tl.duplicate_timeline_returns = False
    client = ScriptedClient(reply(call("add_marker", frame=86500)), reply(text("Couldn't.")))
    ctx = _ctx(resolve, project, tmp_path)

    loop.run_agent("mark", ctx, client)

    assert tl.markers == {}                              # never edited unprotected
    assert "checkpoint" in _results(client, 1)[0]["error"].lower()


def test_finishing_after_an_edit_without_looking_is_sent_back_to_verify(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("add_marker", frame=86500)),
        reply(text("Done!")),                            # tries to finish unverified
        reply(call("export_frame", frame=86500)),
        reply(text("Marker added and I checked the frame.")))
    ctx = _ctx(resolve, project, tmp_path)

    r = loop.run_agent("mark", ctx, client)

    assert r.verified is True and r.stopped_reason == "done"
    assert "haven't looked" in client.requests[2]["messages"][-1]["content"]
    assert r.final_text.startswith("Marker added")


def test_the_model_is_actually_shown_the_frame(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(reply(call("export_frame", frame=86500)), reply(text("Looks fine.")))
    ctx = _ctx(resolve, project, tmp_path)

    loop.run_agent("check frame", ctx, client)

    blocks = client.requests[1]["messages"][-1]["content"][0]["content"]
    assert any(b["type"] == "image" and b["source"]["media_type"] == "image/png" for b in blocks)
    assert tl.timecode == "01:00:04:04"                  # playhead really moved first


def test_stubborn_unverified_run_ends_flagged_not_looping(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("add_marker", frame=86500)),
        reply(text("done")), reply(text("done")), reply(text("done")))
    ctx = _ctx(resolve, project, tmp_path)

    r = loop.run_agent("mark", ctx, client)

    assert r.stopped_reason == "unverified" and r.verified is False


def test_deleting_asks_and_a_no_means_no(tmp_path):
    resolve, project, tl = make_resolve()
    tl.markers[100] = {"color": "Blue"}
    asked = []
    client = ScriptedClient(reply(call("delete_marker", frame=86500)), reply(text("Left it.")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: asked.append(d) or False)

    loop.run_agent("delete the marker", ctx, client)

    assert len(asked) == 1 and 100 in tl.markers
    assert len(project.timelines) == 1                   # declined => not even a checkpoint
    assert "declined" in _results(client, 1)[0]["error"]


def test_deleting_after_a_yes_checkpoints_then_deletes(tmp_path):
    resolve, project, tl = make_resolve()
    tl.markers[100] = {"color": "Blue"}
    client = ScriptedClient(
        reply(call("delete_marker", frame=86500)), reply(call("export_frame")), reply(text("Gone.")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: True)

    r = loop.run_agent("delete the marker", ctx, client)

    assert 100 not in tl.markers and len(r.checkpoints) == 1


def test_only_deleting_and_rendering_ever_ask(tmp_path):
    resolve, project, tl = make_resolve()
    asked = []
    client = ScriptedClient(
        reply(call("add_marker", frame=86500), call("get_markers"), call("list_media_pool"),
              call("queue_render", preset="H.264 Master", target_dir="/tmp/out"),
              call("set_playhead", frame=86500), call("export_frame")),
        reply(text("ok")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: asked.append(d) or False)

    loop.run_agent("do things", ctx, client)

    assert asked == []


def test_starting_a_render_asks_and_declining_does_not_render(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(reply(call("start_render")), reply(text("Not rendering.")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: False)

    loop.run_agent("render", ctx, client)

    assert project.rendering is False


def test_render_queue_then_confirmed_start(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("list_render_presets")),
        reply(call("queue_render", preset="Vertical 9:16", target_dir="/out", custom_name="short")),
        reply(call("start_render")), reply(call("get_render_status", job_id="job1")),
        reply(text("Rendering.")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: True)

    loop.run_agent("render a vertical", ctx, client)

    assert project.jobs == ["job1"] and project.rendering is True
    assert project.render_settings == {"TargetDir": "/out", "CustomName": "short"}


def test_undo_restores_the_checkpoint_and_keeps_the_edit(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("add_marker", frame=86500)), reply(call("export_frame")),
        reply(call("undo_last_checkpoint")), reply(text("Undone.")))
    ctx = _ctx(resolve, project, tmp_path)

    loop.run_agent("mark then undo", ctx, client)

    assert project.current_timeline.GetName().startswith(safety.CHECKPOINT_PREFIX)
    assert tl in project.timelines and 100 in tl.markers   # nothing deleted


def test_checkpoints_cannot_be_deleted(tmp_path):
    resolve, project, tl = make_resolve()
    cp = tl.DuplicateTimeline(f"{safety.CHECKPOINT_PREFIX} 000000 - x")
    client = ScriptedClient(
        reply(call("delete_timeline", name=cp.GetName())), reply(text("Refused.")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: True)

    loop.run_agent("delete it", ctx, client)

    assert cp in project.timelines
    assert "Checkpoints" in _results(client, 1)[0]["error"]


@pytest.mark.parametrize("method", ["DeleteProject", "CloseProject", "DeleteClips", "Quit"])
def test_escape_hatch_never_calls_the_forbidden_methods(tmp_path, method):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("call_resolve_api", target="project_manager", method=method)), reply(text("no")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: True)

    loop.run_agent("nuke it", ctx, client)

    assert "blocked" in _results(client, 1)[0]["error"]


def test_escape_hatch_delete_prefixed_methods_still_ask(tmp_path):
    resolve, project, tl = make_resolve()
    asked = []
    client = ScriptedClient(
        reply(call("call_resolve_api", target="timeline", method="DeleteMarkerAtFrame", args=[5])),
        reply(text("ok")))
    ctx = _ctx(resolve, project, tmp_path, confirm=lambda d: asked.append(d) or False)

    loop.run_agent("x", ctx, client)

    assert len(asked) == 1


def test_escape_hatch_reads_through(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("call_resolve_api", target="timeline", method="GetName")), reply(text("ok")))
    ctx = _ctx(resolve, project, tmp_path)

    loop.run_agent("x", ctx, client)

    assert _results(client, 1)[0]["result"] == "Interview v1"


def test_unknown_tool_and_bad_arguments_come_back_as_errors_not_crashes(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("make_coffee"), call("add_marker", wrong_arg=1)), reply(text("ok")))
    ctx = _ctx(resolve, project, tmp_path)

    loop.run_agent("x", ctx, client)

    errs = _results(client, 1)
    assert "Unknown tool" in errs[0]["error"] and "Bad arguments" in errs[1]["error"]


def test_step_budget_stops_a_runaway(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(*[reply(call("get_timeline_info")) for _ in range(5)])
    ctx = _ctx(resolve, project, tmp_path)

    r = loop.run_agent("forever", ctx, client, max_steps=3)

    assert r.stopped_reason == "max_steps" and r.steps == 3


def test_preferences_persist_and_reach_the_next_run(tmp_path):
    resolve, project, tl = make_resolve()
    first = ScriptedClient(
        reply(call("remember_preference", key="caption_style", value="white, lower third")),
        reply(text("Noted.")))
    loop.run_agent("I like white lower-third captions", _ctx(resolve, project, tmp_path), first)

    second = ScriptedClient(reply(text("hi")))
    loop.run_agent("caption it", _ctx(resolve, project, tmp_path), second)

    assert "caption_style: white, lower third" in second.requests[0]["system"]


def test_edits_are_logged_to_project_memory(tmp_path):
    resolve, project, tl = make_resolve()
    client = ScriptedClient(
        reply(call("add_marker", frame=86500)), reply(call("export_frame")), reply(text("Marked it.")))
    loop.run_agent("mark the start", _ctx(resolve, project, tmp_path), client)

    log = memory.load("Test Project", tmp_path / "mem")["log"]
    assert log[-1]["instruction"] == "mark the start" and log[-1]["summary"] == "Marked it."


def test_read_only_runs_are_not_logged(tmp_path):
    resolve, project, tl = make_resolve()
    loop.run_agent("look", _ctx(resolve, project, tmp_path),
                   ScriptedClient(reply(call("get_timeline_info")), reply(text("ok"))))
    assert memory.load("Test Project", tmp_path / "mem")["log"] == []


def test_theodore_engine_is_reached_through_a_whitelist(tmp_path):
    resolve, project, tl = make_resolve()
    ran = []

    def fake_cli(cmd):
        ran.append(cmd)
        return type("R", (), {"stdout": "Would cut 3 ranges", "stderr": "", "returncode": 0})()

    client = ScriptedClient(
        reply(call("theodore_edit", command="remove-silence", args=["--dry-run"])),
        reply(call("theodore_edit", command="rm", args=["-rf", "/"])),
        reply(call("export_frame")), reply(text("ok")))
    ctx = _ctx(resolve, project, tmp_path, run_cli=fake_cli)

    loop.run_agent("remove silence", ctx, client)

    assert len(ran) == 1 and ran[0][-3:] == ["--project", "Test Project", "--dry-run"]
    assert "Would cut" in _results(client, 1)[0]["output"]
    assert "editing commands" in _results(client, 2)[0]["error"]


def test_failed_cli_is_reported_to_the_model(tmp_path):
    resolve, project, tl = make_resolve()
    bad = lambda cmd: type("R", (), {"stdout": "", "stderr": "No transcript", "returncode": 1})()
    client = ScriptedClient(reply(call("theodore_analyze", command="gaps")), reply(text("ok")))
    loop.run_agent("gaps", _ctx(resolve, project, tmp_path, run_cli=bad), client)
    assert "No transcript" in _results(client, 1)[0]["error"]


def test_every_tool_has_a_valid_schema_and_unique_name():
    names = [t.name for t in tools.TOOLS]
    assert len(names) == len(set(names))
    for t in tools.TOOLS:
        assert t.schema["type"] == "object" and t.description
        assert t.kind in (tools.READ, tools.NAVIGATE, tools.SAFE_WRITE, tools.WRITE,
                          tools.DESTRUCTIVE, tools.RENDER)


def test_export_failure_is_reported_not_faked(tmp_path):
    resolve, project, tl = make_resolve()
    project.export_ok = False
    client = ScriptedClient(reply(call("export_frame")), reply(text("ok")))
    loop.run_agent("look", _ctx(resolve, project, tmp_path), client)
    assert "could not export" in _results(client, 1)[0]["error"]


def test_agent_works_end_to_end_over_the_real_bridge(tmp_path, monkeypatch):
    """Scripted Claude -> tools -> bridge client -> real HTTP -> bridge server
    -> fake Resolve. This is the path that runs on the editor's Mac."""
    import importlib.util
    from pathlib import Path

    from theodore.resolve import bridge_client
    from theodore.resolve.connection import ResolveHandles

    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "bh"))
    spec = importlib.util.spec_from_file_location(
        "bridge_script", Path(__file__).resolve().parent.parent / "resolve_scripts" / "Theodore Bridge.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    resolve, project, tl = make_resolve()
    server, _port, _state, hs = script.start_bridge(resolve, {})
    client = bridge_client.BridgeClient(json.loads(Path(hs).read_text()), socket_timeout=15)

    def connect():
        root = client.root()
        pm = root.GetProjectManager()
        proj = pm.GetCurrentProject()
        return ResolveHandles(resolve=root, project_manager=pm, project=proj,
                              timeline=proj.GetCurrentTimeline(), via="bridge",
                              info=client.health())
    try:
        scripted = ScriptedClient(
            reply(call("get_project_info")),
            reply(call("add_marker", frame=86500, name="over the wire")),
            reply(call("export_frame", frame=86500)),
            reply(text("Marked and checked.")))
        ctx = AgentContext(connect=connect, confirm=lambda d: False, project_name="Test Project",
                           memory_root=tmp_path / "mem", scratch_dir=tmp_path / "s")

        r = loop.run_agent("mark it", ctx, scripted)

        assert r.verified and len(r.checkpoints) == 1
        assert 100 in tl.markers and len(project.timelines) == 2
        assert project.current_timeline is tl
        assert _results(scripted, 1)[0]["timelines"] == ["Interview v1"]
        assert any(b["type"] == "image" for b in scripted.requests[3]["messages"][-1]["content"][0]["content"])
    finally:
        client.close()
        server.shutdown()
        server.server_close()
