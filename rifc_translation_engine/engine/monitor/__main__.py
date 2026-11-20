"""Command line: python -m engine.monitor <command>

  run         scan, then write the daily brief (what the scheduled job runs). --if-stale H skips if a good scan is newer.
  scan        scan only            brief   write the brief only       status  last scans and source health
  link URL    read one announcement address        sources  list sources and their state
  schedule    install | remove | status the automatic daily run (Mac)
  export PATH write the whole inbox to a CSV file
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import store


def _last_good(path=None):
    store.ensure_schema(path, log=lambda m: None)
    d = store.query("SELECT MAX(finished_at) t FROM rm_scans WHERE status IN ('ok','partial')", path=path)["t"].iloc[0]
    return None if pd.isna(d) else pd.Timestamp(d).to_pydatetime()


def notify(title: str, text: str):
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'display notification {json.dumps(text)} with title {json.dumps(title)}'],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def cmd_run(a) -> int:
    from .brief import build_brief
    from .profile import load_context
    from .scan import run_scan
    if a.if_stale:
        t = _last_good()
        if t and datetime.now() - t < timedelta(hours=a.if_stale):
            print(f"Last good scan was {t:%Y-%m-%d %H:%M}, less than {a.if_stale} hours ago. Nothing to do.")
            return 0
    ctx = load_context(progress=lambda m: None, with_projections=True)
    s = run_scan(trigger=a.trigger, ctx=ctx)
    if s.get("status") == "skipped":
        return 2
    b = build_brief(ctx)
    n = sum(len(v) for v in b["sections"].values())
    print(f"Brief written: {n} item(s), {b['uncertain_total']} uncertain match(es), {len(b['health']['problems'])} source problem(s).")
    if n or b["health"]["problems"]:
        notify("RIFC recruitment brief", f"{n} item(s) to review" + (f", {len(b['health']['problems'])} source problem(s)" if b["health"]["problems"] else ""))
    return 0 if s.get("status") in ("ok", "partial") else 1


def cmd_status(a) -> int:
    store.ensure_schema(log=lambda m: None)
    print(store.query("SELECT scan_id, started_at, finished_at, status, trigger FROM rm_scans ORDER BY scan_id DESC LIMIT 8").to_string(index=False))
    print()
    print(store.query("SELECT source_id, last_status, last_success, consecutive_failures, items_last, last_error FROM rm_source_state ORDER BY source_id").to_string(index=False))
    return 0


def _ask(question: str, seconds: int = 40) -> str | None:
    import select
    print(question, end=" ", flush=True)
    try:
        r, _, _ = select.select([sys.stdin], [], [], seconds)
    except Exception:
        return None
    if not r:
        print()
        return None
    return sys.stdin.readline().strip().lower()


def _offer_schedule(S, app_dir: str, home: str) -> int:
    """Called by start.command. If the daily scan is on, keep its file pointing at this app folder. If you have never
    been asked, ask once. Answering no (or not answering) is remembered or retried; nothing is ever installed silently."""
    cfg = store.load_config()
    if sys.platform != "darwin":
        return 0
    st = S.status()
    if cfg["schedule"].get("enabled"):
        if not st["installed"] or st.get("app_dir") != app_dir:
            S.install(app_dir, home, hour=cfg["schedule"]["hour"], minute=cfg["schedule"]["minute"])
            print("Automatic daily scan updated for this app folder.")
        return 0
    if cfg["schedule"].get("asked"):
        return 0
    ans = _ask(f"Run the recruitment scan automatically every morning at {cfg['schedule']['hour']:02d}:{cfg['schedule']['minute']:02d} "
               "while you are logged in? It reads public news only and writes a brief. [y/N]")
    if ans is None:
        return 0                                  # no answer: ask again next time
    cfg["schedule"]["asked"] = True
    if ans in ("y", "yes"):
        res = S.install(app_dir, home, hour=cfg["schedule"]["hour"], minute=cfg["schedule"]["minute"])
        cfg["schedule"]["enabled"] = bool(res["ok"])
        print(res["message"])
    else:
        print("Not set up. You can turn it on later in the app: Monitor Status.")
    store.save_config(cfg)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engine.monitor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--if-stale", type=float, default=0); r.add_argument("--trigger", default="manual")
    s = sub.add_parser("scan"); s.add_argument("--backfill", action="store_true"); s.add_argument("--source", action="append")
    b = sub.add_parser("brief"); b.add_argument("--date")
    sub.add_parser("status"); sub.add_parser("sources")
    l = sub.add_parser("link"); l.add_argument("url"); l.add_argument("--league")
    sc = sub.add_parser("schedule"); sc.add_argument("action", choices=["install", "remove", "status", "offer"])
    sc.add_argument("--hour", type=int, default=7); sc.add_argument("--minute", type=int, default=15)
    e = sub.add_parser("export"); e.add_argument("path")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "scan":
        from .scan import run_scan
        return 0 if run_scan(trigger="manual", backfill=a.backfill, only_sources=a.source).get("status") in ("ok", "partial") else 1
    if a.cmd == "link":
        from .scan import scan_link
        return 0 if scan_link(a.url, a.league).get("status") == "ok" else 1
    if a.cmd == "brief":
        from datetime import date
        from .brief import build_brief
        from .profile import load_context
        b_ = build_brief(load_context(with_projections=True), date.fromisoformat(a.date) if a.date else None)
        print(b_["markdown"])
        return 0
    if a.cmd == "sources":
        from .sources import load_sources
        for x in load_sources(store.load_config()):
            print(f"{'on ' if x.enabled else 'off'} {x.id:18} {x.kind:10} {x.name}\n      {x.verified}")
        return 0
    if a.cmd == "export":
        from .inbox import enrich, load_events, sort_for_review, to_csv
        from .profile import load_context
        store.ensure_schema(log=lambda m: None)
        ev = sort_for_review(enrich(load_events(), load_context(with_projections=True)))
        Path(a.path).write_text(to_csv(ev))
        print(f"Wrote {len(ev)} rows to {a.path}")
        return 0
    if a.cmd == "schedule":
        from . import schedule as S
        from .. import config
        app_dir = str(Path(__file__).resolve().parents[2])
        home = str(config.DATA_DIR.parent)
        if a.action == "offer":
            return _offer_schedule(S, app_dir, home)
        if a.action == "install":
            res = S.install(app_dir, home, hour=a.hour, minute=a.minute)
            if res["ok"]:
                cfg = store.load_config(); cfg["schedule"] = {"enabled": True, "hour": a.hour, "minute": a.minute}; store.save_config(cfg)
        elif a.action == "remove":
            res = S.remove()
            cfg = store.load_config(); cfg["schedule"]["enabled"] = False; store.save_config(cfg)
        else:
            res = S.status()
        print(json.dumps(res, indent=1))
        return 0 if res.get("ok", True) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
