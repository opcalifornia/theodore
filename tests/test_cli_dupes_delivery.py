"""theodore dupes now shows the measured delivery facts behind a
recommendation (not just which take was picked) when delivery.json
exists, and a clear hint to run `theodore delivery` first when it
doesn't. run_redundancy() itself is monkeypatched out (it's a real Claude
call, already covered by tests/test_redundancy.py) so this only tests the
CLI's own wiring: does it load delivery.json, pass it through, and show
the hint at the right time.
"""
import json

from click.testing import CliRunner

from theodore import registry
from theodore.analyze import redundancy as analyze_redundancy
from theodore.cli import cli

ANALYSIS = {
    "segments": [
        {"id": "s001", "answer_start_utterance": "u001", "answer_end_utterance": "u001", "question_text": "Tell me about the flood."},
        {"id": "s002", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Tell me about the flood."},
    ],
    "selects": [
        {"segment_id": "s001", "strength": 0.82, "delivery_strength": 0.9},
        {"segment_id": "s002", "strength": 0.61},
    ],
    "theme_assignments": {},
}

FAKE_REDUNDANCY_RESULT = {"groups": [
    {"id": "r01", "segment_ids": ["s001", "s002"], "recommended_id": "s001", "reason": "Same flood story."},
]}


def _setup_subject(tmp_path, monkeypatch, with_delivery: bool):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    from theodore import config
    project_dir = config.project_dir("docproj")
    project = registry.load_project(project_dir, project_name="docproj")
    registry.register_subject(project, "haylee")
    registry.save_project(project_dir, project)

    subj_dir = registry.subject_dir(project_dir, "haylee")
    subj_dir.mkdir(parents=True, exist_ok=True)
    (subj_dir / "analysis.json").write_text(json.dumps(ANALYSIS))

    if with_delivery:
        delivery = {"segments": {
            "s001": {"descriptor": "Segment s001 delivery profile:\n  - Onset delay: 1.2s before beginning to answer"},
        }}
        (subj_dir / "delivery.json").write_text(json.dumps(delivery))
    return subj_dir


def test_dupes_shows_delivery_facts_when_delivery_json_exists(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch, with_delivery=True)
    monkeypatch.setattr(analyze_redundancy, "run_redundancy", lambda *a, **k: FAKE_REDUNDANCY_RESULT)

    runner = CliRunner()
    result = runner.invoke(cli, ["dupes", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "-> keep  s001" in result.output
    assert "strength 0.82" in result.output
    assert "delivery 0.90" in result.output
    assert "Onset delay: 1.2s" in result.output
    assert "no delivery.json yet" not in result.output


def test_dupes_hints_to_run_delivery_when_missing(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch, with_delivery=False)
    monkeypatch.setattr(analyze_redundancy, "run_redundancy", lambda *a, **k: FAKE_REDUNDANCY_RESULT)

    runner = CliRunner()
    result = runner.invoke(cli, ["dupes", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "-> keep  s001" in result.output
    assert "no delivery.json yet" in result.output
    assert "theodore delivery" in result.output


def test_dupes_no_groups_skips_the_delivery_hint(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch, with_delivery=False)
    monkeypatch.setattr(analyze_redundancy, "run_redundancy", lambda *a, **k: {"groups": []})

    runner = CliRunner()
    result = runner.invoke(cli, ["dupes", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "No redundant segments found." in result.output
    assert "no delivery.json yet" not in result.output
