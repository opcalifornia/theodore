"""Story cuts that move between subjects: speaker labels, the weave report, and the re-weave pass."""
from theodore.edit import interview


def _sel(who, a, b):
    return {"subject": who, "a": a, "b": b}


def test_subject_label_uses_the_editors_mapping_then_the_clip_name():
    m = {"maria": "Maria", "A001": "Dr. Cole"}
    assert interview.subject_label("Maria_interview.mov", m) == "Maria"
    assert interview.subject_label("A001_C002.mov", m) == "Dr. Cole"
    assert interview.subject_label("Kennedy talk.mp4", m) == "Kennedy talk"


def test_intercut_report_counts_shares_changes_and_longest_run():
    sels = [_sel("A", 0, 48), _sel("A", 48, 96), _sel("B", 96, 120), _sel("A", 120, 144)]
    r = interview.intercut_report(sels, 24.0)
    assert r["subjects"]["A"] == {"seconds": 5.0, "selects": 3}
    assert r["subjects"]["B"]["selects"] == 1
    assert r["speaker_changes"] == 2
    assert r["longest_run"]["subject"] == "A" and r["longest_run"]["selects"] == 2


def test_problems_flag_long_runs_and_silent_subjects_but_not_a_solo_story():
    long_run = interview.intercut_report([_sel("A", i * 24, i * 24 + 24) for i in range(5)], 24.0)
    probs = interview.intercut_problems(long_run, ["A", "B"])
    assert any("in a row" in p for p in probs) and any("B is never heard" in p for p in probs)
    assert interview.intercut_problems(long_run, ["A"]) == []
    woven = interview.intercut_report([_sel("A", 0, 24), _sel("B", 24, 48), _sel("A", 48, 72)], 24.0)
    assert interview.intercut_problems(woven, ["A", "B"]) == []


class _Clip:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


def _words(prefix_text, start):
    out, f = [], start
    for w in prefix_text.split():
        out.append({"word": w, "start": f / 24, "end": (f + 10) / 24, "start_frame": f, "end_frame": f + 10})
        f += 12
    return out


def test_build_story_re_weaves_a_lopsided_first_plan(monkeypatch):
    from theodore.resolve import timeline_ops, transcribe

    texts = {"maria.mov": "One. Two. Three. Four.", "cole.mov": "Alpha. Beta."}

    def fake_transcribe(h, clip, word_level=True):
        words = _words(texts[clip.GetName()], 0)
        return {"fps": 24.0, "words": words}

    monkeypatch.setattr(transcribe, "transcribe_clip", fake_transcribe)
    monkeypatch.setattr(transcribe, "sentences", lambda words: [
        {"text": w["word"], "start": w["start"], "end": w["end"]} for w in words])
    placed = {}
    monkeypatch.setattr(timeline_ops, "assemble_clip_selects",
                        lambda h, sels, title: placed.update(sels=sels) or {"timeline": title,
                                                                           "duration_frames": 100})
    calls = []

    def fake_call_json(client, *, pass_name, **kw):
        calls.append(pass_name)
        if pass_name == "interview-story":
            ids = ["C1.1", "C1.2", "C1.3", "C1.4"]          # all Maria: a four-select run, Cole never heard
        else:
            assert "never heard" in kw["user"] or "in a row" in kw["user"]
            ids = ["C1.1", "C2.1", "C1.2", "C2.2"]
        return {"title": "T", "selects": [{"id": i, "section": "S"} for i in ids]}

    monkeypatch.setattr(interview, "call_json", fake_call_json)
    monkeypatch.setattr(interview, "make_client", lambda: object())
    out = interview.build_story(
        None, clips=[_Clip("maria.mov"), _Clip("cole.mov")], target_seconds=10,
        subjects={"maria": "Maria", "cole": "Cole"})
    assert calls == ["interview-story", "interview-story-weave"]
    assert out["intercut"]["re_woven"] is True and out["intercut"]["problems_left"] == []
    assert out["intercut"]["speaker_changes"] == 3
    assert [s["subject"] for s in out["story"]] == ["Maria", "Cole", "Maria", "Cole"]


def test_story_on_a_multicam_timeline_knows_who_says_each_line(monkeypatch, tmp_path):
    from theodore import config
    from theodore.edit import panel
    from theodore.resolve import timeline_ops, transcribe
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    plan = panel.PanelPlan([], [], 24, 30.0, 0.0, cuts=[(0.0, 30.0, "wide", "x")],
                           speech={"Ana": [(0.0, 2.0)], "Ben": [(2.5, 5.0)]})
    panel.save_speakers(plan, "Episode")

    class TL:
        def GetName(self): return "Episode"
        def GetStartFrame(self): return 86400

    class H:
        timeline = TL()

    words = _words("One. Two.", 0) + _words("Three. Four.", 60)
    monkeypatch.setattr(transcribe, "transcribe_timeline", lambda h, word_level=True: {"fps": 24.0, "words": words})
    monkeypatch.setattr(transcribe, "sentences", lambda ws: [
        {"text": w["word"], "start": w["start"], "end": w["end"]} for w in ws])
    monkeypatch.setattr(timeline_ops, "assemble_timeline_ranges",
                        lambda h, ranges, title, labels=None: {"timeline": title, "duration_frames": 100})
    seen = {}

    def fake_call_json(client, *, user, **kw):
        seen["user"] = user
        return {"title": "T", "selects": [{"id": "T.1", "section": "Hook"}, {"id": "T.4", "section": "Button"}]}

    monkeypatch.setattr(interview, "call_json", fake_call_json)
    monkeypatch.setattr(interview, "make_client", lambda: object())
    out = interview.build_story(H(), use_timeline=True, target_seconds=5)
    assert "Ana: One." in seen["user"] and "Ben: Three." in seen["user"]
    assert [s["subject"] for s in out["story"]][0] == "Ana"
