"""One scan: fetch sources, read announcements, match players, update the inbox. Safe to run any time, any number of
times: items and events are keyed so repeats never duplicate, and your decisions are never overwritten.

Failure behaviour: one failing source never stops the others; every failure is stored with its reason; each article is
written in one transaction, so an interrupted scan leaves only complete items and the next scan carries on.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .. import config
from ..normalize import norm_name
from . import store
from .classify import (AVAILABILITY_OPENING, MOVEMENT_TYPES, NAME_RE, extract_mentions, headline_type, title_club, Mention)
from .http import Http, Robots
from .match import Matcher, name_compat, name_key, surname_key
from .profile import Ctx, load_context
from .sources import Source, fetch_article, fetch_source, load_sources, url_key

# unmatched names are kept as leads only for announcements that can open availability, plus signings from college
LEAD_TYPES = {"option_declined", "released", "out_of_contract", "departure", "loan_return"}
STALE_RUN_MIN = 30
log_lines: list[str] = []


def _log(msg: str, echo=True):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    log_lines.append(line)
    if echo:
        print(line, flush=True)
    try:
        d = config.DATA_DIR.parent / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "monitor.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def window_of(d: datetime | None) -> str:
    """Offseason window used for dedupe: July to June."""
    d = d or datetime.now()
    return str(d.year if d.month >= 7 else d.year - 1)


def fingerprint(kind_key: str, event_type: str, club_key: str, win: str) -> str:
    return hashlib.sha1(f"{kind_key}|{event_type}|{club_key}|{win}".encode()).hexdigest()[:20]


def _norm_quote(q: str) -> str:
    return re.sub(r"\W+", " ", q.lower()).strip()


# ---------------------------------------------------------------------------------------------------------------
# lock and scan bookkeeping
# ---------------------------------------------------------------------------------------------------------------
def lock_path() -> Path:
    return config.DATA_DIR / "monitor.lock"


def acquire_lock() -> bool:
    p = lock_path()
    if p.exists():
        try:
            d = json.loads(p.read_text())
            age = time.time() - d.get("ts", 0)
            alive = True
            try:
                os.kill(int(d.get("pid", 0)), 0)
            except (OSError, ValueError):
                alive = False
            if alive and age < STALE_RUN_MIN * 60:
                return False
        except Exception:
            pass
    p.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}))
    return True


def touch_lock():
    try:
        lock_path().write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}))
    except Exception:
        pass


def release_lock():
    try:
        lock_path().unlink()
    except Exception:
        pass


def mark_interrupted_scans(path=None):
    cutoff = datetime.now() - timedelta(minutes=STALE_RUN_MIN)
    with store.conn(path) as con:
        n = con.execute("SELECT COUNT(*) FROM rm_scans WHERE status = 'running' AND heartbeat_at < ?", [cutoff]).fetchone()[0]
        if n:
            con.execute("UPDATE rm_scans SET status = 'interrupted', finished_at = ? WHERE status = 'running' AND heartbeat_at < ?",
                        [datetime.now(), cutoff])
    return n


def _record_source(con, res, now):
    sid = res.source.id
    row = con.execute("SELECT consecutive_failures FROM rm_source_state WHERE source_id = ?", [sid]).fetchone()
    ok = res.status == "ok"
    fails = 0 if ok else ((row[0] or 0) + 1 if row else 1)
    if row is None:
        con.execute("INSERT INTO rm_source_state VALUES (?,?,?,?,?,?,?,?,?)",
                    [sid, res.source.name, now, now if ok else None, res.status, res.error, fails, len(res.items), res.source.verified or ""])
    else:
        if ok:
            con.execute("UPDATE rm_source_state SET name=?, last_attempt=?, last_success=?, last_status=?, last_error=?, "
                        "consecutive_failures=0, items_last=?, note=? WHERE source_id=?",
                        [res.source.name, now, now, res.status, None, len(res.items), res.source.verified or "", sid])
        else:
            con.execute("UPDATE rm_source_state SET name=?, last_attempt=?, last_status=?, last_error=?, "
                        "consecutive_failures=?, items_last=?, note=? WHERE source_id=?",
                        [res.source.name, now, res.status, res.error, fails, len(res.items), res.source.verified or "", sid])


# ---------------------------------------------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------------------------------------------
def _upsert_event(con, m: Mention, res, item_id: int, item_row: dict, ctx: Ctx, now: datetime, stats: dict):
    announced = item_row.get("published") or now
    win = window_of(announced)
    ref = ctx.catalog.lookup(m.club_text) if (ctx.catalog and m.club_text) else None
    ck = ref.key if ref else ""                      # same key the matcher uses for your saved answers
    from .clubs import club_key as _ck
    fpclub = _ck(m.club_text) if m.club_text else ""
    nk = name_key(m.name)
    kind_key = "n:" + nk + (("|" + item_row.get("url_key", "")) if m.event_type == "performance" else "")
    fp = fingerprint(kind_key, m.event_type, fpclub, win)
    uid = res.uid if res.status == "matched" else None
    followed = bool(uid and uid in ctx.followed())
    ev = con.execute("SELECT event_id, uid, match_status, user_decided FROM rm_events WHERE fingerprint = ?", [fp]).fetchone()
    if ev is None and uid and m.event_type != "performance":
        ev = con.execute("SELECT event_id, uid, match_status, user_decided FROM rm_events WHERE uid = ? AND event_type = ? "
                         "AND club_norm = ? AND win = ?", [uid, m.event_type, ck, win]).fetchone()
    quote_n = _norm_quote(m.quote)
    cands = json.dumps(res.candidates)
    reasons = json.dumps(res.reasons)
    if ev is None:
        eid = store.next_id(con, "rm_event_seq")
        prior = None
        if uid:
            p = con.execute("SELECT inbox_status, status_changed_at FROM rm_events WHERE uid = ? AND user_decided AND "
                            "inbox_status IN ('Pass','Monitor','Review') ORDER BY status_changed_at DESC LIMIT 1", [uid]).fetchone()
            if p:
                prior = f"You marked an earlier announcement about him '{p[0]}' on {str(p[1])[:10]}."
        saved = con.execute("SELECT status, note FROM rm_event_decisions WHERE fingerprint = ? ORDER BY decided_at DESC LIMIT 1", [fp]).fetchone()
        status, decided, note = (saved[0], True, saved[1]) if saved else ("New", False, "")
        con.execute("INSERT INTO rm_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [eid, fp, m.event_type, m.name, nk, m.club_text, ck, item_row.get("league_hint"), m.position, announced, now, now,
                     1, uid, res.status, res.grade, reasons, cands, m.extraction, status, now, note, decided, False, "", prior,
                     m.quote, hashlib.sha1(quote_n.encode()).hexdigest()[:12], followed, win])
        con.execute("INSERT INTO rm_event_evidence VALUES (?,?,?,?,?)", [eid, item_id, m.quote, m.name, now])
        stats["new_events"] += 1
        stats["by_match"][res.status] = stats["by_match"].get(res.status, 0) + 1
        return eid
    eid = ev[0]
    already = con.execute("SELECT quote FROM rm_event_evidence WHERE event_id = ? AND item_id = ?", [eid, item_id]).fetchone()
    if already is not None and _norm_quote(already[0]) != quote_n:
        # the same article now says something different about him: that is an update, not a repeat
        con.execute("UPDATE rm_event_evidence SET quote = ?, seen_at = ? WHERE event_id = ? AND item_id = ?", [m.quote, now, eid, item_id])
        con.execute("UPDATE rm_events SET updated_flag = TRUE, update_note = ?, last_seen = ?, quote = ? WHERE event_id = ?",
                    ["The source article changed after we first read it. Compare with the earlier text.", now, m.quote, eid])
        stats["updated_events"] += 1
    if already is None:
        con.execute("INSERT INTO rm_event_evidence VALUES (?,?,?,?,?)", [eid, item_id, m.quote, m.name, now])
        n_src = con.execute("SELECT COUNT(DISTINCT item_id) FROM rm_event_evidence WHERE event_id = ?", [eid]).fetchone()[0]
        con.execute("UPDATE rm_events SET last_seen=?, n_sources=? WHERE event_id=?", [now, n_src, eid])
        stats["dup_reports"] += 1                      # a repeat of the same announcement from another source
    return eid


def _rematch_open_events(ctx: Ctx, matcher: Matcher, path=None) -> int:
    """Events still waiting for a match are tried again each scan (new data or your earlier answers may resolve them).
    Events you decided on, and events already matched, are not touched."""
    if not ctx.ready:
        return 0
    n = 0
    with store.conn(path) as con:
        rows = con.execute("SELECT event_id, player_text, club_text, league_hint, position_hint FROM rm_events "
                           "WHERE match_status IN ('review','unmatched') AND NOT user_decided").fetchall()
        for eid, name, club, lg, pos in rows:
            r = matcher.match(name, club, lg, pos)
            if r.status == "matched":
                con.execute("UPDATE rm_events SET uid=?, match_status='matched', match_grade=?, match_reasons=?, candidates_json=? WHERE event_id=?",
                            [r.uid, r.grade, json.dumps(r.reasons), json.dumps(r.candidates), eid])
                n += 1
    return n


def _followed_mentions(text: str, ctx: Ctx, matcher: Matcher, default_club: str | None, league_hint, taken: set,
                       already: list[Mention] | None = None) -> list[Mention]:
    """Names of players you follow (shortlist, verified) that appear in the text with a corroborated club. Used for
    performance and news items about players you already track."""
    out = []
    fol = ctx.followed()
    if not fol or not ctx.ready:
        return out
    fsur = {}
    for uid in fol:
        if uid in ctx.roster.names:
            for n in ctx.roster.names[uid]:
                fsur.setdefault(surname_key(n), set()).add(uid)
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        clubs_here = ctx.catalog.find_in_text(sent, league_hint) if ctx.catalog else []
        for mm in NAME_RE.finditer(sent):
            nm = mm.group(0).rstrip(".")
            sk = surname_key(nm)
            if sk not in fsur:
                continue
            if any(name_compat(nm, a.name) or name_compat(a.name, nm) for a in (already or [])):
                continue                                    # already read as an announcement about him
            club = clubs_here[0].name if clubs_here else default_club
            r = matcher.match(nm, club, league_hint, None)
            if r.status == "matched" and r.uid in fol and r.uid not in taken:
                taken.add(r.uid)
                out.append(Mention(nm, "performance", None, sent[:320], club, "followed player named in text"))
    return out


# ---------------------------------------------------------------------------------------------------------------
# the scan
# ---------------------------------------------------------------------------------------------------------------
def run_scan(trigger: str = "manual", http: Http | None = None, ctx: Ctx | None = None, now: datetime | None = None,
             backfill: bool = False, sources: list[Source] | None = None, cfg: dict | None = None, path=None,
             echo: bool = True, only_sources: list[str] | None = None) -> dict:
    """Runs one scan and returns its summary. Never raises for source or parsing problems."""
    now = now or datetime.now()
    cfg = cfg or store.load_config()
    t0 = time.time()
    summary = {"trigger": trigger, "started": now.isoformat(timespec="seconds"), "sources": [], "new_items": 0,
               "new_events": 0, "updated_events": 0, "dup_reports": 0, "article_fetches": 0, "skipped_old": 0,
               "by_match": {}, "warnings": [], "rematched": 0, "error": None}
    stats = summary
    mig = store.ensure_schema(path, log=lambda m: _log(m, echo))
    if mig["backup"]:
        summary["backup"] = mig["backup"]
    if not acquire_lock():
        _log("Another scan is already running; this one stops.", echo)
        summary["error"] = "already running"
        summary["status"] = "skipped"
        return summary
    scan_id = None
    try:
        mark_interrupted_scans(path)
        with store.conn(path) as con:
            scan_id = store.next_id(con, "rm_scan_seq")
            con.execute("INSERT INTO rm_scans VALUES (?,?,?,?,?,?,?)", [scan_id, now, None, now, "running", trigger, "{}"])
        summary["scan_id"] = scan_id
        _log(f"Scan {scan_id} started ({trigger}).", echo)
        if ctx is None:
            ctx = load_context(progress=lambda m: _log("  " + m, echo), with_projections=False)
        for w in ctx.warnings:
            summary["warnings"].append(w)
            _log("WARNING: " + w, echo)
        with store.conn(path) as con:
            md = con.execute("SELECT * FROM rm_match_decisions").df()
        matcher = Matcher(ctx.roster, ctx.catalog, md) if ctx.ready else None
        summary["rematched"] = _rematch_open_events(ctx, matcher, path) if matcher else 0

        http = http or Http(timeout=cfg.get("request_timeout_s", 25), retries=cfg.get("max_retries", 3))
        robots = Robots(http)
        srcs = sources if sources is not None else load_sources(cfg)
        if only_sources:
            srcs = [s for s in srcs if s.id in only_sources]
        max_age = timedelta(days=int(cfg.get("max_age_days", 45)))
        fetch_cap = int(cfg.get("max_article_fetches_per_scan", 60))
        for src in srcs:
            res = fetch_source(src, http, robots)
            with store.conn(path) as con:
                _record_source(con, res, now)
                con.execute("UPDATE rm_scans SET heartbeat_at=? WHERE scan_id=?", [datetime.now(), scan_id])
            touch_lock()
            summary["sources"].append(res.public())
            _log(f"  {res.status.upper():8} {src.name}: {len(res.items)} items" + (f" ({res.error})" if res.error else ""), echo)
            if res.status != "ok":
                continue
            for it in res.items:
                try:
                    _process_item(it, src, res, http, robots, ctx, matcher, now, max_age, backfill, summary, fetch_cap, scan_id, path)
                except Exception as exc:                       # one bad item never stops the scan
                    summary["warnings"].append(f"Item skipped ({it.get('title', '')[:60]}): {type(exc).__name__}: {exc}")
                    _log("  item error: " + traceback.format_exc().splitlines()[-1], echo)
        ok_n = sum(1 for s in summary["sources"] if s["status"] == "ok")
        tried = sum(1 for s in summary["sources"] if s["status"] != "skipped")
        status = "ok" if ok_n == tried and tried else "partial" if ok_n else "failed"
        summary["status"] = status
        summary["http_log"] = http.log[-20:]
    except KeyboardInterrupt:
        summary["status"], summary["error"] = "interrupted", "Stopped by the user"
        raise
    except Exception as exc:
        summary["status"], summary["error"] = "failed", f"{type(exc).__name__}: {exc}"
        _log("Scan failed: " + traceback.format_exc().splitlines()[-1], echo)
    finally:
        summary["seconds"] = round(time.time() - t0, 1)
        if scan_id is not None:
            try:
                with store.conn(path) as con:
                    con.execute("UPDATE rm_scans SET finished_at=?, heartbeat_at=?, status=?, summary_json=? WHERE scan_id=?",
                                [datetime.now(), datetime.now(), summary.get("status", "interrupted"), json.dumps(summary, default=str), scan_id])
            except Exception:
                pass
        release_lock()
    _log(f"Scan {scan_id} finished: {summary.get('status')}, {summary['new_items']} new items, {summary['new_events']} new events, "
         f"{summary['updated_events']} updated.", echo)
    return summary


def _process_item(it: dict, src: Source, res, http, robots, ctx: Ctx, matcher, now, max_age, backfill, summary, fetch_cap, scan_id, path):
    key = url_key(it["url"])
    pub = it.get("published")
    if pub and not backfill and (now - pub) > max_age:
        summary["skipped_old"] += 1
        return
    with store.conn(path) as con:
        row = con.execute("SELECT item_id, text_hash FROM rm_items WHERE url_key = ?", [key]).fetchone()
    title = it["title"]
    htype = headline_type(title)
    sur_hit = False
    if ctx.ready and htype is None and not it.get("headline_only"):
        fol = ctx.followed()
        sset = {surname_key(n) for u in fol if u in ctx.roster.names for n in ctx.roster.names[u]}
        sur_hit = any(surname_key(" ".join(["x", w])) in sset for w in re.findall(r"[A-Za-zÀ-ÿ'’\-]{3,}", title))
    want_body = ((htype is not None and htype != "performance") or sur_hit) and not it.get("headline_only")
    body, body_status = "", "not needed"
    if row is not None and not want_body:
        return
    if want_body:
        if summary["article_fetches"] >= fetch_cap:
            body_status = "skipped: article fetch limit reached this scan"
        else:
            summary["article_fetches"] += 1
            body_status, t2, body, p2 = fetch_article(it["url"], http, robots)
            pub = pub or p2
            if pub and not backfill and (now - pub) > max_age:
                summary["skipped_old"] += 1
                return
    elif it.get("headline_only") and htype:
        body_status = "headline only: this source does not give the article text"
    full = title + "\n" + body
    chash = hashlib.sha1(full.encode()).hexdigest()[:16]
    if row is not None and row[1] == chash:
        return
    # read the announcement
    league = it.get("league_hint")
    default_club = it.get("club_hint")
    default_club = title_club(title, ctx.catalog, league) or default_club
    mentions = extract_mentions(title + ("\n" + body if body else ""), ctx.catalog, league, default_club) if htype or body else []
    mentions = [m for m in mentions if m.event_type in MOVEMENT_TYPES or m.event_type == "performance"]
    if matcher and (body or htype is None):
        taken: set = set()
        mentions += _followed_mentions(full, ctx, matcher, default_club, league, taken, list(mentions))
    atype = htype
    if mentions and atype in (None, "performance"):
        atype = sorted(mentions, key=lambda m: m.event_type in LEAD_TYPES, reverse=True)[0].event_type
    if htype == "club_roster_update":
        atype = "club_roster_update"
    with store.conn(path) as con:
        if row is None:
            item_id = store.next_id(con, "rm_item_seq")
            con.execute("INSERT INTO rm_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [item_id, src.id, it["url"], key, title, pub, now, it.get("snippet", "")[:600], body[:24000], body_status,
                         chash, atype, default_club, league, scan_id, chash])
            summary["new_items"] += 1
        else:
            item_id = row[0]
            con.execute("UPDATE rm_items SET body_text=?, body_status=?, text_hash=?, announcement_type=?, club_text=?, processed_hash=? WHERE item_id=?",
                        [body[:24000], body_status, chash, atype, default_club, chash, item_id])
        item_row = {"published": pub, "league_hint": league, "url_key": key}
        made = 0
        for m in mentions:
            r = (matcher.match(m.name, m.club_text, league, m.position, m.extra_clubs, m.article_clubs) if matcher else None)
            if r is None:
                from .match import MatchResult
                r = MatchResult("unmatched", reasons=["Matching was not possible in this run (model or identity layer unavailable)."])
            if r.status == "unmatched" and m.event_type not in LEAD_TYPES and not (m.event_type == "signing" and league == "College"):
                continue                                   # not in our data and not a recruitment lead: not stored as an event
            if m.event_type == "performance" and r.status != "matched":
                continue
            _upsert_event(con, m, r, item_id, item_row, ctx, now, summary)
            made += 1
        if htype == "club_roster_update" and made == 0 and default_club:
            m = Mention("(players not read)", "club_roster_update", None, title, default_club, "headline")
            from .match import MatchResult
            _upsert_event(con, m, MatchResult("unmatched", reasons=["Club level item: no player is named in what we could read."]),
                          item_id, item_row, ctx, now, summary)


def scan_link(url: str, league_hint: str | None = None, **kw) -> dict:
    """Reads one announcement address you give it, regardless of its age, through the same pipeline."""
    src = Source("manual_link", "Link you added", "single", url, league_hint, note="Added by hand")
    return run_scan(trigger="link", sources=[src], backfill=True, **kw)
