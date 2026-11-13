"""Get a finished video onto the editor's phone: copy it into an iCloud Drive folder.

Free, no accounts: iCloud Drive syncs the folder to the iPhone's Files app. Only
review-size videos go there (free iCloud is 5 GB); masters stay where they were
rendered. Deleting here deletes on every device (iCloud keeps it ~30 days).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

MAX_MB = 400


def share_dir() -> Optional[Path]:
    """The folder that syncs to the phone. OFF unless the editor sets THEODORE_SHARE_DIR to it: this Mac's
    iCloud isn't necessarily the account on their phone, so nothing is ever copied by guesswork."""
    env = os.environ.get("THEODORE_SHARE_DIR")
    return Path(env) if env else None


def copy_for_phone(path, max_mb: float = MAX_MB) -> dict:
    """Copy a video into the synced folder. Never raises: returns {"shared": bool, ...} with the reason."""
    src = Path(path)
    dest_dir = share_dir()
    if dest_dir is None:
        return {"shared": False, "reason": "Phone sharing is off (no THEODORE_SHARE_DIR set)."}
    if not src.is_file():
        return {"shared": False, "reason": f"{src} doesn't exist."}
    size_mb = src.stat().st_size / 1_000_000
    if size_mb > max_mb:
        return {"shared": False, "reason": f"{src.name} is {size_mb:.0f} MB, over the {max_mb:.0f} MB phone limit "
                                          "(free iCloud is 5 GB). Make a smaller review copy."}
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / src.name)
    except OSError as exc:
        return {"shared": False, "reason": str(exc)}
    return {"shared": True, "on_phone": f"Files app > iCloud Drive > {dest_dir.name} > {src.name}",
            "path": str(dest_dir / src.name)}


def list_shared() -> list[str]:
    d = share_dir()
    return sorted(p.name for p in d.iterdir() if p.is_file()) if d and d.is_dir() else []


def delete_shared(name: str) -> dict:
    """Delete one file from the synced folder (by plain file name, nothing outside the folder)."""
    d = share_dir()
    if d is None or Path(name).name != name or not (d / name).is_file():
        return {"deleted": False, "reason": f"No file named {name!r} in the phone folder.", "available": list_shared()}
    (d / name).unlink()
    return {"deleted": True, "name": name, "note": "Gone from every device; iCloud keeps it in Recently Deleted ~30 days."}
