"""The club source directory: for each club, where its official news and roster live, and what was actually verified.

* Current clubs come from each league's own club page (USL Championship, USL League One). A club listed there is
  'current'; clubs the league lists as 'Future Clubs' are recorded as future. MLS NEXT Pro clubs cannot be read from the
  league site in every environment: they are listed from the database and marked as needing discovery.
* Every source records what was checked, when, and with what result. A URL that was only listed, never fetched, says so.
* `build` (live, needs the league sites) writes club_directory.json. `verify` fetches club websites, respects robots.txt,
  discovers their news and roster pages and records the results in your database. Run on a computer that can reach them.
* Nothing is guessed: a missing URL stays missing and shows as a gap on the coverage page.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pandas as pd

from .. import config
from . import store
from .htmlbits import clean, feed_links, find_links, ngin_items, plain_text
from .http import Http, Robots
from .roster import looks_like_roster, parse_roster_text

DIRECTORY_FILE = Path(__file__).with_name("club_directory.json")
USER_FILE = "club_sources.json"
LEAGUE_SITES = {"USL Championship": "https://www.uslchampionship.com", "USL League One": "https://www.uslleagueone.com"}
MLSNP = "MLS Next Pro"
LEAGUE_CODE = {"USL Championship": "uslc", "USL League One": "usl1", "MLS Next Pro": "mlsnp"}
NEWS_WORDS = ("news", "press", "stories", "announcements")
ROSTER_WORDS = ("roster", "players", "squad", "first-team", "first team", "team")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse_league_clubs(page: str, base: str) -> tuple[list[dict], list[str]]:
    """(current clubs, future club names) from a league's club page. Each current club: name, website, league links."""
    body = page[page.find("<body"):] if "<body" in page else page
    toks = [(m.start(), m.group(0)) for m in re.finditer(r'<h[1-3][^>]*>.*?</h[1-3]>|col-xs-6 col-sm-4 col-md-3 i"', body, re.S)]
    section, cur, fut = "clubs", [], []
    for i, (pos, t) in enumerate(toks):
        if t.startswith("<h"):
            txt = clean(t).lower()
            if "future" in txt or "expansion" in txt or "upcoming" in txt:
                section = "future"
            elif re.search(r"club|team", txt):
                section = "clubs"
            continue
        end = toks[i + 1][0] if i + 1 < len(toks) else pos + 5000
        seg = body[pos:min(end, pos + 5000)]
        if section == "future":
            m = re.search(r'(?:alt|title)="([^"]+?) Club Card"', seg)
            nm = m.group(1) if m else clean(re.sub(r"Team Website|News", " ", seg))[:40].strip()
            if nm:
                fut.append(nm)
            continue
        nm = re.search(r'aria-label="([^"]+?) Team Pages"', seg)
        if not nm:
            continue
        links = {}
        for a, inner in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', seg, re.S):
            k = clean(inner)
            if k and k not in links:
                links[k] = urljoin(base, a)
        cur.append({"name": clean(nm.group(1)), "website": links.get("Team Website"),
                    "league_news": links.get("News"), "league_roster": links.get("Roster")})
    return cur, fut


def _check(http: Http, robots: Robots, url: str, now: datetime, kind: str, exclude: set | None = None) -> dict:
    """What a fetch of one page showed: reachability, robots, and (for news) items or (for roster) names."""
    v = {"checked_at": now.isoformat(timespec="minutes"), "http": None, "robots": "unknown", "ok": False, "note": ""}
    ok, why = robots.allowed(url)
    v["robots"] = "allowed" if ok else why
    if not ok:
        v["note"] = "Not fetched: " + (why or "robots.txt")
        return v
    r = http.get(url, max_bytes=2_000_000)
    v["http"] = r.status
    if not r.ok:
        v["note"] = f"Fetch failed: {r.error}"
        return v
    if kind in ("league_news", "club_news", "tracker"):
        items = ngin_items(r.text, url)
        if not items:
            items = [{"title": t, "published": None} for u, t in find_links(r.text, url, ("news", "article", "story", "/20")) if len(t) > 25]
        v["items"] = len(items)
        dates = [i["published"] for i in items if i.get("published")]
        v["newest"] = max(dates).date().isoformat() if dates else None
        v["ok"] = len(items) > 0
        v["note"] = "" if v["ok"] else "Loaded, but no news items could be read (layout unknown or script rendered)."
    else:
        text = plain_text(r.text)
        names = parse_roster_text(text, exclude)
        good, why = looks_like_roster(names, text)
        v["static_players"] = len(names) if good else 0
        script = len(re.findall(r"<script", r.text))
        v["js_rendered"] = (not good) and script >= 5
        v["ok"] = good
        v["note"] = ("" if good else f"Loaded, but no readable player list ({why}). The list is probably drawn by JavaScript, which a plain fetch "
                     "cannot read: it would need a browser backend, so roster changes here are NOT covered.")
    return v


def build(http: Http | None = None, db_clubs: dict | None = None, now: datetime | None = None, log=print) -> dict:
    """Builds the directory from the live league pages and verifies every league-hosted page it lists."""
    http = http or Http(timeout=25, retries=2)
    robots = Robots(http)
    now = now or datetime.now()
    d = {"generated": now.isoformat(timespec="minutes"), "leagues": {}, "clubs": [],
         "about": "Clubs are read from each league's own club page. 'verified' fields say what was fetched and when; "
                  "an entry with checked_at null was listed but never fetched from the build environment."}
    from .clubs import club_key
    pages = {}
    for league, base in LEAGUE_SITES.items():
        url = base + "/league-teams"
        ok, why = robots.allowed(url)
        r = http.get(url, max_bytes=3_000_000) if ok else None
        if not ok or not r.ok:
            d["leagues"][league] = {"club_list_url": url, "error": why or (r.error if r else "")}
            log(f"{league}: club page not read ({why or r.error})")
            continue
        pages[league] = (url, base) + parse_league_clubs(r.text, base)
    exclude = {club_key(c["name"]) for _, _, cur, _ in pages.values() for c in cur}
    exclude |= {club_key(n) for _, _, _, fut in pages.values() for n in fut} | {club_key(n) for n in (db_clubs or {}).get(MLSNP, [])}
    for league, (url, base, cur, fut) in pages.items():
        d["leagues"][league] = {"club_list_url": url, "fetched_at": now.isoformat(timespec="minutes"),
                                "n_current": len(cur), "future": fut, "transactions_url": base + "/transactions"}
        for c in cur:
            e = {"club_id": slug(LEAGUE_CODE[league] + "-" + c["name"]), "name": c["name"], "league": league,
                 "status": "current", "website": c["website"], "listed_by": url, "sources": []}
            for kind, key in (("league_news", "league_news"), ("league_roster", "league_roster")):
                if c.get(key):
                    v = _check(http, robots, c[key], now, kind, exclude)
                    url_used = c[key]
                    if v.get("http") == 404:                       # the league's own link is broken: try the league's usual pattern
                        alt = f"{base}/{slug(c['name'])}-{'news' if kind == 'league_news' else 'roster'}"
                        v2 = _check(http, robots, alt, now, kind, exclude)
                        if v2.get("http") == 200:
                            v2["note"] = (f"The league page links {c[key]} (404). The league's usual address pattern works: {alt}. " + (v2.get("note") or "")).strip()
                            v, url_used = v2, alt
                    e["sources"].append({"kind": kind, "url": url_used, "verified": v})
            e["sources"].append({"kind": "club_site", "url": c["website"], "verified": {
                "checked_at": None, "note": "Listed by the league; not fetched from the build environment. Run 'directory verify'."}})
            for kind in ("club_news", "club_roster"):
                e["sources"].append({"kind": kind, "url": None, "verified": {
                    "checked_at": None, "note": "Not discovered yet: needs 'directory verify' on a computer that can reach the club site."}})
            d["clubs"].append(e)
            log(f"  {league} / {c['name']}: " + ", ".join(f"{s['kind']}={'ok' if s['verified'].get('ok') else 'no'}" for s in e["sources"] if s["verified"].get("checked_at")))
        for nm in fut:
            d["clubs"].append({"club_id": slug(LEAGUE_CODE[league] + "-" + nm), "name": nm, "league": league, "status": "future", "website": None,
                               "listed_by": url, "sources": [], "note": "Listed by the league as a future club: not playing yet."})
        tx = base + "/transactions"
        d["leagues"][league]["transactions_check"] = _check(http, robots, tx, now, "tracker")
    # MLS NEXT Pro: league site not necessarily reachable; list clubs from the database and say so
    for nm in sorted((db_clubs or {}).get(MLSNP, [])):
        d["clubs"].append({"club_id": slug("mlsnp-" + nm), "name": nm, "league": MLSNP, "status": "current (from database)",
                           "website": None, "listed_by": "database 2026 player data", "sources": [
                               {"kind": k, "url": None, "verified": {"checked_at": None, "note": "No URL known: run 'directory discover' where "
                                                                     "mlsnextpro.com is reachable."}} for k in ("club_site", "club_news", "club_roster")]})
    return d


def load_directory() -> dict:
    try:
        d = json.loads(DIRECTORY_FILE.read_text())
    except Exception:
        d = {"clubs": [], "leagues": {}}
    try:
        user = json.loads((config.DATA_DIR / USER_FILE).read_text())
    except Exception:
        user = {}
    by = {c["club_id"]: c for c in d["clubs"]}
    for c in user.get("clubs", []):                      # your own additions or corrections win
        if c.get("club_id") in by:
            by[c["club_id"]].update({k: v for k, v in c.items() if k != "sources"})
            for s in c.get("sources", []):
                cur = [x for x in by[c["club_id"]]["sources"] if x["kind"] == s["kind"]]
                if cur:
                    cur[0].update(s)
                else:
                    by[c["club_id"]]["sources"].append(s)
        else:
            by[c["club_id"]] = c
    d["clubs"] = list(by.values())
    return d


# ---------------------------------------------------------------------------------------------------------------
# club websites: discover news and roster pages (run on your computer)
# ---------------------------------------------------------------------------------------------------------------
def verify_club_site(entry: dict, http: Http, robots: Robots, now: datetime) -> list[dict]:
    """Reads a club's website, finds its news, roster and feed addresses, checks each. Returns source records."""
    out = []
    site = entry.get("website")
    if not site:
        return [{"kind": "club_site", "url": None, "verified": {"checked_at": now.isoformat(timespec="minutes"), "ok": False,
                                                                 "note": "No website known for this club."}}]
    home = _check_home(http, robots, site, now)
    out.append({"kind": "club_site", "url": site, "verified": home["v"]})
    if not home["ok"]:
        return out
    page = home["html"]
    feeds = feed_links(page, site)
    if feeds:
        out.append({"kind": "club_feed", "url": feeds[0], "verified": {"checked_at": now.isoformat(timespec="minutes"), "ok": True,
                                                                         "note": "Advertised in the page head; use as an RSS source."}})
    news = [u for u, _ in find_links(page, site, NEWS_WORDS)][:3]
    roster = [u for u, t in find_links(page, site, ROSTER_WORDS) if re.search(r"roster|players|squad|first", (u + t).lower())][:3]
    for kind, urls in (("club_news", news), ("club_roster", roster)):
        best = None
        for u in urls:
            v = _check(http, robots, u, now, kind)
            if best is None or (v.get("ok") and not best["verified"].get("ok")):
                best = {"kind": kind, "url": u, "verified": v}
            if v.get("ok"):
                break
        out.append(best or {"kind": kind, "url": None, "verified": {"checked_at": now.isoformat(timespec="minutes"), "ok": False,
                                                                      "note": "No link to a news or roster page was found on the home page."}})
    return out


def _check_home(http, robots, site, now):
    v = {"checked_at": now.isoformat(timespec="minutes"), "http": None, "robots": "unknown", "ok": False, "note": ""}
    ok, why = robots.allowed(site)
    v["robots"] = "allowed" if ok else why
    if not ok:
        v["note"] = "Not fetched: " + (why or "robots.txt")
        return {"ok": False, "v": v, "html": ""}
    r = http.get(site, max_bytes=2_000_000)
    v["http"] = r.status
    v["ok"] = r.ok
    v["note"] = "" if r.ok else f"Fetch failed: {r.error}"
    return {"ok": r.ok, "v": v, "html": r.text if r.ok else ""}


def discover_mlsnp(http: Http, robots: Robots, entries: list[dict], now: datetime) -> dict:
    """Finds MLS NEXT Pro club websites from the league's club list (needs mlsnextpro.com to be reachable)."""
    res = {"found": 0, "error": None}
    for u in ("https://www.mlsnextpro.com/clubs", "https://www.mlsnextpro.com/"):
        ok, why = robots.allowed(u)
        if not ok:
            res["error"] = why
            continue
        r = http.get(u)
        if not r.ok:
            res["error"] = r.error
            continue
        res["error"] = None
        from .clubs import ClubCatalog
        cat = ClubCatalog([(e["name"], MLSNP) for e in entries])
        for url, text in [(a, t) for a, t in __import__("engine.monitor.htmlbits", fromlist=["anchors"]).anchors(r.text, u)]:
            if urlsplit(url).netloc.endswith("mlsnextpro.com") or not text:
                continue
            ref = cat.lookup(text)
            if ref and ref.strength == "exact":
                for e in entries:
                    if e["name"] == ref.name and not e.get("website"):
                        e["website"] = url
                        e["discovered_by"] = f"{u} ({now.date()}); not yet verified"
                        res["found"] += 1
        if res["found"]:
            break
    return res


# ---------------------------------------------------------------------------------------------------------------
# storage and coverage
# ---------------------------------------------------------------------------------------------------------------
def save_results(rows: list[dict], path=None):
    """rm_club_source holds what was verified on THIS computer; it overrides the shipped directory."""
    with store.conn(path) as con:
        for r in rows:
            con.execute("DELETE FROM rm_club_source WHERE club_id = ? AND kind = ?", [r["club_id"], r["kind"]])
            v = r.get("verified", {})
            store.insert(con, "rm_club_source", club_id=r["club_id"], name=r["name"], league=r["league"], db_name=r.get("db_name"), kind=r["kind"],
                         url=r.get("url"), verified_at=pd.Timestamp(v["checked_at"]).to_pydatetime() if v.get("checked_at") else None,
                         status="ok" if v.get("ok") else ("not checked" if not v.get("checked_at") else "problem"), http_status=v.get("http"),
                         robots=v.get("robots"), detail=v.get("note"), items_found=v.get("items", v.get("static_players")), note=json.dumps(
                             {k: v[k] for k in ("js_rendered", "newest") if k in v}))


def verify_all(http: Http | None = None, only_league: str | None = None, only_club: str | None = None, now: datetime | None = None,
               path=None, log=print) -> dict:
    http = http or Http(timeout=25, retries=2)
    robots = Robots(http)
    now = now or datetime.now()
    d = load_directory()
    rows, n = [], 0
    for e in d["clubs"]:
        if only_league and e["league"] != only_league or only_club and only_club.lower() not in e["name"].lower():
            continue
        if e.get("status", "").startswith("future"):
            continue
        n += 1
        recs = verify_club_site(e, http, robots, now)
        for r in recs:
            rows.append({**r, "club_id": e["club_id"], "name": e["name"], "league": e["league"]})
        log(f"  {e['league']} / {e['name']}: " + ", ".join(f"{r['kind']}={'ok' if r['verified'].get('ok') else 'no'}" for r in recs))
    save_results(rows, path)
    return {"clubs": n, "rows": len(rows)}


def coverage_table(path=None, now: datetime | None = None) -> pd.DataFrame:
    """One row per club: configured sources, what was verified, last successful check, failures, and what is missing."""
    now = now or datetime.now()
    d = load_directory()
    try:
        saved = store.query("SELECT * FROM rm_club_source", path=path)
        watches = store.query("SELECT * FROM rm_watch", path=path)
        events = store.query("SELECT club_text, COUNT(*) n, MAX(first_seen) last FROM rm_events WHERE source_state IS DISTINCT FROM 'withdrawn' "
                             "GROUP BY club_text", path=path)
    except Exception:
        saved, watches, events = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    rows = []
    from .clubs import club_key
    ev_by = {club_key(r.club_text): (int(r.n), r.last) for r in events.itertuples()} if len(events) else {}
    for e in d["clubs"]:
        cid = e["club_id"]
        srcs = {s["kind"]: dict(s) for s in e.get("sources", [])}
        for r in (saved[saved["club_id"] == cid].itertuples() if len(saved) else []):      # this computer's results win
            srcs[r.kind] = {"kind": r.kind, "url": r.url, "verified": {"checked_at": str(r.verified_at)[:16] if pd.notna(r.verified_at) else None,
                                                                       "ok": r.status == "ok", "note": r.detail or "", "http": r.http_status}}
        w = watches[watches["club_id"] == cid] if len(watches) else watches
        configured, missing, problems = [], [], []
        for kind, label in (("league_news", "league news page"), ("league_roster", "league roster page"), ("club_feed", "club feed"),
                            ("club_news", "club news page"), ("club_roster", "club roster page")):
            s = srcs.get(kind)
            if not s or not s.get("url"):
                missing.append(label)
                continue
            v = s.get("verified", {})
            if v.get("ok"):
                configured.append(label)
            elif v.get("checked_at"):
                problems.append(f"{label}: {v.get('note') or 'problem'}")
            else:
                missing.append(f"{label} (listed, not verified)")
        watched = ", ".join(sorted(set(w["role"].dropna()))) if len(w) and "role" in w else ""
        okw = w[w["last_status"] == "ok"] if len(w) else w
        failw = w[w["consecutive_failures"].fillna(0) > 0] if len(w) else w
        last_ok = str(okw["last_success"].max())[:16] if len(okw) and pd.notna(okw["last_success"].max()) else ""
        n_ev, last_ev = ev_by.get(club_key(e["name"]), (0, None))
        rows.append({"League": e["league"], "Club": e["name"], "Status": e.get("status", ""), "Configured and verified": ", ".join(configured) or "none",
                     "Watched pages": f"{len(w)} ({watched})" if len(w) else "none", "Last successful watch check": last_ok,
                     "Failing watches": int(len(failw)), "Problems": "; ".join(problems + [f"{r.role or 'watch'} {r.last_error}" for r in failw.itertuples()][:2]),
                     "Missing coverage": ", ".join(missing) or "none listed", "Events seen": n_ev,
                     "Last event first seen": str(last_ev)[:10] if last_ev is not None and pd.notna(last_ev) else ""})
    return pd.DataFrame(rows)
