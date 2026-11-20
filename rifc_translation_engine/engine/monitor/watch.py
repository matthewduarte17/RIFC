"""changedetection.io integration.

Role split (verified against the real tool, see README_MONITOR.md):
  * changedetection.io fetches each watched page, keeps only the section we ask for (menus, ads, cookie banners and
    footers are removed), ignores cosmetic text, and stores a snapshot when the section really changed.
  * This module reads those snapshots and does the football part: new headlines go through the normal article
    reader; roster name sets are compared and any appearance or disappearance becomes an UNCONFIRMED event.

How it runs: only during a scan. It is started on 127.0.0.1 with one fetch worker, the update check that would send an
install id to its maker is switched off, watches are synced, each is checked once, results are read, and the process
is stopped. Nothing keeps running between scans and no port stays open.

It lives in its own virtual environment (.venv-watch) so its many packages cannot disturb the app. It is installed
only after you agree (see setup()).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .. import config
from . import store
from .htmlbits import ngin_items
from .http import USER_AGENT
from .roster import diff_rosters, parse_roster_text

CD_VERSION = "0.70.4"          # the version this integration was tested with
PORT_WAIT_S = 60

CONSENT = """changedetection.io (free, open source) will be installed so the monitor can watch club pages that have no news feed.

  What it costs:      nothing to buy. It uses about 550 MB of disk in {venv}, a few minutes to install, and
                      a few minutes of your computer's time during each scan (about one request per watched page,
                      one page at a time, about one per second).
  What it runs as:    NOT a background service. It starts when a scan starts and stops when the scan ends. No web
                      address stays open. Pages are fetched with plain requests, so no web browser is installed.
  Does the Mac need to be awake?  Only while a scan runs, the same as the scan itself: if the Mac is asleep at the
                      scheduled time the scan runs when it wakes. It checks nothing while the Mac sleeps or is off,
                      so a roster change is seen at the next scan after the Mac is awake, not at the moment it happens.
  What it sends out:  requests to the public pages being watched, identifying itself as RIFC-recruitment-monitor.
                      Its own update check (which would send an install id and watch count to its maker) is switched off.
  What it cannot do:  read pages whose player lists are drawn by JavaScript (that needs a browser, which this does not
                      install). Those pages show as 'not covered' on the Monitor Status page.
"""

# ---------------------------------------------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------------------------------------------
def venv_dir() -> Path:
    return config.DATA_DIR.parent / ".venv-watch"


def datastore_dir() -> Path:
    return config.DATA_DIR / "watch"


def executable(cfg: dict | None = None) -> Path | None:
    cfg = cfg or store.load_config()
    cmd = (cfg.get("watch") or {}).get("command")
    if cmd:
        return Path(cmd) if Path(cmd).exists() else None
    p = venv_dir() / "bin" / "changedetection.py"
    return p if p.exists() else None


def status(cfg: dict | None = None) -> dict:
    exe = executable(cfg)
    return {"installed": exe is not None, "executable": str(exe) if exe else None, "venv": str(venv_dir()),
            "enabled": bool((cfg or store.load_config()).get("watch", {}).get("enabled")), "tested_version": CD_VERSION}


def setup(assume_yes: bool = False, input_fn=input, run=subprocess.run, log=print) -> dict:
    """Shows what installing costs, asks, then installs into its own virtual environment. Nothing is installed without a yes."""
    log(CONSENT.format(venv=venv_dir()))
    if not assume_yes:
        ans = (input_fn("Install changedetection.io now? [y/N] ") or "").strip().lower()
        if ans not in ("y", "yes"):
            return {"ok": False, "message": "Not installed. Nothing was changed."}
    v = venv_dir()
    if not (v / "bin" / "python").exists():
        r = run([sys.executable, "-m", "venv", str(v)], capture_output=True, text=True)
        if r.returncode != 0:
            return {"ok": False, "message": "Could not create the environment: " + (r.stderr or "")[-400:]}
    r = run([str(v / "bin" / "python"), "-m", "pip", "install", "--quiet", f"changedetection.io=={CD_VERSION}"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "message": "The install failed (are you online?): " + (r.stderr or "")[-400:]}
    cfg = store.load_config()
    cfg.setdefault("watch", {})["enabled"] = True
    store.save_config(cfg)
    return {"ok": True, "message": f"Installed changedetection.io {CD_VERSION} in {v}. Page watching is on; it starts with the next scan."}


# ---------------------------------------------------------------------------------------------------------------
# the process
# ---------------------------------------------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Runner:
    """changedetection.io for the length of one scan."""

    def __init__(self, exe: Path, datastore: Path, first_url: str | None = None, allow_local: bool = False, log_file: Path | None = None):
        self.exe, self.ds, self.first_url, self.allow_local = Path(exe), Path(datastore), first_url, allow_local
        self.proc, self.port, self.token = None, None, None
        self.log_file = log_file or (config.DATA_DIR.parent / "logs" / "changedetection.log")

    def __enter__(self):
        self.ds.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        env = dict(os.environ, DISABLE_VERSION_CHECK="yes", FETCH_WORKERS="1", LOGGER_LEVEL="WARNING")
        if self.allow_local:
            env["ALLOW_IANA_RESTRICTED_ADDRESSES"] = "true"
        args = [str(self.exe), "-d", str(self.ds), "-p", str(self.port), "-h", "127.0.0.1"]
        if not (self.ds / "changedetection.json").exists() and self.first_url:
            args += ["-u", self.first_url]                  # a fresh datastore would otherwise add two sample watches
        self._log = open(self.log_file, "a")
        self.proc = subprocess.Popen(args, env=env, stdout=self._log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < PORT_WAIT_S:
            if self.proc.poll() is not None:
                raise RuntimeError(f"changedetection.io stopped at start-up (see {self.log_file})")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=2).read(1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("changedetection.io did not start in time")
        for _ in range(40):
            try:
                self.token = json.loads((self.ds / "changedetection.json").read_text())["settings"]["application"]["api_access_token"]
                break
            except Exception:
                time.sleep(0.25)
        if not self.token:
            raise RuntimeError("could not read the changedetection.io API key from its datastore")
        return self

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self._log.close()
        except Exception:
            pass

    def api(self, method: str, path: str, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/v1{path}", method=method, data=data,
                                     headers={"x-api-key": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode()
                try:
                    return r.status, json.loads(t)
                except Exception:
                    return r.status, t
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:300]

    def wait_idle(self, max_s: float = 120) -> bool:
        t0 = time.time()
        while time.time() - t0 < max_s:
            st, s = self.api("GET", "/systeminfo")
            if st == 200 and s.get("queue_size", 1) == 0 and not s.get("checking_now"):
                return True
            time.sleep(0.5)
        return False


# ---------------------------------------------------------------------------------------------------------------
# what to watch
# ---------------------------------------------------------------------------------------------------------------
NOISE = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", ".ad", ".ads", ".advert", "[class*=cookie]",
         "[class*=banner]", "[class*=sponsor]", "[id*=cookie]", "[class*=newsletter]", "[class*=popup]", "[class*=social]"]
IGNORE = [r"/Last updated:.*/i", r"/Updated:?\s+\w+ \d{1,2},? \d{4}.*/i", r"/Visitors? (today|online):?\s*[\d,]+/i", r"/©\s*\d{4}.*/i",
          r"/\b\d+\s+(minutes?|hours?|days?)\s+ago\b/i", r"/Copyright.*/i"]
PROFILES = {
    "ngin_news": {"include_filters": [".newsAggregatorElement"], "subtractive_selectors": NOISE},
    "generic_news": {"include_filters": ["main"], "subtractive_selectors": NOISE},
    "generic_roster": {"include_filters": ["main"], "subtractive_selectors": NOISE},
}


@dataclass
class Spec:
    watch_id: str
    club_id: str
    club_name: str
    league: str
    role: str                    # news | tracker | roster
    url: str
    profile: str
    ingest_baseline: bool = False
    include_filters: list = field(default_factory=list)
    subtractive_selectors: list = field(default_factory=list)

    def body(self) -> dict:
        p = PROFILES[self.profile]
        return {"url": self.url, "title": f"{self.club_name} {self.role}", "paused": False, "fetch_backend": "html_requests",
                "include_filters": self.include_filters or p["include_filters"],
                "subtractive_selectors": self.subtractive_selectors or p["subtractive_selectors"], "ignore_text": IGNORE,
                "headers": {"User-Agent": USER_AGENT}, "time_between_check": {"weeks": 4, "days": 0, "hours": 0, "minutes": 0, "seconds": 0},
                "trim_text_whitespace": True, "remove_duplicate_lines": False, "check_unique_lines": False}

    def config_hash(self) -> str:
        return hashlib.sha1(json.dumps(self.body(), sort_keys=True).encode()).hexdigest()[:12]


def specs_from_directory(directory: dict, saved: pd.DataFrame | None = None, include_trackers: bool = True) -> list[Spec]:
    """Pages worth watching: every club page verified readable, plus each league's transactions page. A page the check found
    unreadable (script drawn) is NOT watched: it would only ever report an empty page."""
    out = []
    saved = saved if saved is not None else pd.DataFrame()
    for e in directory.get("clubs", []):
        if str(e.get("status", "")).startswith("future"):
            continue
        srcs = {s["kind"]: dict(s) for s in e.get("sources", [])}
        for r in (saved[saved["club_id"] == e["club_id"]].itertuples() if len(saved) else []):
            srcs[r.kind] = {"kind": r.kind, "url": r.url, "verified": {"ok": r.status == "ok"}}
        for kind, role, prof in (("league_news", "news", "ngin_news"), ("club_news", "news", "generic_news"),
                                 ("league_roster", "roster", "generic_roster"), ("club_roster", "roster", "generic_roster")):
            s = srcs.get(kind)
            if s and s.get("url") and s.get("verified", {}).get("ok"):
                if kind.endswith("news") and prof == "generic_news" and "ngin" in json.dumps(s):
                    prof = "ngin_news"
                out.append(Spec(f"{e['club_id']}:{kind}", e["club_id"], e["name"], e["league"], role, s["url"], prof))
    if include_trackers:
        for lg, v in directory.get("leagues", {}).items():
            tx, chk = v.get("transactions_url"), v.get("transactions_check", {})
            if tx and chk.get("ok"):
                out.append(Spec(f"{lg.lower().replace(' ', '-')}:transactions", "", f"{lg} transactions", lg, "tracker", tx, "ngin_news", ingest_baseline=True))
    return out


# ---------------------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------------------
def _snapshot(runner: Runner, uuid: str, ts: str) -> str:
    st, t = runner.api("GET", f"/watch/{uuid}/history/{ts}")
    return t if isinstance(t, str) else json.dumps(t)


def _sync(runner: Runner, specs: list[Spec], path, now: datetime) -> dict:
    st, existing = runner.api("GET", "/watch")
    by_url = {w["url"]: u for u, w in (existing or {}).items()} if isinstance(existing, dict) else {}
    want = {s.url for s in specs}
    for url, u in list(by_url.items()):                        # anything we did not ask for is removed (for example sample watches)
        if url not in want:
            runner.api("DELETE", f"/watch/{u}")
            by_url.pop(url)
    uuids = {}
    with store.conn(path) as con:
        for s in specs:
            row = con.execute("SELECT cd_uuid, config_hash FROM rm_watch WHERE watch_id = ?", [s.watch_id]).fetchone()
            u = by_url.get(s.url)
            if u is None:
                st, res = runner.api("POST", "/watch", s.body())
                if st not in (200, 201) or not isinstance(res, dict) or "uuid" not in res:
                    uuids[s.watch_id] = None
                    continue
                u = res["uuid"]
                st2, _ = runner.api("PUT", f"/watch/{u}", s.body())
            elif row is None or row[1] != s.config_hash():
                runner.api("PUT", f"/watch/{u}", s.body())
            uuids[s.watch_id] = u
            if row is None:
                store.insert(con, "rm_watch", watch_id=s.watch_id, club_id=s.club_id, kind=s.role, url=s.url, cd_uuid=u, created_at=now,
                             consecutive_failures=0, n_changes=0, selector=",".join(s.include_filters or PROFILES[s.profile]["include_filters"]),
                             config_hash=s.config_hash(), role=s.role)
            else:
                con.execute("UPDATE rm_watch SET cd_uuid=?, config_hash=?, url=?, role=? WHERE watch_id=?", [u, s.config_hash(), s.url, s.role, s.watch_id])
    return uuids


def run_watches(ctx, matcher, path=None, now: datetime | None = None, cfg: dict | None = None, http=None, robots=None, process_item=None,
                echo=print, specs: list[Spec] | None = None, runner_factory=None, backfill: bool = False) -> dict:
    """Runs every watch once. Returns a summary; never raises for a page or a tool problem (it is reported)."""
    from .directory import load_directory
    now = now or datetime.now()
    cfg = cfg or store.load_config()
    out = {"enabled": False, "status": "off", "watches": 0, "checked": 0, "failed": 0, "changes": 0, "baselined": 0, "events": 0,
           "anomalies": [], "failures": [], "message": ""}
    wcfg = cfg.get("watch", {})
    exe = executable(cfg)
    if not wcfg.get("enabled"):
        out["message"] = "Page watching is off (turn it on with 'watch setup')."
        return out
    if exe is None and runner_factory is None:
        out.update(status="failed", message="Page watching is on but changedetection.io is not installed here. Run 'watch setup'.")
        return out
    out["enabled"] = True
    if specs is None:
        saved = store.query("SELECT * FROM rm_club_source", path=path)
        specs = specs_from_directory(load_directory(), saved)
    out["watches"] = len(specs)
    if not specs:
        out.update(status="ok", message="No pages are verified as watchable yet. Run 'directory verify'.")
        return out
    factory = runner_factory or (lambda: Runner(exe, datastore_dir(), first_url=specs[0].url, allow_local=bool(wcfg.get("allow_local"))))
    try:
        with factory() as rn:
            uuids = _sync(rn, specs, path, now)
            order = [s for s in specs if uuids.get(s.watch_id)]
            for s in order:
                rn.api("GET", f"/watch/{uuids[s.watch_id]}?recheck=1")
                if not rn.wait_idle(float(wcfg.get("page_timeout_s", 90))):
                    break
                time.sleep(float(wcfg.get("page_delay_s", 0.5)))
            rn.wait_idle(30)
            for s in specs:
                u = uuids.get(s.watch_id)
                try:
                    r = _harvest_one(rn, s, u, ctx, matcher, path, now, http, robots, process_item, backfill)
                except Exception as exc:
                    r = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                out["checked"] += 1
                if r["status"] != "ok":
                    out["failed"] += 1
                    out["failures"].append(f"{s.club_name} {s.role}: {r.get('error')}")
                out["changes"] += r.get("changes", 0)
                out["baselined"] += int(bool(r.get("baselined")))
                out["events"] += r.get("events", 0)
                if r.get("anomaly"):
                    out["anomalies"].append(f"{s.club_name} {s.role}: {r['anomaly']}")
        out["status"] = "ok" if out["failed"] == 0 else ("partial" if out["failed"] < out["checked"] else "failed")
    except Exception as exc:
        out.update(status="failed", message=f"{type(exc).__name__}: {exc}")
    return out


def _fail(path, watch_id, now, msg):
    with store.conn(path) as con:
        con.execute("UPDATE rm_watch SET last_checked=?, last_status='failed', last_error=?, consecutive_failures=COALESCE(consecutive_failures,0)+1 WHERE watch_id=?",
                    [now, msg[:300], watch_id])


def _harvest_one(rn: Runner, s: Spec, uuid: str | None, ctx, matcher, path, now, http, robots, process_item, backfill) -> dict:
    if uuid is None:
        _fail(path, s.watch_id, now, "changedetection.io would not accept this watch")
        return {"status": "failed", "error": "the watch could not be created"}
    st, w = rn.api("GET", f"/watch/{uuid}")
    if st != 200 or not isinstance(w, dict):
        _fail(path, s.watch_id, now, f"watch lookup failed (HTTP {st})")
        return {"status": "failed", "error": f"watch lookup failed (HTTP {st})"}
    err = w.get("last_error")
    st, hist = rn.api("GET", f"/watch/{uuid}/history")
    stamps = sorted(hist, key=int) if isinstance(hist, dict) else []
    if err or not stamps:
        msg = str(err) if err else "no snapshot was stored (the page could not be fetched or the selected section is not on the page)"
        _fail(path, s.watch_id, now, msg)
        return {"status": "failed", "error": msg}
    with store.conn(path) as con:
        row = con.execute("SELECT baseline_at, last_snapshot FROM rm_watch WHERE watch_id = ?", [s.watch_id]).fetchone()
    base_at, last = row
    new_stamps = [t for t in stamps if (last is None or int(t) > int(last))]
    res = {"status": "ok", "changes": 0, "events": 0}
    if base_at is None or pd.isna(base_at):                      # first successful look: remember it, report nothing as a change
        res["baselined"] = True
        if s.ingest_baseline and process_item and s.role in ("news", "tracker"):
            res["events"] += _ingest_news(s, None, "", ctx, matcher, path, now, http, robots, process_item, backfill, baseline=True)
        with store.conn(path) as con:
            con.execute("UPDATE rm_watch SET baseline_at=?, last_snapshot=?, last_checked=?, last_success=?, last_status='ok', last_error=NULL, "
                        "consecutive_failures=0 WHERE watch_id=?", [now, stamps[-1], now, now, s.watch_id])
        return res
    prev_ts = last
    for ts in new_stamps:
        new_text = _snapshot(rn, uuid, ts)
        old_text = _snapshot(rn, uuid, prev_ts) if prev_ts else ""
        prev_ts = ts
        res["changes"] += 1
        if s.role == "roster":
            res.update(_roster_change(s, old_text, new_text, ts, ctx, matcher, path, now, res))
        else:
            added = [l for l in new_text.splitlines() if l.strip() and l not in set(old_text.splitlines())]
            res["events"] += _ingest_news(s, ts, "\n".join(added), ctx, matcher, path, now, http, robots, process_item, backfill)
            _log_change(path, s, ts, now, added, [], False, "")
    with store.conn(path) as con:
        con.execute("UPDATE rm_watch SET last_snapshot=?, last_checked=?, last_success=?, last_status='ok', last_error=NULL, consecutive_failures=0, "
                    "n_changes=COALESCE(n_changes,0)+?, last_change_at=CASE WHEN ?>0 THEN ? ELSE last_change_at END WHERE watch_id=?",
                    [stamps[-1], now, now, res["changes"], res["changes"], now, s.watch_id])
    return res


def _log_change(path, s: Spec, ts, now, added, removed, cosmetic, note):
    with store.conn(path) as con:
        store.insert(con, "rm_watch_change", change_id=store.next_id(con, "rm_change_seq"), watch_id=s.watch_id, detected_at=now, snapshot_to=ts,
                     added_json=json.dumps(added[:60]), removed_json=json.dumps(removed[:60]), cosmetic_only=cosmetic, note=note)


def _ingest_news(s: Spec, ts, added_text: str, ctx, matcher, path, now, http, robots, process_item, backfill, baseline=False) -> int:
    """A news or transactions page changed: read its list with our own parser and pass new items to the normal reader."""
    if http is None or process_item is None:
        return 0
    ok, why = robots.allowed(s.url)
    if not ok:
        return 0
    r = http.get(s.url, max_bytes=3_000_000)
    if not r.ok:
        return 0
    items = ngin_items(r.text, s.url)
    if not baseline and added_text:
        titles = {l.strip() for l in added_text.splitlines()}
        items = [i for i in items if i["title"] in titles] or items[:10]
    n = 0
    from .sources import Source
    src = Source(f"watch:{s.watch_id}", f"Watched page: {s.club_name} {s.role}", "watch", s.url, s.league)
    for it in items:
        it = dict(it, source_id=src.id, league_hint=s.league, club_hint=s.club_name if s.role == "news" else None, headline_only=False)
        process_item(it, src)
        n += 1
    return n


def _roster_change(s: Spec, old_text, new_text, ts, ctx, matcher, path, now, res) -> dict:
    from .scan import record_roster_events
    from .clubs import club_key
    ex = {club_key(c) for c in ([ctx.catalog.by_key[k]["name"] for k in ctx.catalog.by_key] if ctx.catalog else [])}
    old, new = parse_roster_text(old_text, ex), parse_roster_text(new_text, ex)
    d = diff_rosters(old, new)
    if d.anomaly:
        _log_change(path, s, ts, now, [], [], False, d.anomaly)
        return {"anomaly": d.anomaly}
    if d.empty:
        _log_change(path, s, ts, now, [], [], True, "Page text changed but no player name did (cosmetic).")
        return {}
    _log_change(path, s, ts, now, [r.name for r in d.added], [r.name for r in d.removed], False, "")
    n = record_roster_events(s, d, ts, ctx, matcher, path, now)
    res["events"] += n
    return {}
