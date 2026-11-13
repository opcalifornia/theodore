import json

import pytest

from theodore import registry


def test_load_project_creates_fresh_registry_when_nothing_on_disk(tmp_path):
    project_dir = tmp_path / "acme_doc"
    project = registry.load_project(project_dir, project_name="acme_doc")
    assert project["project"] == "acme_doc"
    assert project["subjects"] == {}
    assert project["addressing"] == "sequential"
    assert (project_dir / "project.json").exists()


def test_load_project_returns_existing_registry_unchanged(tmp_path):
    project_dir = tmp_path / "acme_doc"
    registry.load_project(project_dir, project_name="acme_doc")
    project = registry.load_project(project_dir)
    registry.register_subject(project, "haylee", display_name="Haylee Reyes")
    registry.save_project(project_dir, project)

    reloaded = registry.load_project(project_dir)
    assert "haylee" in reloaded["subjects"]
    assert reloaded["subjects"]["haylee"]["display_name"] == "Haylee Reyes"


def test_register_subject_is_idempotent_and_does_not_reset_counter(tmp_path):
    project = registry._default_project("p")
    registry.register_subject(project, "haylee")
    registry.set_next_question_number(project, "haylee", 5)
    registry.register_subject(project, "haylee")  # re-register, e.g. on a second ingest
    assert registry.next_question_number(project, "haylee") == 5


def test_register_subject_rejects_invalid_id(tmp_path):
    project = registry._default_project("p")
    with pytest.raises(registry.RegistryError):
        registry.register_subject(project, "Haylee Reyes!")


def test_slugify_subject_id():
    assert registry.slugify_subject_id("Haylee Reyes") == "haylee_reyes"
    assert registry.slugify_subject_id("  marcus  ") == "marcus"


def test_slugify_subject_id_raises_on_empty_result():
    with pytest.raises(registry.RegistryError):
        registry.slugify_subject_id("!!!")


def test_require_subject_raises_with_available_list(tmp_path):
    project = registry._default_project("p")
    registry.register_subject(project, "haylee")
    with pytest.raises(registry.RegistryError, match="haylee"):
        registry.require_subject(project, "marcus")


def test_next_question_number_defaults_to_one_for_new_subject():
    project = registry._default_project("p")
    registry.register_subject(project, "haylee")
    assert registry.next_question_number(project, "haylee") == 1


def test_subject_dir_creates_directory(tmp_path):
    project_dir = tmp_path / "acme_doc"
    d = registry.subject_dir(project_dir, "haylee")
    assert d == project_dir / "subjects" / "haylee"
    assert d.is_dir()


def test_is_legacy_project_detects_flat_v1_layout(tmp_path):
    project_dir = tmp_path / "acme_doc"
    project_dir.mkdir()
    assert registry.is_legacy_project(project_dir) is False  # nothing there yet
    (project_dir / "transcript.json").write_text("{}")
    assert registry.is_legacy_project(project_dir) is True


def test_is_legacy_project_false_once_project_json_exists(tmp_path):
    project_dir = tmp_path / "acme_doc"
    project_dir.mkdir()
    (project_dir / "transcript.json").write_text("{}")
    (project_dir / "project.json").write_text("{}")
    assert registry.is_legacy_project(project_dir) is False


def test_migrate_legacy_project_copies_without_deleting_originals(tmp_path):
    project_dir = tmp_path / "acme_doc"
    project_dir.mkdir()
    transcript = {"source_file": "x.wav", "utterances": []}
    (project_dir / "transcript.json").write_text(json.dumps(transcript))
    (project_dir / "media.json").write_text("[]")
    (project_dir / "audio").mkdir()
    (project_dir / "audio" / "abc.wav").write_bytes(b"fake wav")

    project = registry.load_project(project_dir, project_name="acme_doc")

    assert "acme_doc" in project["subjects"]  # migrated subject slug from project name
    subject_id = next(iter(project["subjects"]))
    migrated_transcript = project_dir / "subjects" / subject_id / "transcript.json"
    assert migrated_transcript.exists()
    assert json.loads(migrated_transcript.read_text()) == transcript
    assert (project_dir / "subjects" / subject_id / "audio" / "abc.wav").exists()

    # Originals must still be there, untouched.
    assert (project_dir / "transcript.json").exists()
    assert (project_dir / "media.json").exists()
    assert (project_dir / "audio" / "abc.wav").exists()


def test_migrate_legacy_project_starts_id_counter_above_existing_segments(tmp_path):
    project_dir = tmp_path / "acme_doc"
    project_dir.mkdir()
    (project_dir / "transcript.json").write_text("{}")
    (project_dir / "media.json").write_text("[]")
    (project_dir / "segments.json").write_text(json.dumps({
        "segments": [{"id": "s001"}, {"id": "s002"}, {"id": "s007"}],
    }))

    project = registry.load_project(project_dir, project_name="acme_doc")
    subject_id = next(iter(project["subjects"]))
    assert registry.next_question_number(project, subject_id) == 8


def test_list_subjects(tmp_path):
    project = registry._default_project("p")
    registry.register_subject(project, "haylee")
    registry.register_subject(project, "marcus")
    assert set(registry.list_subjects(project)) == {"haylee", "marcus"}
