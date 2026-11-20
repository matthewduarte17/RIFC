"""Tests for the club directory, roster reading, unconfirmed roster changes, trackers and the changedetection.io integration.

Offline except test_real_changedetection_end_to_end, which starts the real changedetection.io (a separate environment) against
a local test site on 127.0.0.1; it is skipped when that tool is not installed (set RIFC_WATCH_EXE to its changedetection.py).

Run:  python -m pytest -q tests/test_watch.py
"""
import json
import os
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from tests.test_monitor import (FEED, NOW, SRC, USL1, USLC, World, article, db_path, events, make_ctx, rss, scan, store, brief,  # noqa: E402
                                IB, Matcher, schedule)
from engine.monitor import directory as D, scan as S, watch as W  # noqa: E402
from engine.monitor.clubs import club_key  # noqa: E402
from engine.monitor.htmlbits import ngin_items, plain_text  # noqa: E402
from engine.monitor.http import Http, Robots  # noqa: E402
from engine.monitor.roster import diff_rosters, looks_like_roster, parse_roster_text  # noqa: E402
from engine.monitor.sources import Source  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


# ------------------------------------------------------------------ roster reading -------------------------------
def names(text, ex=None):
    return [(r.name, r.section) for r in parse_roster_text(text, ex)]


def test_roster_formats():
    numbered = "First Team Roster\n\n  Goalkeepers\n    * 1 Koke Vegas GK\n  Forwards\n    * 9 Brandon Diene FWD\n    * 11 Juan Garcia FWD"
    assert names(numbered) == [("Koke Vegas", "goalkeepers"), ("Brandon Diene", "forwards"), ("Juan Garcia", "forwards")]
    table = "Name Pos Ht Wt Age Hometown\nBrandon Diene F 6-1 180 27 Dakar, Senegal\nJuan Garcia M 5-9 160 24 Providence, Rhode Island"
    assert [n for n, _ in names(table)] == ["Brandon Diene", "Juan Garcia"]      # header and hometown are not players
    blocks = "Goalkeepers\n#1\nKoke Vegas\nGoalkeeper\nMadrid, Spain\nDefenders\n#4\nHugo Bacharach\nTechnical Staff\nKhano Smith\nHead Coach\nForwards\n#9\nBrandon Diene"
    assert [n for n, _ in names(blocks)] == ["Koke Vegas", "Hugo Bacharach", "Brandon Diene"]     # staff skipped, players resume after


def test_menus_clubs_and_coaches_are_not_players():
    text = "Select a Team Roster...\nBirmingham Legion FC\nRhode Island FC\nTeam Website\nPlayer Stats\nTeam Stats\nKhano Smith\nHead Coach"
    ex = {club_key("Birmingham Legion FC"), club_key("Rhode Island FC")}
    got = [n for n, _ in names(text, ex)]
    assert "Birmingham Legion FC" not in got and "Team Website" not in got and "Player Stats" not in got


def test_a_page_with_a_coach_and_menus_is_not_a_roster():
    html = (FIX / "league_roster_js_page.html").read_text()
    ex = {club_key(n) for n in ("Rhode Island FC", "Birmingham Legion FC", "Brooklyn FC", "Charleston Battery")}
    good, why = looks_like_roster(parse_roster_text(plain_text(html), ex), plain_text(html))
    assert not good, why
    real = "\n".join(["Goalkeepers"] + [f"{i} Player{chr(65+i)} Name{chr(65+i)} GK" for i in range(3)] + ["Forwards"] + [f"{i+9} Striker{chr(65+i)} Surname{chr(65+i)} FWD" for i in range(6)])
    ok, why = looks_like_roster(parse_roster_text(real), real)
    assert ok, why


def _r(n, offset=0):
    return parse_roster_text("Forwards\n" + "\n".join(f"{i+1} Player{chr(65+i)} Name{chr(65+i)} FWD" for i in range(offset, n)))


def test_roster_diff_adds_removes_and_refuses_to_guess():
    a = _r(12)
    b = parse_roster_text("Forwards\n" + "\n".join(f"{i+1} Player{chr(65+i)} Name{chr(65+i)} FWD" for i in range(12) if i != 3) + "\n99 Newcomer Person FWD")
    d = diff_rosters(a, b)
    assert [x.name for x in d.removed] == ["PlayerD NameD"] and [x.name for x in d.added] == ["Newcomer Person"] and not d.anomaly
    assert diff_rosters(a, _r(4)).anomaly and not diff_rosters(a, _r(4)).removed            # broken page is not a mass exit
    assert diff_rosters(a, a[:6]).anomaly                                                   # 50% fewer: layout, not departures
    assert diff_rosters(_r(3), _r(3)).anomaly                                               # too few names to compare


# ------------------------------------------------------------------ directory -----------------------------------
def test_league_club_page_is_read_with_future_clubs_kept_apart():
    cur, fut = D.parse_league_clubs((FIX / "league_teams_snippet.html").read_text(), "https://www.uslchampionship.com")
    assert [c["name"] for c in cur] == ["Birmingham Legion FC", "Rhode Island FC"]
    rifc = cur[1]
    assert rifc["website"] == "https://www.rhodeislandfc.com/" and rifc["league_news"].endswith("/rhode-island-fc-news")
    assert fut == ["Atletico Dallas", "OKC United"]


def test_news_list_markup_gives_title_link_date():
    it = ngin_items((FIX / "ngin_news_list.html").read_text(), "https://www.uslchampionship.com")
    assert len(it) == 8 and it[0]["url"].startswith("https://www.uslchampionship.com/news_article/show/") and it[0]["published"]


def test_shipped_directory_records_what_was_and_was_not_verified():
    d = D.load_directory()
    cur = [c for c in d["clubs"] if str(c["status"]).startswith("current")]
    usl = [c for c in cur if c["league"] in ("USL Championship", "USL League One")]
    assert len([c for c in usl if c["league"] == "USL Championship"]) == 25 and len([c for c in usl if c["league"] == "USL League One"]) == 17
    for c in usl:
        by = {s["kind"]: s for s in c["sources"]}
        assert by["league_news"]["verified"]["ok"] and by["league_news"]["verified"]["checked_at"], c["name"]
        assert by["club_site"]["verified"]["checked_at"] is None                      # listed, never fetched: it must say so
        assert not by["league_roster"]["verified"]["ok"]                              # script drawn: not claimed as covered
    mls = [c for c in d["clubs"] if c["league"] == "MLS Next Pro"]
    assert len(mls) >= 25 and all(not s.get("url") for c in mls for s in c["sources"])   # no invented URLs
    assert any(c["status"] == "future" for c in d["clubs"])


def club_world():
    w = World()
    w.add("https://club.test/", '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body>'
                                '<a href="/news">News</a><a href="/roster">First Team Roster</a><a href="/tickets">Tickets</a></body></html>')
    items = "".join(f'<div class="odd item condensed"><ul class="details"><li><h4><a href="/news/{i}">Club news headline number {i} here</a></h4></li>'
                    f'<li class="dateAuthor"><span class="newsDate">11/1{i}/2026, 9:00am EST</span></li></ul><span class="clearAll"></span></div>' for i in range(3))
    w.add("https://club.test/news", f"<html><body>{items}</body></html>")
    rows = "<h2>Goalkeepers</h2><ul>" + "".join(f"<li>{i} Player{chr(65+i)} Name{chr(65+i)} GK</li>" for i in range(3)) + "</ul><h2>Forwards</h2><ul>" + "".join(
        f"<li>{i+9} Striker{chr(65+i)} Surname{chr(65+i)} FWD</li>" for i in range(6)) + "</ul>"
    w.add("https://club.test/roster", f"<html><body><nav>Tickets Shop</nav>{rows}</body></html>")
    return w


def test_verifying_a_club_site_finds_news_roster_and_feed():
    w = club_world()
    recs = {r["kind"]: r for r in D.verify_club_site({"website": "https://club.test/", "name": "Club"}, w.http(), Robots(w.http()), NOW)}
    assert recs["club_news"]["url"] == "https://club.test/news" and recs["club_news"]["verified"]["ok"] and recs["club_news"]["verified"]["items"] == 3
    assert recs["club_roster"]["url"] == "https://club.test/roster" and recs["club_roster"]["verified"]["ok"] and recs["club_roster"]["verified"]["static_players"] == 9
    assert recs["club_feed"]["url"] == "https://club.test/feed.xml"


def test_verification_respects_robots_and_reports_script_rendered_rosters():
    w = club_world()
    w.add("https://club.test/robots.txt", "User-agent: *\nDisallow: /roster")
    recs = {r["kind"]: r for r in D.verify_club_site({"website": "https://club.test/", "name": "Club"}, w.http(), Robots(w.http()), NOW)}
    assert not recs["club_roster"]["verified"]["ok"] and "robots" in recs["club_roster"]["verified"]["note"].lower()
    w2 = World()
    w2.add("https://js.test/", '<a href="/roster">Roster</a>')
    w2.add("https://js.test/roster", "<html><body>" + "<script>1</script>" * 8 + "<div id='app'></div></body></html>")
    r2 = {r["kind"]: r for r in D.verify_club_site({"website": "https://js.test/", "name": "JS"}, w2.http(), Robots(w2.http()), NOW)}
    v = r2["club_roster"]["verified"]
    assert not v["ok"] and v["js_rendered"] and "NOT covered" in v["note"]
    gone = D.verify_club_site({"website": "https://gone.test/", "name": "Gone"}, w2.http(), Robots(w2.http()), NOW)
    assert gone[0]["kind"] == "club_site" and not gone[0]["verified"]["ok"] and "404" in gone[0]["verified"]["note"]


def test_coverage_table_shows_gaps_and_failures(tmp_path):
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    D.save_results([{"club_id": "uslc-rhode-island-fc", "name": "Rhode Island FC", "league": "USL Championship", "kind": "club_roster", "url": "https://x/r",
                     "verified": {"checked_at": "2026-11-20T10:00", "ok": False, "note": "Loaded, but no readable player list"}}], p)
    with store.conn(p) as con:
        store.insert(con, "rm_watch", watch_id="w1", club_id="uslc-rhode-island-fc", kind="news", url="https://x/n", last_status="failed",
                     last_error="HTTP 500", consecutive_failures=2, role="news")
    cov = D.coverage_table(p).set_index("Club")
    r = cov.loc["Rhode Island FC"]
    assert "league news page" in r["Configured and verified"] and "league roster page" not in r["Configured and verified"]
    assert "club roster page: Loaded, but no readable player list" in r["Problems"] and r["Failing watches"] == 1
    assert "club news page" in r["Missing coverage"] or "club news page (listed, not verified)" in r["Missing coverage"]
    mls = cov[cov["League"] == "MLS Next Pro"]
    assert len(mls) and all("club news page" in m for m in mls["Missing coverage"])


# ------------------------------------------------------------------ unconfirmed roster changes ------------------
class FakeDiff:
    def __init__(self, added=(), removed=()):
        from engine.monitor.roster import RosterName
        from engine.monitor.match import name_key
        self.added = [RosterName(n, name_key(n), "forwards", n) for n in added]
        self.removed = [RosterName(n, name_key(n), "goalkeepers", n) for n in removed]


SPEC = W.Spec("uslc-alpha:club_roster", "uslc-alpha", "Alpha FC", USLC, "roster", "https://alpha.test/roster", "generic_roster")


def roster_events(p, ctx, added=(), removed=(), now=None):
    now = now or NOW
    return S.record_roster_events(SPEC, FakeDiff(added, removed), "1", ctx, Matcher(ctx.roster, ctx.catalog), p, now)


def test_roster_changes_are_always_unconfirmed(tmp_path):
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    ctx = make_ctx()
    assert roster_events(p, ctx, added=["Brandon Diene", "Zed Nobody"], removed=["Koke Vegas"]) == 2    # Zed Nobody is not in our data: no event
    ev = events(p).set_index("player_text")
    assert set(ev.index) == {"Brandon Diene", "Koke Vegas"}
    assert (ev["confirmation"] == "unconfirmed").all()
    assert ev.loc["Koke Vegas", "event_type"] == "roster_removed" and ev.loc["Koke Vegas", "match_status"] == "matched"
    assert ev.loc["Brandon Diene", "match_status"] == "review" and pd.isna(ev.loc["Brandon Diene", "uid"])      # known elsewhere: queued, never linked
    assert "not an announcement" in ev.loc["Koke Vegas", "quote"]


def test_unconfirmed_changes_never_outrank_or_read_as_departures(tmp_path):
    from engine.monitor.profile import player_card, review_priority, inference_for
    ctx = make_ctx(["P_VEGAS"])
    card = player_card(ctx, "P_VEGAS")
    assert review_priority({"event_type": "roster_removed", "announced_at": NOW}, card)["tier"] != "High"
    txt = " ".join(inference_for("roster_removed", True))
    assert "not an announcement" in txt and "Not established" in txt
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    roster_events(p, ctx, removed=["Koke Vegas"])
    b = brief.build_brief(ctx, today=NOW.date(), path=p, save=False)
    assert [i["title"] for i in b["sections"]["unconfirmed"]] and not b["sections"]["shortlist"] and not b["sections"]["signals"]
    assert "UNCONFIRMED" in b["markdown"] and "Release" not in b["markdown"].split("UNCONFIRMED")[0][-80:]
    # a player nobody follows does not enter the brief at all
    (tmp_path / "b").mkdir()
    p2 = db_path(tmp_path / "b")
    store.ensure_schema(p2, log=lambda m: None)
    ctx2 = make_ctx()
    roster_events(p2, ctx2, removed=["Koke Vegas"])
    assert not brief.build_brief(ctx2, today=NOW.date(), path=p2, save=False)["sections"]["unconfirmed"]


def test_only_an_announcement_supports_a_roster_change(tmp_path):
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    ctx = make_ctx()
    roster_events(p, ctx, removed=["Koke Vegas"])
    assert S.support_pass(p, NOW) == 0 and events(p).iloc[0].confirmation == "unconfirmed", "the page alone never confirms itself"
    # an announcement about him at that club arrives
    w = World()
    w.add(FEED, rss([("Alpha FC declines option on Koke Vegas", "https://feed.test/v", NOW - timedelta(hours=1), "")]), ctype="application/xml")
    w.add("https://feed.test/v", article("Alpha FC declines option on Koke Vegas", ["Alpha FC declined contract options for Koke Vegas."]))
    scan(w, p, ctx)
    ev = events(p).set_index("event_type")
    assert ev.loc["roster_removed", "confirmation"] == "supported" and str(ev.loc["option_declined", "event_id"]) in ev.loc["roster_removed", "supported_by"]
    assert ev.loc["option_declined", "confirmation"] == "announced"
    assert "supported by an announcement" in ev.loc["roster_removed", "update_note"].lower()


def test_an_announcement_for_another_player_or_far_in_time_does_not_support(tmp_path):
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    ctx = make_ctx()
    roster_events(p, ctx, removed=["Koke Vegas"])
    w = World()
    w.add(FEED, rss([("Alpha FC declines option on Juan Garcia", "https://feed.test/o", NOW - timedelta(hours=1), "")]), ctype="application/xml")
    w.add("https://feed.test/o", article("Alpha FC declines option on Juan Garcia", ["Alpha FC declined contract options for Juan Garcia."]))
    scan(w, p, ctx)
    assert events(p).set_index("event_type").loc["roster_removed", "confirmation"] == "unconfirmed"
    S.support_pass(p, NOW + timedelta(days=200))
    assert events(p).set_index("event_type").loc["roster_removed", "confirmation"] == "unconfirmed"


# ------------------------------------------------------------------ trackers -------------------------------------
def tracker_world(post_body):
    w = World()
    w.add("https://tracker.test/feed", rss([("USL League One off season transactions tracker", "https://tracker.test/p/1", NOW - timedelta(days=1), ""),
                                              ("Why I love the lower leagues", "https://tracker.test/p/2", NOW - timedelta(days=1), "")]), ctype="application/xml")
    w.add("https://tracker.test/p/1", article("USL League One off season transactions tracker", post_body))
    w.add("https://tracker.test/p/2", article("Why I love the lower leagues", ["One Knoxville SC declined contract options for Brandon Diene."]))
    return w


TRK = [Source("trk", "Independent tracker", "rss", "https://tracker.test/feed", USL1, tracker=True, tracker_titles=r"transaction|tracker",
              reliability="One independent writer")]


def test_tracker_entries_are_cross_checks_with_their_link_and_never_confirmations(tmp_path):
    p = db_path(tmp_path)
    w = tracker_world(["One Knoxville SC declined contract options for Brandon Diene."])
    ctx = make_ctx()
    scan(w, p, ctx, sources=TRK)
    ev = events(p)
    assert len(ev) == 1, "only the post whose title is a transactions list is read"
    r = ev.iloc[0]
    assert r.confirmation == "cross-check" and r.sources[0]["url"] == "https://tracker.test/p/1" and r.match_status == "matched"
    assert "cross-check" in IB.to_csv(IB.enrich(ev, ctx))


def test_tracker_alone_cross_checks_a_roster_change_but_does_not_confirm_it(tmp_path):
    p = db_path(tmp_path)
    ctx = make_ctx()
    store.ensure_schema(p, log=lambda m: None)
    S_ = W.Spec("usl1-knox:club_roster", "usl1-knox", "One Knoxville SC", USL1, "roster", "https://knox.test/r", "generic_roster")
    S.record_roster_events(S_, FakeDiff(removed=["Brandon Diene"]), "1", ctx, Matcher(ctx.roster, ctx.catalog), p, NOW)
    scan(tracker_world(["One Knoxville SC declined contract options for Brandon Diene."]), p, ctx, sources=TRK)
    ev = events(p).set_index("event_type")
    assert ev.loc["roster_removed", "confirmation"] == "cross-checked", ev[["confirmation"]]
    assert "not a confirmation" in ev.loc["roster_removed", "update_note"]


# ------------------------------------------------------------------ migration ------------------------------------
V1 = """
CREATE TABLE rm_meta (key VARCHAR PRIMARY KEY, value VARCHAR);
CREATE TABLE rm_items (item_id INTEGER PRIMARY KEY, source_id VARCHAR, url VARCHAR, url_key VARCHAR, title VARCHAR, published_at TIMESTAMP,
  discovered_at TIMESTAMP, snippet VARCHAR, body_text VARCHAR, body_status VARCHAR, text_hash VARCHAR, announcement_type VARCHAR, club_text VARCHAR,
  league_hint VARCHAR, scan_id INTEGER, processed_hash VARCHAR);
CREATE TABLE rm_events (event_id INTEGER PRIMARY KEY, fingerprint VARCHAR, event_type VARCHAR, player_text VARCHAR, name_norm VARCHAR, club_text VARCHAR,
  club_norm VARCHAR, league_hint VARCHAR, position_hint VARCHAR, announced_at TIMESTAMP, first_seen TIMESTAMP, last_seen TIMESTAMP, n_sources INTEGER,
  uid VARCHAR, match_status VARCHAR, match_grade VARCHAR, match_reasons VARCHAR, candidates_json VARCHAR, extraction VARCHAR, inbox_status VARCHAR,
  status_changed_at TIMESTAMP, user_note VARCHAR, user_decided BOOLEAN, updated_flag BOOLEAN, update_note VARCHAR, prior_decision VARCHAR, quote VARCHAR,
  detail_hash VARCHAR, followed_at_match BOOLEAN, win VARCHAR);
CREATE TABLE rm_event_evidence (event_id INTEGER, item_id INTEGER, quote VARCHAR, mention_text VARCHAR, seen_at TIMESTAMP);
CREATE SEQUENCE rm_event_seq START 5;
"""


def test_v1_database_is_backed_up_then_upgraded_without_losing_anything(tmp_path):
    import duckdb
    p = tmp_path / "v1.duckdb"
    con = duckdb.connect(str(p))
    con.execute(V1)
    con.execute("INSERT INTO rm_meta VALUES ('schema_version','1')")
    con.execute("INSERT INTO rm_events (event_id, fingerprint, event_type, player_text, inbox_status, user_note, user_decided) VALUES "
                "(1,'fp1','option_declined','Juan Garcia','Monitor','keep me',TRUE)")
    con.close()
    info = store.ensure_schema(p, log=lambda m: None)
    assert info["backup"] and (Path(info["backup"]) / "v1.duckdb").exists()
    bk = duckdb.connect(str(Path(info["backup"]) / "v1.duckdb"), read_only=True)
    assert "source_state" not in [r[0] for r in bk.execute("SELECT column_name FROM information_schema.columns WHERE table_name='rm_events'").fetchall()]
    bk.close()
    e = store.query("SELECT * FROM rm_events", path=p).iloc[0]
    assert e.inbox_status == "Monitor" and e.user_note == "keep me" and bool(e.user_decided) and "confirmation" in e.index
    assert store.query("SELECT value FROM rm_meta WHERE key='schema_version'", path=p).iloc[0, 0] == "2"
    assert not store.ensure_schema(p, log=lambda m: None)["backup"], "no second backup when nothing changes"


# ------------------------------------------------------------------ the real changedetection.io ------------------
EXE = os.environ.get("RIFC_WATCH_EXE", "/tmp/claude-0/venv-cd/bin/changedetection.py")


class Site:
    """A tiny local website whose pages the test edits between scans."""
    def __init__(self):
        self.pages = {}
        site = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                p = site.pages.get(self.path.split("?")[0])
                if p is None:
                    self.send_response(404); self.end_headers(); self.wfile.write(b"missing"); return
                code, body = p if isinstance(p, tuple) else (200, p)
                self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.srv.shutdown()


def roster_html(players, updated="Nov 19, 2026 09:14 AM", ad="Offer ends 2026-11-30", visitors=1042):
    lis = "".join(f'<li class="p"><span>{n}</span> {name} {pos}</li>' for n, name, pos in players)
    return (f'<html><body><header><nav><a href="/">Home</a><a href="/tickets">Tickets</a></nav></header><div class="cookie-banner">Accept cookies</div>'
            f'<div class="ad">{ad}</div><main><h1>First Team</h1><p>Last updated: {updated}</p><h2>Goalkeepers</h2><ul>'
            f'{"".join(f"<li>{n} {nm} GK</li>" for n, nm, pos in players if pos == "GK")}</ul><h2>Forwards</h2><ul>'
            f'{"".join(f"<li>{n} {nm} FWD</li>" for n, nm, pos in players if pos == "FWD")}</ul></main>'
            f'<footer>© 2026 Alpha FC Visitors today: {visitors}</footer></body></html>')


PLAYERS = [(1, "Koke Vegas", "GK"), (2, "Aaron Keeper", "GK")] + [(i + 9, f"Striker{chr(65+i)} Surname{chr(65+i)}", "FWD") for i in range(8)]


def news_html(extra=()):
    rows = list(extra) + [("Old headline about the club schedule", "/a/old", "11/01/2026, 9:00am EST")]
    return ('<html><body><nav>Menu</nav><div class="pageElement aggregatorElement newsAggregatorElement">' + "".join(
        f'<div class="odd item condensed"><ul class="details"><li><h4><a href="{u}">{t}</a></h4></li><li class="dateAuthor"><span class="newsDate">{d}</span></li>'
        f'<li class="teaser expandedView">teaser</li></ul><span class="clearAll"></span></div>' for t, u, d in rows) + "</div></body></html>")


@pytest.mark.skipif(not Path(EXE).exists(), reason="changedetection.io is not installed (set RIFC_WATCH_EXE)")
def test_real_changedetection_end_to_end(tmp_path):
    site = Site()
    try:
        b = site.base
        site.pages["/roster"] = roster_html(PLAYERS)
        site.pages["/news"] = news_html()
        site.pages["/a/old"] = article("Old headline about the club schedule", ["The club released its schedule for the new season."])
        specs = [W.Spec("uslc-alpha:club_roster", "uslc-alpha", "Alpha FC", USLC, "roster", f"{b}/roster", "generic_roster"),
                 W.Spec("uslc-alpha:club_news", "uslc-alpha", "Alpha FC", USLC, "news", f"{b}/news", "ngin_news"),
                 W.Spec("uslc-alpha:broken", "uslc-alpha", "Alpha FC", USLC, "news", f"{b}/missing", "generic_news")]
        p = db_path(tmp_path)
        ctx = make_ctx()
        cfg = store.load_config()
        cfg["watch"] = {"enabled": True, "command": EXE, "allow_local": True, "page_timeout_s": 60, "page_delay_s": 0.1}
        ds = tmp_path / "cd"

        def go(now):
            factory = lambda: W.Runner(Path(EXE), ds, first_url=specs[0].url, allow_local=True, log_file=tmp_path / "cd.log")
            return S.run_scan(trigger="test", http=Http(timeout=10, retries=0, host_delay=0), ctx=ctx, path=p, sources=[], cfg=cfg, echo=False, now=now,
                              watch_kwargs={"specs": specs, "runner_factory": factory})["watch"]

        # 1. first look: a baseline, never a change; the broken page is reported as a failure, not silence
        w1 = go(NOW)
        assert w1["status"] == "partial" and w1["failed"] == 1 and w1["baselined"] == 2 and w1["changes"] == 0 and w1["events"] == 0
        assert any("broken" in f.lower() or "Alpha FC news" in f for f in w1["failures"])
        assert events(p).empty
        # 2. only cosmetic edits (timestamp, counter, ad text, a menu link): nothing detected
        site.pages["/roster"] = roster_html(PLAYERS, updated="Nov 21, 2026 11:30 AM", ad="Offer ends 2026-12-15", visitors=2210).replace("/tickets", "/shop")
        w2 = go(NOW + timedelta(days=1))
        assert w2["changes"] == 0 and w2["events"] == 0, w2
        # 3. a real roster change (one out, one in) and a new headline
        newp = [x for x in PLAYERS if x[1] != "Koke Vegas"] + [(14, "Brandon Diene", "FWD")]
        site.pages["/roster"] = roster_html(newp)
        site.pages["/news"] = news_html([("Alpha FC declines option on Juan Garcia", "/a/new", "11/19/2026, 9:00am EST")])
        site.pages["/a/new"] = article("Alpha FC declines option on Juan Garcia", ["Alpha FC declined contract options for Juan Garcia."])
        w3 = go(NOW + timedelta(days=2))
        assert w3["changes"] == 2 and w3["events"] >= 3, w3
        ev = events(p)
        by = {(r.player_text, r.event_type): r for r in ev.itertuples()}
        assert by[("Koke Vegas", "roster_removed")].confirmation == "unconfirmed" and by[("Koke Vegas", "roster_removed")].match_status == "matched"
        assert by[("Brandon Diene", "roster_added")].confirmation == "unconfirmed" and by[("Brandon Diene", "roster_added")].match_status == "review"
        decl = by[("Juan Garcia", "option_declined")]
        assert decl.confirmation == "announced" and decl.match_status == "matched" and decl.uid == "P_GARCIA_A"      # news page change -> normal reader
        # nothing left running
        import socket
        st = store.query("SELECT role, last_status, n_changes FROM rm_watch ORDER BY watch_id", path=p)
        assert set(st["last_status"]) == {"ok", "failed"}
        ch = store.query("SELECT added_json, removed_json, cosmetic_only, note FROM rm_watch_change", path=p)
        assert any("Koke Vegas" in x for x in ch["removed_json"]) and any("Brandon Diene" in x for x in ch["added_json"])
        cov = D.coverage_table(p)
        assert len(cov) > 0
    finally:
        site.stop()


def test_watching_is_off_until_installed_and_never_installs_silently(tmp_path):
    cfg = store.load_config()
    assert cfg["watch"]["enabled"] is False
    out = W.run_watches(make_ctx(), None, path=db_path(tmp_path), cfg=cfg)
    assert out["status"] == "off" and "off" in out["message"].lower()
    asked = []
    r = W.setup(assume_yes=False, input_fn=lambda q: asked.append(q) or "n", log=lambda m: asked.append(m))
    assert not r["ok"] and "Nothing was changed" in r["message"] and any("550 MB" in str(x) for x in asked) and any("awake" in str(x) for x in asked)
    cfg["watch"]["enabled"] = True
    (tmp_path / "x").mkdir()
    out = W.run_watches(make_ctx(), None, path=db_path(tmp_path / "x"), cfg=cfg, specs=[SPEC])
    assert out["status"] == "failed" and "not installed" in out["message"]
