"""Resolve doctor: if Resolve quit or froze, say why (from its own log), relaunch it, reopen the project, restart the bridge.
Usage: resolve_doctor.py ["Project name"]   (default: the project that was open when it died, read from the log)
Never kills fuscript while Resolve runs. Never renders. A running-but-wedged Resolve is reported, not force-quit."""
import os, re, subprocess, sys, time
from pathlib import Path
LOGS = Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/logs"
BRIDGE = Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/Theodore Bridge.py"
APP = "DaVinci Resolve"

def running(): return subprocess.run(["pgrep", "-f", "DaVinci Resolve.app/Contents/MacOS/Resolve"], capture_output=True).returncode == 0

def last_session():
    """(project, last log line time, last meaningful lines) from the previous session's log."""
    for name in ("davinci_resolve.log.1", "davinci_resolve.log"):
        p = LOGS / name
        if not p.exists(): continue
        lines = p.read_text(errors="replace").splitlines()
        proj = None
        for ln in reversed(lines):
            m = re.search(r"Start saving project (.+)$", ln)
            if m: proj = m.group(1).strip(); break
        clean = any("Application will quit" in l or "Quitting" in l or "Shutdown" in l for l in lines[-60:])
        tail = [l.split("|", 3)[-1].strip()[:150] for l in lines[-6:]]
        ts = re.search(r"\| (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", lines[-1] if lines else "")
        return proj, ts.group(1) if ts else "?", clean, tail
    return None, "?", False, []

def connect_any():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from theodore.resolve.connection import connect
    return connect(require_project=False, require_timeline=False)

def main():
    want = sys.argv[1] if len(sys.argv) > 1 else None
    proj, ts, clean, tail = last_session()
    print(f"Previous session: project '{proj}', last log entry {ts}, {'clean shutdown' if clean else 'NO shutdown lines (hard crash or force quit)'}")
    for t in tail: print("   ", t)
    if not running():
        print("Resolve is not running: relaunching.")
        subprocess.run(["open", "-a", APP])
    else:
        print("Resolve is running.")
    h = None
    for i in range(60):
        try:
            h = connect_any(); break
        except Exception as e:
            time.sleep(3)
    if h is None: print("Resolve did not accept scripting after 3 minutes: it may be at a dialog or the project manager. Stop and tell the editor."); return 2
    r = getattr(h, "resolve", h); pm = r.GetProjectManager()
    target = want or proj
    cur = pm.GetCurrentProject()
    if target and (cur is None or cur.GetName() != target):
        print("Loading", target, bool(pm.LoadProject(target)))
    cur = pm.GetCurrentProject(); print("Open project:", cur.GetName() if cur else None, "| timeline:", cur.GetCurrentTimeline().GetName() if cur and cur.GetCurrentTimeline() else None)
    # bridge: with External scripting = Local we can start it ourselves
    fu = r.Fusion()
    if fu is not None and BRIDGE.exists():
        print("Bridge start:", fu.RunScript(str(BRIDGE)) if hasattr(fu, "RunScript") else "no RunScript")
    return 0

if __name__ == "__main__": sys.exit(main())
