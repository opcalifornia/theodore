import json

import pytest

from theodore.edit import broll, cleanup, compare, interview, pipeline


class _Tl:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class _H:
    def __init__(self, name):
        self.timeline = _Tl(name)


@pytest.fixture
def world(monkeypatch, tmp_path):
    from theodore import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    d = tmp_path / "references" / "ref"
    d.mkdir(parents=True)
    (d / "blueprint.json").write_text("{}")
    (d / "analysis.json").write_text("{}")
    state = {"current": "Raw", "calls": [], "broll_calls": 0, "fail": None}

    def connect():
        return _H(state["current"])

    def build_story(h, **kw):
        state["calls"].append(("story", kw["reference"], kw["target_seconds"]))
        state["current"] = "Story"
        return {"seconds": 58.0, "title": "T", "logline": "L", "structure": [], "story": [], "alternate_hooks": []}

    def clean_up(h, **kw):
        state["calls"].append(("clean", h.timeline.GetName()))
        state["current"] = "Story - clean"
        return {"cuts": 4}

    def place(h, lib, **kw):
        state["calls"].append(("broll", kw["coverage"], kw["on_duplicate"], kw["reference"]))
        state["broll_calls"] += 1
        return {"placed": [1, 2], "coverage_pct": 20.0, "refused": [], "notes": None}

    def check(h, ref):
        short = state["broll_calls"] < 2
        row = {"measure": "B-roll share of runtime (%)", "edit": 20.0 if short else 38.0, "reference": 40.0,
               "matches": not short and True}
        return {"rows": [row], "verdict": "1 measure(s) off" if short else "close match",
                "fixes": ["B-roll: place more cutaways"] if short else []}

    class _Trans:
        @staticmethod
        def add_transitions(h, reference=None, **kw):
            state["calls"].append(("transitions", h.timeline.GetName(), reference))
            if state["fail"] == "transitions":
                raise RuntimeError("Resolve would not import")
            state["current"] = "Story - clean - transitions"
            return {"transitions_added": [1, 3], "notes": [], "warning": None, "editor_loves": None}

    monkeypatch.setattr(interview, "build_story", build_story)
    monkeypatch.setattr(interview, "find_clips", lambda h, names, b: ["clip"])
    monkeypatch.setattr(cleanup, "clean_up", clean_up)
    monkeypatch.setattr(broll, "place_broll", place)
    monkeypatch.setattr(compare, "check", check)
    from theodore.resolve import transitions
    monkeypatch.setattr(transitions, "add_transitions", _Trans.add_transitions)
    return state, connect


def test_runs_the_chain_in_order_and_fills_broll_gaps_once(world):
    state, connect = world
    out = pipeline.cut_like_reference(connect, "ref", target_seconds=45, bin="Interviews", broll_bin="B-roll")
    kinds = [c[0] for c in state["calls"]]
    assert kinds == ["story", "clean", "broll", "broll", "transitions"]
    assert state["calls"][1] == ("clean", "Story")                 # worked on the timeline the story built
    assert state["calls"][2][2] is False                           # B-roll goes on our own timeline, no extra copy
    assert state["calls"][3][1] == pytest.approx(0.4)              # second pass asks for the share the reference has
    assert state["calls"][4] == ("transitions", "Story - clean", "ref")
    assert [s["step"] for s in out["steps"]] == ["story", "clean-up", "b-roll", "b-roll (fill the gaps)", "transitions"]
    assert out["final_timeline"] == "Story - clean - transitions" and out["verdict"] == "close match"
    assert "stopped_at" not in out


def test_a_failure_keeps_earlier_work_and_says_where_it_stopped(world):
    state, connect = world
    state["fail"] = "transitions"
    out = pipeline.cut_like_reference(connect, "ref", bin="Interviews", broll_bin="B-roll")
    assert out["stopped_at"] == "transitions"
    assert out["steps"][-1]["ok"] is False and "would not import" in out["steps"][-1]["error"]
    assert [s["ok"] for s in out["steps"]] == [True, True, True, True, False]


def test_skips_what_was_not_asked_for_and_checks_the_reference_first(world):
    state, connect = world
    out = pipeline.cut_like_reference(connect, "ref", use_timeline=True, story=False, clean_up=False,
                                      transitions=False)
    assert state["calls"] == [] and out["verdict"] == "1 measure(s) off"
    with pytest.raises(FileNotFoundError):
        pipeline.cut_like_reference(connect, "missing")
