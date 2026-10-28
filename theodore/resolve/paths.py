"""Finding DaVinci Resolve's scripting API without being told where it is.

``setup.sh`` already writes ``RESOLVE_SCRIPT_API``/``RESOLVE_SCRIPT_LIB``
into the user's shell profile, but that only helps a shell that has sourced
it. Anything launched from the GUI -- the Electron panel, a script Resolve
itself runs, an app opened from the Dock -- inherits none of it, which is a
large share of how Theodore actually gets started.

So rather than requiring the environment to be right, this module looks in
the documented install locations for each platform and reports what it
finds. The environment still wins when it is set: a non-standard install is
the one case the user genuinely knows better than we do.

This matters less than it used to -- with the bridge, Theodore no longer
needs the external scripting API at all -- but it is still what makes the
fallback path work, and it is what lets Theodore tell the user *why* a
connection failed instead of just that it did.
"""
from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Documented install locations, most-specific first. The all-users path
#: precedes the per-user one on macOS because that is where Resolve's own
#: installer puts the scripting API.
_CANDIDATES = {
    "Darwin": [
        ("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
         "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"),
        ("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
         "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"),
    ],
    "Windows": [
        (r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
         r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"),
    ],
    "Linux": [
        ("/opt/resolve/Developer/Scripting", "/opt/resolve/libs/Fusion/fusionscript.so"),
        ("/home/resolve/Developer/Scripting", "/home/resolve/libs/Fusion/fusionscript.so"),
    ],
}

#: Where Resolve keeps the Scripts folder it scans at launch -- the folder
#: the bridge script has to live in. Reported so Theodore can tell the user
#: exactly where to put it instead of making them match an OS to a path.
_SCRIPTS_DIRS = {
    "Darwin": [
        "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
    ],
    "Windows": [
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility",
    ],
    "Linux": [
        "/opt/resolve/Fusion/Scripts/Utility",
        "~/.local/share/DaVinciResolve/Fusion/Scripts/Utility",
    ],
}


@dataclass
class ScriptingPaths:
    """Where the external scripting API lives, if it does."""

    api_dir: Optional[Path]
    lib_file: Optional[Path]
    modules_dir: Optional[Path]
    from_environment: bool

    @property
    def complete(self) -> bool:
        return bool(
            self.api_dir and self.api_dir.is_dir()
            and self.lib_file and self.lib_file.exists()
        )


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


def detect_scripting_paths(system: Optional[str] = None) -> ScriptingPaths:
    """Locate the scripting API: environment first, then known locations."""
    env_api = os.environ.get("RESOLVE_SCRIPT_API")
    env_lib = os.environ.get("RESOLVE_SCRIPT_LIB")
    if env_api or env_lib:
        api = _expand(env_api) if env_api else None
        return ScriptingPaths(
            api_dir=api,
            lib_file=_expand(env_lib) if env_lib else None,
            modules_dir=(api / "Modules") if api else None,
            from_environment=True,
        )

    system = system or platform.system()
    for api_raw, lib_raw in _CANDIDATES.get(system, []):
        api, lib = _expand(api_raw), _expand(lib_raw)
        if api.is_dir() and lib.exists():
            return ScriptingPaths(
                api_dir=api, lib_file=lib,
                modules_dir=api / "Modules", from_environment=False,
            )

    return ScriptingPaths(None, None, None, from_environment=False)


def scripts_dirs(system: Optional[str] = None) -> list:
    """Candidate Scripts folders, per-user first (no admin rights needed)."""
    system = system or platform.system()
    return [_expand(p) for p in _SCRIPTS_DIRS.get(system, [])]


def bridge_install_dir(system: Optional[str] = None) -> Optional[Path]:
    """Where the bridge script should be installed: ``<Scripts>/Theodore``.

    Prefers a Scripts folder that already exists, so we point at Resolve's
    real one rather than inventing a path it never scans.
    """
    candidates = scripts_dirs(system)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate / "Theodore"
    return (candidates[0] / "Theodore") if candidates else None


def ensure_importable(paths: Optional[ScriptingPaths] = None) -> ScriptingPaths:
    """Put the scripting module on ``sys.path`` for this process.

    Lets the external-scripting fallback work in a GUI-launched process that
    never sourced the shell profile, without asking the user to re-run
    ``setup.sh`` or export anything.
    """
    import sys

    paths = paths or detect_scripting_paths()
    if paths.modules_dir and paths.modules_dir.is_dir():
        modules = str(paths.modules_dir)
        if modules not in sys.path:
            sys.path.insert(0, modules)
    # DaVinciResolveScript reads these itself when it loads the native lib.
    if paths.api_dir and not os.environ.get("RESOLVE_SCRIPT_API"):
        os.environ["RESOLVE_SCRIPT_API"] = str(paths.api_dir)
    if paths.lib_file and not os.environ.get("RESOLVE_SCRIPT_LIB"):
        os.environ["RESOLVE_SCRIPT_LIB"] = str(paths.lib_file)
    return paths


_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(version_string: Optional[str]) -> Optional[tuple]:
    """``"20.3.1 Build 5"`` -> ``(20, 3, 1)``. None if unparseable.

    Resolve's version string format has changed across releases, so this
    takes the leading numeric run and ignores the rest rather than assuming
    a shape.
    """
    if not version_string:
        return None
    match = _VERSION_RE.search(str(version_string))
    if not match:
        return None
    return tuple(int(g) if g else 0 for g in match.groups())


def is_studio(product_name: Optional[str]) -> bool:
    """Resolve reports ``"DaVinci Resolve Studio"`` vs ``"DaVinci Resolve"``."""
    return "studio" in (product_name or "").lower()


def external_scripting_supported(product_name: Optional[str],
                                 version_string: Optional[str]) -> bool:
    """Whether this Resolve can be driven from *outside* its own process.

    Blackmagic gates external scripting to Studio, and from Resolve 19.1 the
    free edition refuses outside connections outright. Theodore's bridge
    sidesteps this entirely by running inside Resolve, which is why it is the
    preferred path -- this predicate exists to explain the fallback's limits,
    not to gate the bridge.
    """
    if is_studio(product_name):
        return True
    version = parse_version(version_string)
    if version is None:
        return False
    return version < (19, 1)
