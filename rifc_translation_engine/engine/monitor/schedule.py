"""Optional automatic daily run on a Mac, using launchd (built into macOS, nothing to install).

What this does and does not do, accurately:
  * The agent runs when you are logged in. It runs once when you log in (only if the last good scan is older than
    4 hours) and again at the chosen time each day.
  * If the Mac is asleep at that time, launchd runs the missed job when the Mac wakes. If the Mac is powered off, it
    does not run until you next log in (the login run then catches up).
  * It does not run with the lid closed and the Mac asleep, and nothing runs for a user who is logged out.
  * The app does not need to be open: the job is a separate short Python process.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.rifc.recruitment-monitor"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(python: str, app_dir: str, home: str, hour: int = 7, minute: int = 15) -> dict:
    logs = Path(home) / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "engine.monitor", "run", "--if-stale", "4", "--trigger", "schedule"],
        "WorkingDirectory": app_dir,
        "EnvironmentVariables": {"RIFC_TE_HOME": home, "PYTHONPATH": app_dir, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"},
        "StartCalendarInterval": {"Hour": int(hour), "Minute": int(minute)},
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "launchd_out.log"),
        "StandardErrorPath": str(logs / "launchd_err.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def status() -> dict:
    p = plist_path()
    out = {"supported": sys.platform == "darwin", "installed": p.exists(), "plist": str(p)}
    if p.exists():
        try:
            d = plistlib.loads(p.read_bytes())
            out["time"] = "{:02d}:{:02d}".format(d["StartCalendarInterval"]["Hour"], d["StartCalendarInterval"]["Minute"])
            out["app_dir"] = d.get("WorkingDirectory")
            out["app_dir_exists"] = Path(d.get("WorkingDirectory", "")).exists()
        except Exception as exc:
            out["error"] = str(exc)
    return out


def install(app_dir: str, home: str, python: str | None = None, hour: int = 7, minute: int = 15, load: bool = True) -> dict:
    if sys.platform != "darwin":
        return {"ok": False, "message": "Automatic scheduling here uses launchd, which exists only on a Mac. "
                                        "Nothing was changed. Run the scan from the app or with 'python -m engine.monitor run'."}
    python = python or sys.executable
    (Path(home) / "logs").mkdir(parents=True, exist_ok=True)
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(plistlib.dumps(build_plist(python, app_dir, home, hour, minute)))
    msg = f"Daily scan set for {hour:02d}:{minute:02d}. File: {p}"
    if load:
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(p)], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(p)], capture_output=True, text=True)
        if r.returncode != 0:
            r2 = subprocess.run(["launchctl", "load", "-w", str(p)], capture_output=True, text=True)
            if r2.returncode != 0:
                return {"ok": False, "message": f"The schedule file was written ({p}) but launchd refused it: {r.stderr.strip() or r2.stderr.strip()}"}
    return {"ok": True, "message": msg}


def remove() -> dict:
    p = plist_path()
    if sys.platform == "darwin" and p.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], capture_output=True)
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    if p.exists():
        p.unlink()
        return {"ok": True, "message": "Automatic daily scan removed."}
    return {"ok": True, "message": "No automatic scan was set up."}
