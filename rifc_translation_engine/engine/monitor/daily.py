"""The daily run: scan if needed, and always make sure today's brief exists.

A fresh scan is not a reason to skip the brief: if you opened the app last evening the 07:15 job still has to write
this morning's brief."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import store


def last_good_scan(path=None) -> datetime | None:
    d = store.query("SELECT MAX(finished_at) t FROM rm_scans WHERE status IN ('ok','partial')", path=path)["t"].iloc[0]
    return None if pd.isna(d) else pd.Timestamp(d).to_pydatetime()


def run_daily(ctx=None, path=None, trigger: str = "manual", if_stale_hours: float = 0, http=None, sources=None, now: datetime | None = None,
              echo: bool = True, force_brief: bool = False) -> dict:
    from .brief import build_brief
    from .profile import load_context
    from .scan import run_scan
    now = now or datetime.now()
    store.ensure_schema(path, log=lambda m: None)
    out = {"scan": None, "brief_written": False, "scan_summary": None, "brief": None}
    last = last_good_scan(path)
    fresh = bool(if_stale_hours and last and (now - last) < timedelta(hours=if_stale_hours))
    if ctx is None:
        ctx = load_context(progress=lambda m: None, with_projections=True)
    if fresh:
        out["scan"] = "skipped-fresh"
    else:
        s = run_scan(trigger=trigger, ctx=ctx, http=http, sources=sources, now=now, path=path, echo=echo)
        out["scan"], out["scan_summary"] = s.get("status"), s
        if s.get("status") == "skipped":                      # another scan is running: it will write the brief
            return out
    have = store.query("SELECT COUNT(*) n FROM rm_briefs WHERE brief_date = ?", [now.date()], path=path)["n"].iloc[0]
    if out["scan"] != "skipped-fresh" or have == 0 or force_brief:
        out["brief"] = build_brief(ctx, today=now.date(), path=path, now=now)
        out["brief_written"] = True
    return out
