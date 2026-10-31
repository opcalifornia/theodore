"""Finding Resolve without being told where it is, and reading its edition."""
from __future__ import annotations

from theodore.resolve import paths


# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------

def test_environment_wins_over_autodetection(monkeypatch, tmp_path):
    # A non-standard install is the one case the user genuinely knows
    # better than we do.
    monkeypatch.setenv("RESOLVE_SCRIPT_API", str(tmp_path / "api"))
    monkeypatch.setenv("RESOLVE_SCRIPT_LIB", str(tmp_path / "lib" / "fusionscript.so"))

    found = paths.detect_scripting_paths(system="Darwin")

    assert found.from_environment is True
    assert found.api_dir == tmp_path / "api"
    assert found.modules_dir == tmp_path / "api" / "Modules"


def test_detection_returns_empty_when_resolve_is_not_installed(monkeypatch):
    monkeypatch.delenv("RESOLVE_SCRIPT_API", raising=False)
    monkeypatch.delenv("RESOLVE_SCRIPT_LIB", raising=False)

    # This sandbox has no Resolve, which is exactly the case under test.
    found = paths.detect_scripting_paths(system="Darwin")

    assert found.api_dir is None
    assert found.complete is False
    assert found.from_environment is False


def test_incomplete_environment_paths_are_reported_as_incomplete(monkeypatch, tmp_path):
    monkeypatch.setenv("RESOLVE_SCRIPT_API", str(tmp_path / "nonexistent"))
    monkeypatch.delenv("RESOLVE_SCRIPT_LIB", raising=False)

    found = paths.detect_scripting_paths(system="Linux")

    assert found.from_environment is True
    assert found.complete is False  # pointed somewhere, but nothing is there


def test_unknown_platform_yields_nothing_rather_than_guessing(monkeypatch):
    monkeypatch.delenv("RESOLVE_SCRIPT_API", raising=False)
    monkeypatch.delenv("RESOLVE_SCRIPT_LIB", raising=False)
    assert paths.detect_scripting_paths(system="Plan9").api_dir is None


# ---------------------------------------------------------------------------
# Where the bridge script has to be installed
# ---------------------------------------------------------------------------

def test_scripts_dirs_are_known_for_each_real_platform():
    for system in ("Darwin", "Windows", "Linux"):
        assert paths.scripts_dirs(system), f"no Scripts folder known for {system}"


def test_scripts_dirs_put_the_per_user_path_first_on_mac():
    # Installing there needs no admin rights, which matters when we're
    # telling a non-technical editor where to copy a file.
    first = str(paths.scripts_dirs("Darwin")[0])
    assert first.startswith(str(paths.Path.home()))


def test_bridge_install_dir_is_a_theodore_subfolder():
    install = paths.bridge_install_dir("Darwin")
    assert install is not None
    assert install.name == "Theodore"
    assert "Fusion/Scripts/Utility" in str(install)


def test_bridge_install_dir_is_none_on_an_unknown_platform():
    assert paths.bridge_install_dir("Plan9") is None


# ---------------------------------------------------------------------------
# Version / edition
# ---------------------------------------------------------------------------

def test_parse_version_handles_the_shapes_resolve_actually_reports():
    assert paths.parse_version("20.3.1 Build 5") == (20, 3, 1)
    assert paths.parse_version("19.1") == (19, 1, 0)
    assert paths.parse_version("18") == (18, 0, 0)
    assert paths.parse_version("DaVinci Resolve 20.0.1") == (20, 0, 1)


def test_parse_version_returns_none_rather_than_guessing():
    assert paths.parse_version(None) is None
    assert paths.parse_version("") is None
    assert paths.parse_version("unknown") is None


def test_is_studio_reads_the_product_name():
    assert paths.is_studio("DaVinci Resolve Studio") is True
    assert paths.is_studio("davinci resolve studio") is True
    assert paths.is_studio("DaVinci Resolve") is False
    assert paths.is_studio(None) is False


def test_studio_always_supports_external_scripting():
    assert paths.external_scripting_supported("DaVinci Resolve Studio", "20.3.1") is True


def test_free_edition_lost_external_scripting_at_19_1():
    # Blackmagic blocked outside connections on the free edition from
    # 19.1 -- the whole reason the bridge exists.
    assert paths.external_scripting_supported("DaVinci Resolve", "19.0") is True
    assert paths.external_scripting_supported("DaVinci Resolve", "19.1") is False
    assert paths.external_scripting_supported("DaVinci Resolve", "20.3.1") is False


def test_unknown_free_version_is_assumed_unsupported():
    # Better to point the user at the bridge (which always works) than to
    # promise a path that may refuse them.
    assert paths.external_scripting_supported("DaVinci Resolve", None) is False
