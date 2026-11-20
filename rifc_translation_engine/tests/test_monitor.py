"""Recruitment monitor tests. Offline: a fake HTTP layer and a controlled fake identity layer, so nothing touches the
network and no real player is needed. The one test on the real release database (test_core_data_untouched) is skipped
when that database is not present.

Run:  python -m pytest -q tests/test_monitor.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="rifc_te_mon_"))
os.environ.setdefault("RIFC_TE_HOME", str(TMP))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from engine import config, db  # noqa: E402
from engine.monitor import ai, brief, inbox as IB, schedule, store  # noqa: E402
from engine.monitor.classify import extract_mentions, headline_type  # noqa: E402
from engine.monitor.clubs import ClubCatalog  # noqa: E402
from engine.monitor.http import Http, Robots  # noqa: E402
from engine.monitor.match import Matcher, Roster, name_compat  # noqa: E402
from engine.monitor.profile import Ctx, player_card, review_priority  # noqa: E402
from engine.monitor.scan import run_scan  # noqa: E402
from engine.monitor.sources import Source, parse_feed  # noqa: E402
from tests.fixtures.real_announcements import RIFC_2025, TAMPA_2025  # noqa: E402

USLC, USL1 = "USL Championship", "USL League One"
NOW = datetime.now().replace(microsecond=0)


# ------------------------------------------------------------------ fake world ---------------------------------
def fake_identity():
    """A small controlled identity layer: shaped like engine.identity.Identity."""
    P = []   # uid, name, club, league, season, pos, minutes, level, flags
    spec = [
        ("P_DIENE", "B. Diene", "One Knoxville SC", USL1, "2026", "ST", 1836, "Verified", []),
        ("P_GARCIA_A", "J. Garcia", "Alpha", USLC, "2026", "CM", 2100, "Verified", []),
        ("P_GARCIA_B", "J. Garcia", "Beta SC", USLC, "2026", "W", 1500, "Verified", []),
        ("P_SMITH_1", "A. Smith", "Gamma United", USLC, "2026", "CB", 1700, "Verified", []),
        ("P_SMITH_2", "A. Smith", "Gamma United", USLC, "2025", "CB", 900, "Verified", []),
        ("P_BROWN", "K. Brown", "Delta", USLC, "2024", "CM", 1300, "Verified", []),
        ("P_FLAG", "R. Flagged", "Alpha", USLC, "2026", "ST", 1900, "Uncertain", ["Over 1,500 minutes in two leagues"]),
        ("P_STALE", "M. Stale", "Echo", USLC, "2024", "ST", 2000, "Verified", []),
        ("P_VEGAS", "Koke Vegas", "Alpha", USLC, "2026", "GK", 2500, "Verified", []),
    ]
    pl, rec = [], []
    for uid, nm, club, lg, season, pos, mins, lvl, flags in spec:
        pl.append({"uid": uid, "display_name": nm, "aliases": [nm], "birth_year": 1997.0, "nationality": "US", "height_cm": 180.0,
                   "foot": "right", "first_season": season, "last_season": season, "last_season_start": float(season) + .2,
                   "last_club": club, "last_league": lg, "last_position": pos, "last_partial": False, "n_records": 1, "n_seasons": 1,
                   "total_minutes": float(mins), "identity_level": lvl, "flags": flags, "model_pid": 1, "cluster": 1.0,
                   "leagues": [lg], "clubs": [club], "recency": "In the latest data" if season == "2026" else "Not in later data",
                   "recent": season == "2026", "seasons_since": 2026 - int(season), "is_rifc_now": False})
        rec.append({"uid": uid, "league": lg, "season": season, "season_start": float(season) + .2, "team": club,
                    "position_group": pos, "minutes": float(mins), "matches": 25.0, "current_team": club, "display_only": False,
                    "age_at_season": 27.0, "contract_expires": "2026-12-31"})
    players, records = pd.DataFrame(pl), pd.DataFrame(rec)
    stints = pd.DataFrame([{"uid": r["uid"], "provider": "Wyscout", "league": r["league"], "season": r["season"], "team": r["team"],
                            "minutes": r["minutes"], "matches": 25.0, "position_raw": r["position_group"], "import_id": 1,
                            "wyscout_team_field": r["team"], "contract_expires": "2026-12-31", "export_date": "2026-11-05",
                            "season_start": r["season_start"]} for r in rec])
    return SimpleNamespace(players=players, records=records, stints=stints, review=pd.DataFrame(), similar=pd.DataFrame(),
                           league_latest={USLC: "2026", USL1: "2026"}, latest_season="2026")


def make_ctx(shortlist_uids=()):
    idn = fake_identity()
    ctx = Ctx(eng=object(), idn=idn)
    ctx.roster = Roster(idn)
    ctx.catalog = ClubCatalog(list(idn.records[["team", "league"]].drop_duplicates().itertuples(index=False)))
    ctx.shortlist = pd.DataFrame({"key": list(shortlist_uids), "role": ["Center forward"] * len(shortlist_uids),
                                  "note": ["watch"] * len(shortlist_uids), "added_at": [NOW] * len(shortlist_uids)})
    return ctx


class World:
    """Fake internet: url -> (status, body). Unknown robots.txt is 404 (allowed)."""
    def __init__(self):
        self.pages, self.calls = {}, []

    def add(self, url, body="", status=200, ctype="text/html"):
        self.pages[url] = (status, body, ctype)

    def opener(self, url, timeout, max_bytes):
        self.calls.append(url)
        if url in self.pages:
            st_, body, ctype = self.pages[url]
            if isinstance(body, Exception):
                raise body
            return st_, {"Content-Type": ctype + "; charset=utf-8"}, body.encode(), url
        return 404, {}, b"not found", url

    def http(self):
        return Http(timeout=1, retries=2, sleep=lambda s: None, opener=self.opener, host_delay=0)


def rss(items):
    xs = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{format_datetime(d)}</pubDate><description>{s}</description></item>"
                 for t, u, d, s in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{xs}</channel></rss>'


def article(title, paras, when=None):
    when = when or (NOW - timedelta(days=1))
    p = "".join(f"<p>{x}</p>" for x in paras)
    return (f'<html><head><meta property="og:title" content="{title}"/><meta property="article:published_time" content="{when.isoformat()}-05:00"/>'
            f"</head><body><nav>menu</nav>{p}<p>Follow the USL Championship</p><p>Most recent headline that must not be read as this story</p></body></html>")


def db_path(tmp_path):
    p = tmp_path / "t.duckdb"
    db.init_db(p)
    return p


FEED = "https://feed.test/rss"
SRC = [Source("t1", "Test feed", "rss", FEED, USLC)]


def scan(world, path, ctx=None, sources=None, **kw):
    cfg = store.load_config()
    return run_scan(trigger="test", http=world.http(), ctx=ctx or make_ctx(), path=path, sources=sources or SRC, cfg=cfg, echo=False, **kw)


def events(path):
    return IB.load_events(path)


# ------------------------------------------------------------------ reading announcements -----------------------
def test_reads_real_wording():
    cat = ClubCatalog([("Rhode Island", USLC), ("Tampa Bay Rowdies", USLC)])
    m = {x.name: x.event_type for x in extract_mentions(RIFC_2025, cat, USLC)}
    assert m["Koke Vegas"] == "option_exercised" and m["JJ Williams"] == "option_exercised"
    assert m["Will Meyer"] == "option_declined" and m["Taimu Okiyoshi"] == "option_declined"
    assert m["Marc Ybarra"] == "out_of_contract" and m["Joe Brito"] == "retirement"
    assert m["Hugo Bacharach"] == "under_contract"
    assert "Chris Cowger" not in m, "photo credit must not be read as a player"
    t = {x.name: x.event_type for x in extract_mentions(TAMPA_2025, ClubCatalog([("Tampa Bay Rowdies", USLC)]), USLC)}
    assert t["Bill Hamid"] == "option_declined" and t["Cristian Ortiz"] == "option_declined"
    assert t["Juan Carlos Azocar"] == "out_of_contract" and t["Nate Worth"] == "option_exercised"


def test_headlines_and_staff():
    assert headline_type("Greenville Triumph SC Names Mark Lowry Head Coach") is None
    assert headline_type("Huntsville City FC Announces Roster Updates Ahead of the 2027 MLS NEXT Pro Season") == "club_roster_update"
    assert headline_type("Oakland Roots declines option on forward Jack Skahan") == "option_declined"
    assert headline_type("Charleston Battery’s Colton Swan voted 2026 USL Championship Young Player of the Year") == "performance"
    assert [x.name for x in extract_mentions("The club parted ways with head coach Pascal Jansen after Decision Day.")] == []


# ------------------------------------------------------------------ similar names / matching ---------------------
def test_name_compat_rules():
    assert name_compat("Brandon Diene", "B. Diene") == "initial"
    assert name_compat("Carlos Garcia", "J. Garcia") is None          # different first initial
    assert name_compat("Marc Ybarra", "Marco Ybarra") == "prefix"
    assert name_compat("Koke Vegas", "Koke Vegas") == "exact"


def matcher(ctx=None, decisions=None):
    ctx = ctx or make_ctx()
    return Matcher(ctx.roster, ctx.catalog, decisions)


def test_similar_names_never_link_on_name_alone():
    m = matcher()
    # same surname and initial at two different clubs: the club decides
    a = m.match("Juan Garcia", "Alpha FC", USLC)
    assert a.status == "matched" and a.uid == "P_GARCIA_A"
    b = m.match("Jose Garcia", "Beta SC", USLC)
    assert b.status == "matched" and b.uid == "P_GARCIA_B"
    # a club that neither of them played for: not linked to either
    c = m.match("Juan Garcia", "Zeta FC", USLC)
    assert c.status != "matched"
    # no club at all and only an initial-level name: lead, never a link
    d = m.match("Juan Garcia", None, USLC)
    assert d.status != "matched"
    # full name that is only a surname twin
    assert m.match("Carlos Garcia", "Alpha FC", USLC).status == "unmatched"


def test_ambiguous_goes_to_review():
    m = matcher()
    r = m.match("Adam Smith", "Gamma United", USLC)          # two A. Smith at the same club
    assert r.status == "review" and r.uid is None and len(r.candidates) == 2
    assert "cannot choose" in " ".join(r.reasons)


def test_flagged_identity_is_not_auto_linked():
    r = matcher().match("Ryan Flagged", "Alpha FC", USLC)
    assert r.status == "review"
    assert any("identity" in f.lower() for f in r.candidates[0]["flags"])


def test_old_club_history_is_review_not_match():
    r = matcher().match("Kevin Brown", "Delta", USLC)         # last at Delta in 2024; league latest is 2026
    assert r.status == "review" and r.grade == "Possible"


def test_position_label_differences_do_not_block_but_contradictions_do():
    from engine.monitor.classify import POSITION_COMPAT
    m = matcher()
    assert m.match("Koke Vegas", "Alpha FC", USLC, "GK").status == "matched"
    assert m.match("Koke Vegas", "Alpha FC", USLC, "FWD").status == "review"       # a goalkeeper called a forward
    assert "W" in POSITION_COMPAT["MID"] and "DM" in POSITION_COMPAT["DEF"]


def test_your_answers_are_applied_before_anything_else():
    ctx = make_ctx()
    key = ("adam smith", ctx.catalog.lookup("Gamma United").key)
    dec = pd.DataFrame([{"name_norm": key[0], "club_norm": key[1], "uid": "P_SMITH_2", "verdict": "same"}])
    r = Matcher(ctx.roster, ctx.catalog, dec).match("Adam Smith", "Gamma United", USLC)
    assert r.status == "matched" and r.uid == "P_SMITH_2" and r.grade == "Confirmed by you"
    dec2 = pd.DataFrame([{"name_norm": key[0], "club_norm": key[1], "uid": u, "verdict": "different"} for u in ("P_SMITH_1", "P_SMITH_2")])
    assert Matcher(ctx.roster, ctx.catalog, dec2).match("Adam Smith", "Gamma United", USLC).status == "unmatched"


# ------------------------------------------------------------------ scanning -----------------------------------
def roster_world(title="Alpha FC announces roster decisions", body=None):
    w = World()
    url = "https://feed.test/a1"
    w.add(FEED, rss([(title, url, NOW - timedelta(days=1), "")]), ctype="application/xml")
    w.add(url, article(title, body or ["Alpha FC declined contract options for Juan Garcia and Adam Smith, while Ryan Flagged is out of contract.",
                                       "Alpha FC exercised contract options for goalkeeper Koke Vegas."]))
    return w, url


def test_scan_creates_events_and_keeps_failures_visible(tmp_path):
    p = db_path(tmp_path)
    w, _ = roster_world()
    s = scan(w, p)
    assert s["status"] == "ok" and s["new_events"] >= 3
    ev = events(p)
    got = {r.player_text: (r.event_type, r.match_status, r.inbox_status) for r in ev.itertuples()}
    assert got["Juan Garcia"] == ("option_declined", "matched", "New")
    assert got["Adam Smith"][1] == "unmatched" or got["Adam Smith"][1] == "review"
    assert got["Ryan Flagged"][1] == "review"
    row = ev[ev.player_text == "Juan Garcia"].iloc[0]
    assert row.sources[0]["url"].endswith("/a1") and row.sources[0]["quote"] and str(row.first_seen)


def test_sidebar_text_is_not_read_as_the_story(tmp_path):
    p = db_path(tmp_path)
    w, _ = roster_world(body=["Alpha FC declined contract options for Juan Garcia."])
    scan(w, p)
    assert "Most recent" not in " ".join(r.quote for r in events(p).itertuples())


def test_duplicate_news_is_one_event(tmp_path):
    p = db_path(tmp_path)
    w, url = roster_world()
    scan(w, p)
    n1 = len(events(p))
    scan(w, p)                                                   # same items again: nothing new
    assert len(events(p)) == n1
    # the same announcement reported by a second outlet (different URL): same event, two sources, not 'updated'
    url2 = "https://other.test/story"
    w.add(FEED, rss([("Alpha FC announces roster decisions", url, NOW - timedelta(days=1), ""),
                     ("Alpha declines options on Juan Garcia", url2, NOW - timedelta(hours=3), "")]), ctype="application/xml")
    w.add(url2, article("Alpha declines options on Juan Garcia", ["Alpha FC declined contract options for Juan Garcia."]))
    s = scan(w, p)
    ev = events(p)
    g = ev[(ev.player_text == "Juan Garcia") & (ev.event_type == "option_declined")]
    assert len(g) == 1 and int(g.iloc[0].n_sources) == 2 and not bool(g.iloc[0].updated_flag)
    assert s["dup_reports"] >= 1


def test_changed_article_is_flagged_as_update(tmp_path):
    p = db_path(tmp_path)
    w, url = roster_world(body=["Alpha FC declined contract options for Juan Garcia."])
    scan(w, p)
    w.add(url, article("Alpha FC announces roster decisions", ["Alpha FC declined contract options for Juan Garcia after the playoffs."]))
    scan(w, p)
    e = events(p).query("player_text == 'Juan Garcia'").iloc[0]
    assert bool(e.updated_flag) and "changed" in e.update_note.lower()


def test_old_items_are_ignored(tmp_path):
    p = db_path(tmp_path)
    w, url = roster_world()
    w.add(FEED, rss([("Alpha FC announces roster decisions", url, NOW - timedelta(days=200), "")]), ctype="application/xml")
    s = scan(w, p)
    assert s["skipped_old"] == 1 and events(p).empty


# ------------------------------------------------------------------ decisions are preserved --------------------
def test_your_decisions_survive_rescans_and_rebuilds(tmp_path):
    p = db_path(tmp_path)
    w, url = roster_world()
    scan(w, p)
    e = events(p).query("player_text == 'Juan Garcia'").iloc[0]
    IB.set_status(int(e.event_id), "Monitor", "check video in January", path=p)
    # a later report of the same event, and an edited article: status and note must stay
    w.add(url, article("Alpha FC announces roster decisions", ["Alpha FC declined contract options for Juan Garcia after the playoffs."]))
    scan(w, p)
    e2 = events(p).query("player_text == 'Juan Garcia'").iloc[0]
    assert e2.inbox_status == "Monitor" and e2.user_note == "check video in January" and bool(e2.user_decided)
    # the event row is lost (for example a rebuilt table): the saved decision comes back on the next read of the news
    with store.conn(p) as con:
        con.execute("DELETE FROM rm_event_evidence"); con.execute("DELETE FROM rm_events"); con.execute("DELETE FROM rm_items")
    scan(w, p)
    e3 = events(p).query("player_text == 'Juan Garcia'").iloc[0]
    assert e3.inbox_status == "Monitor" and e3.user_note == "check video in January"


def test_match_answers_are_remembered_for_later_scans(tmp_path):
    p = db_path(tmp_path)
    w, url = roster_world(body=["Alpha FC declined contract options for Adam Smith."])
    ctx = make_ctx()
    scan(w, p, ctx)
    e = events(p).query("player_text == 'Adam Smith'")
    assert len(e) == 1
    e = e.iloc[0]
    assert e.match_status in ("review", "unmatched")
    # the club for Smith is Gamma United, so make the news name it
    w2 = World()
    w2.add(FEED, rss([("Gamma United announces option decisions", "https://feed.test/g", NOW - timedelta(days=1), "")]), ctype="application/xml")
    w2.add("https://feed.test/g", article("Gamma United announces option decisions", ["Gamma United declined contract options for Adam Smith."]))
    scan(w2, p, ctx)
    g = events(p).query("player_text == 'Adam Smith' and club_text == 'Gamma United'").iloc[0]
    assert g.match_status == "review" and not g.uid
    IB.confirm_match(int(g.event_id), "P_SMITH_1", path=p)
    # a different outlet reports the same name at the same club later: your answer is applied
    w2.add(FEED, rss([("Gamma United announces option decisions", "https://feed.test/g", NOW - timedelta(days=1), ""),
                      ("Gamma United releases Adam Smith", "https://feed.test/g2", NOW, "")]), ctype="application/xml")
    w2.add("https://feed.test/g2", article("Gamma United releases Adam Smith", ["Gamma United released Adam Smith."]))
    scan(w2, p, ctx)
    g2 = events(p).query("player_text == 'Adam Smith' and event_type == 'released'").iloc[0]
    assert g2.uid == "P_SMITH_1" and g2.match_grade == "Confirmed by you"
    IB.reject_match(int(g.event_id), "P_SMITH_1", path=p)
    assert events(p).set_index("event_id").loc[int(g.event_id), "match_status"] == "unmatched"


def test_club_outside_database_announces_player_we_know(tmp_path):
    """Nashville SC (not in the database) signs a player whose recorded club is named in the same paragraph."""
    p = db_path(tmp_path)
    w = World()
    t = "Nashville SC sign forward Brandon Diene"
    w.add(FEED, rss([(t, "https://feed.test/n", NOW - timedelta(hours=2), "")]), ctype="application/xml")
    w.add("https://feed.test/n", article(t, ["Nashville SC have signed forward Brandon Diene, who joins from One Knoxville SC, through 2027.",
                                             "Nashville SC will present him next week."]))
    ctx = make_ctx()
    scan(w, p, ctx)
    e = events(p)
    r = e[e.player_text == "Brandon Diene"].iloc[0]
    assert r.club_text == "Nashville SC", "the announcing club, not the club he left"
    assert r.match_status == "matched" and r.uid == "P_DIENE" and r.event_type == "signing"
    # the same words with no club he is known at in that paragraph must not link him
    w2 = World()
    w2.add(FEED, rss([(t, "https://feed.test/n2", NOW - timedelta(hours=2), "")]), ctype="application/xml")
    w2.add("https://feed.test/n2", article(t, ["Nashville SC have signed forward Brandon Diene through 2027."]))
    p2 = db_path(tmp_path / "second") if False else db_path(tmp_path)
    with store.conn(p2) as con:
        con.execute("DELETE FROM rm_event_evidence"); con.execute("DELETE FROM rm_events"); con.execute("DELETE FROM rm_items")
    scan(w2, p2, make_ctx())
    assert events(p2).query("player_text == 'Brandon Diene'").empty or events(p2).query("player_text == 'Brandon Diene'").iloc[0].match_status != "matched"


def test_club_named_elsewhere_in_article_queues_a_review_never_links(tmp_path):
    p = db_path(tmp_path)
    w = World()
    t = "Nashville SC sign forward Brandon Diene"
    w.add(FEED, rss([(t, "https://feed.test/e", NOW - timedelta(hours=2), "")]), ctype="application/xml")
    w.add("https://feed.test/e", article(t, ["Nashville SC have signed forward Brandon Diene through 2027.",
                                             "He scored 12 goals last season for One Knoxville SC in League One."]))
    scan(w, p, make_ctx())
    r = events(p).query("player_text == 'Brandon Diene'").iloc[0]
    assert r.match_status == "review" and not r.uid and r.club_text == "Nashville SC"
    assert any(c["club_level"] == "article" for c in json.loads(r.candidates_json))


def test_list_of_names_is_never_the_club():
    from engine.monitor.classify import title_club
    assert title_club("Thiago Martins, Keaton Parks to leave NYCFC at season end") is None
    assert title_club("Nashville SC sign homegrown winger Liam Stribling") == "Nashville SC"
    assert title_club("Charleston Battery’s Colton Swan voted Young Player of the Year") == "Charleston Battery"


def test_unmatched_names_stay_leads_without_invented_profiles(tmp_path):
    p = db_path(tmp_path)
    w, _ = roster_world(body=["Alpha FC declined contract options for Zed Nobody."])
    ctx = make_ctx()
    scan(w, p, ctx)
    ev = IB.enrich(events(p), ctx)
    r = ev.iloc[0]
    assert r.match_status == "unmatched" and r.uid is None and r.card is None and r.tier == "Lead"
    assert pd.isna(r.minutes) and r.display_name is None
    assert "no statistical profile" in " ".join(r.factors).lower() or "not in our database" in " ".join(r.factors).lower()


# ------------------------------------------------------------------ availability wording ------------------------
def test_signing_and_extension_are_updates_not_leads():
    ctx = make_ctx()
    card = player_card(ctx, "P_GARCIA_A")
    sign = review_priority({"event_type": "extension", "announced_at": NOW}, card)
    depart = review_priority({"event_type": "option_declined", "announced_at": NOW}, card)
    assert sign["tier"] == "Low" and depart["points"] > sign["points"]
    assert any("not a lead" in f for f in sign["factors"])
    # shortlisted players still surface for any change
    ctx2 = make_ctx(["P_GARCIA_A"])
    c2 = player_card(ctx2, "P_GARCIA_A")
    assert review_priority({"event_type": "extension", "announced_at": NOW}, c2)["tier"] in ("Medium", "High")


def test_departure_text_does_not_claim_free_agent():
    from engine.monitor.profile import inference_for
    txt = " ".join(inference_for("option_declined", True))
    assert "Not established" in txt and "free agent" in txt


def test_stale_data_and_old_announcements_are_labelled():
    ctx = make_ctx()
    card = player_card(ctx, "P_STALE")
    assert card["seasons_since_last_record"] == 2 and card["last_season"] == "2024"
    p = review_priority({"event_type": "option_declined", "announced_at": NOW}, card)
    assert any("older than" in f or "season(s) behind" in f for f in p["factors"])
    cur = review_priority({"event_type": "option_declined", "announced_at": NOW}, player_card(ctx, "P_GARCIA_A"))
    assert cur["points"] > p["points"]
    # an announcement older than his latest record is down-weighted
    old = review_priority({"event_type": "option_declined", "announced_at": datetime(2025, 12, 4)}, player_card(ctx, "P_GARCIA_A"))
    assert old["points"] < cur["points"] and any("superseded" in f for f in old["factors"])


# ------------------------------------------------------------------ blocked, failing, interrupted ---------------
def test_blocked_and_failed_sources_are_reported_and_others_continue(tmp_path):
    p = db_path(tmp_path)
    w, url = roster_world()
    w.add("https://blocked.test/robots.txt", "User-agent: *\nDisallow: /")
    w.add("https://blocked.test/feed", rss([]), ctype="application/xml")
    w.add("https://forbidden.test/feed", "no", status=403)
    w.add("https://down.test/feed", "err", status=503)
    w.add("https://junk.test/feed", "<html>this is not a feed</html>")
    w.add("https://slow.test/feed", OSError("timed out"))
    srcs = SRC + [Source("b", "Blocked by robots", "rss", "https://blocked.test/feed"), Source("f", "Forbidden", "rss", "https://forbidden.test/feed"),
                  Source("d", "Down", "rss", "https://down.test/feed"), Source("j", "Junk", "rss", "https://junk.test/feed"),
                  Source("s", "Slow", "rss", "https://slow.test/feed"), Source("o", "Off", "rss", "https://x.test", enabled=False)]
    s = scan(w, p, sources=srcs)
    st_ = {x["id"]: x for x in s["sources"]}
    assert st_["t1"]["status"] == "ok" and st_["b"]["status"] == "blocked" and "robots" in st_["b"]["error"]
    assert st_["f"]["status"] == "blocked" and st_["d"]["status"] == "failed" and st_["j"]["status"] == "failed"
    assert st_["s"]["status"] == "failed" and st_["o"]["status"] == "skipped"
    assert s["status"] == "partial" and len(events(p)) >= 3                     # the good source still delivered
    assert sum(1 for c in w.calls if c == "https://down.test/feed") == 3          # retried (1 + 2 retries), then gave up
    state = store.query("SELECT * FROM rm_source_state", path=p).set_index("source_id")
    assert state.loc["d", "consecutive_failures"] == 1 and state.loc["t1", "consecutive_failures"] == 0
    scan(w, p, sources=srcs)
    assert store.query("SELECT consecutive_failures FROM rm_source_state WHERE source_id='d'", path=p).iloc[0, 0] == 2
    h = brief.source_health(p)
    assert any("Down" in x for x in h["problems"]) and any("Blocked by robots" in x for x in h["problems"])


def test_interrupted_scan_leaves_complete_items_and_resumes(tmp_path, monkeypatch):
    p = db_path(tmp_path)
    w = World()
    items = [(f"Alpha FC declines option on Juan Garcia {i}", f"https://feed.test/i{i}", NOW - timedelta(hours=i + 1), "") for i in range(3)]
    w.add(FEED, rss(items), ctype="application/xml")
    for t, u, d, s_ in items:
        w.add(u, article(t, ["Alpha FC declined contract options for Juan Garcia."]))
    import engine.monitor.scan as S
    real, n = S.fetch_article, {"i": 0}

    def flaky(url, http, robots):
        n["i"] += 1
        if n["i"] == 2:
            raise KeyboardInterrupt()
        return real(url, http, robots)
    monkeypatch.setattr(S, "fetch_article", flaky)
    with pytest.raises(KeyboardInterrupt):
        scan(w, p)
    last = store.query("SELECT status FROM rm_scans ORDER BY scan_id DESC LIMIT 1", path=p).iloc[0, 0]
    assert last == "interrupted"
    assert not S.lock_path().exists(), "the lock must be released after an interruption"
    n_items = store.query("SELECT COUNT(*) FROM rm_items", path=p).iloc[0, 0]
    assert n_items == 1                                           # only the complete first item was written
    monkeypatch.setattr(S, "fetch_article", real)
    s = scan(w, p)
    assert s["status"] == "ok" and store.query("SELECT COUNT(*) FROM rm_items", path=p).iloc[0, 0] == 3
    ev = events(p)
    assert len(ev[ev.player_text == "Juan Garcia"]) == 1 and int(ev[ev.player_text == "Juan Garcia"].iloc[0].n_sources) == 3


def test_stale_running_scan_is_marked_interrupted_and_lock_is_taken_over(tmp_path):
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    old = datetime.now() - timedelta(hours=3)
    with store.conn(p) as con:
        con.execute("INSERT INTO rm_scans VALUES (99, ?, NULL, ?, 'running', 'old', '{}')", [old, old])
    import engine.monitor.scan as S
    S.lock_path().write_text(json.dumps({"pid": 999999, "ts": old.timestamp()}))      # a dead process
    w, _ = roster_world()
    s = scan(w, p)
    assert s["status"] == "ok"
    assert store.query("SELECT status FROM rm_scans WHERE scan_id = 99", path=p).iloc[0, 0] == "interrupted"


def test_second_scan_while_one_runs_is_refused(tmp_path):
    p = db_path(tmp_path)
    import engine.monitor.scan as S
    S.lock_path().write_text(json.dumps({"pid": os.getpid(), "ts": datetime.now().timestamp()}))
    try:
        s = scan(World(), p)
        assert s["status"] == "skipped"
    finally:
        S.release_lock()


# ------------------------------------------------------------------ migration and protection --------------------
def test_migration_backs_up_first_and_never_touches_existing_tables(tmp_path):
    p = tmp_path / "m.duckdb"
    db.init_db(p)
    with db.connect(p) as con:
        con.execute("CREATE TABLE IF NOT EXISTS shortlist (key VARCHAR PRIMARY KEY, role VARCHAR, note VARCHAR, added_at TIMESTAMP)")
        con.execute("INSERT INTO shortlist VALUES ('P1','Winger','keep me', now())")
        before = con.execute("SELECT * FROM shortlist").df().to_dict("records")
    info = store.ensure_schema(p, log=lambda m: None)
    assert info["created"] and info["backup"] and (Path(info["backup"]) / "m.duckdb").exists()
    bk = duckdb.connect(str(Path(info["backup"]) / "m.duckdb"), read_only=True)
    assert "rm_events" not in {r[0] for r in bk.execute("SELECT table_name FROM information_schema.tables").fetchall()}   # backup = before
    bk.close()
    with db.connect(p) as con:
        assert con.execute("SELECT * FROM shortlist").df().to_dict("records") == before
    again = store.ensure_schema(p, log=lambda m: None)
    assert not again["created"] and again["backup"] is None                          # no second backup, no change


def test_shortlist_uses_the_existing_workflow(tmp_path):
    ctx = make_ctx()
    from engine import recruit as R
    R.shortlist_add  # the existing function
    role = IB.add_to_shortlist(ctx, "P_DIENE", None, "from inbox")
    sl = R.shortlist()
    row = sl[sl["key"] == "P_DIENE"].iloc[0]
    assert row["role"] == role == "Center forward" and row["note"] == "from inbox"
    R.shortlist_remove("P_DIENE")


# ------------------------------------------------------------------ brief and export ---------------------------
def test_brief_prioritises_shortlist_and_does_not_pad(tmp_path):
    p = db_path(tmp_path)
    w = World()
    w.add(FEED, rss([("One Knoxville SC declines option on Brandon Diene", "https://feed.test/d", NOW - timedelta(hours=5), ""),
                     ("Alpha FC re-signs Juan Garcia", "https://feed.test/g", NOW - timedelta(hours=4), "")]), ctype="application/xml")
    w.add("https://feed.test/d", article("One Knoxville SC declines option on Brandon Diene", ["One Knoxville SC declined contract options for Brandon Diene."]))
    w.add("https://feed.test/g", article("Alpha FC re-signs Juan Garcia", ["Alpha FC re-signed midfielder Juan Garcia through 2028."]))
    ctx = make_ctx(["P_DIENE"])
    scan(w, p, ctx)
    b = brief.build_brief(ctx, path=p, save=False)
    assert [i["title"].split(" (")[0] for i in b["sections"]["shortlist"]] == ["B. Diene"]
    flat = [i["title"] for v in b["sections"].values() for i in v]
    assert not any("J. Garcia" in t for t in flat), "an extension for a player you do not follow must not fill the brief"
    md = b["markdown"]
    assert "Source says" in md and "Our records" in md and "Our reading (inference)" in md and "Review next" in md
    assert "https://feed.test/d" in md and "free agent" in md and "Not established" in md
    # written today, then again tomorrow with nothing new: nothing is repeated and nothing is added to fill space
    today = NOW.date()
    brief.build_brief(ctx, today=today, path=p, save=True)
    nxt = brief.build_brief(ctx, today=today + timedelta(days=1), path=p, save=True)
    assert not any(nxt["sections"].values()) and "Nothing new needs review" in nxt["markdown"]


def test_csv_keeps_source_database_and_inference_apart(tmp_path):
    p = db_path(tmp_path)
    w, _ = roster_world()
    ctx = make_ctx(["P_GARCIA_A"])
    scan(w, p, ctx)
    csv = IB.to_csv(IB.enrich(events(p), ctx))
    head = csv.splitlines()[0].split(",")
    assert any(h.startswith("SOURCE_") for h in head) and any(h.startswith("DB_") for h in head) and any(h.startswith("INFERRED_") for h in head)
    df = pd.read_csv(pd.io.common.StringIO(csv))
    g = df[df["SOURCE_player_as_written"] == "Juan Garcia"].iloc[0]
    assert g["DB_shortlisted"] and g["player_id"] == "P_GARCIA_A" and "https://feed.test/a1" in g["SOURCE_links"]


# ------------------------------------------------------------------ http, robots, scheduling, AI -----------------
def test_http_retries_transient_errors_but_not_policy_or_missing():
    w = World()
    calls = {"n": 0}

    def opener(url, timeout, mb):
        calls["n"] += 1
        return (503, {}, b"", url) if calls["n"] < 3 else (200, {}, b"ok", url)
    h = Http(retries=3, sleep=lambda s: None, opener=opener, host_delay=0)
    assert h.get("https://x.test/").ok and calls["n"] == 3
    w.add("https://gone.test/", "x", status=404)
    h2 = w.http(); r = h2.get("https://gone.test/")
    assert r.status == 404 and w.calls.count("https://gone.test/") == 1
    def policy(url, t, mb): raise OSError("Tunnel connection failed: 403 Forbidden")
    n = {"c": 0}
    def counting(url, t, mb): n["c"] += 1; return policy(url, t, mb)
    h3 = Http(retries=3, sleep=lambda s: None, opener=counting, host_delay=0)
    assert not h3.get("https://p.test/").ok and n["c"] == 1


def test_robots_rules():
    w = World()
    w.add("https://a.test/robots.txt", "User-agent: *\nDisallow: /private/\nAllow: /")
    w.add("https://b.test/robots.txt", "denied", status=403)
    w.add("https://c.test/robots.txt", "x", status=500)
    rb = Robots(w.http())
    assert rb.allowed("https://a.test/news")[0] and not rb.allowed("https://a.test/private/x")[0]
    assert not rb.allowed("https://b.test/news")[0]
    assert not rb.allowed("https://c.test/news")[0]
    assert rb.allowed("https://d.test/news")[0]                                   # no robots.txt: allowed


def test_feed_parsing_rejects_non_feeds():
    assert parse_feed(rss([("T", "https://x/y", NOW, "s")]))[0]["title"] == "T"
    with pytest.raises(ValueError):
        parse_feed("<html><body>not a feed</body></html>")


def test_schedule_plist_and_non_mac_refusal():
    d = schedule.build_plist("/py", "/app dir", "/home dir", 7, 15)
    assert d["StartCalendarInterval"] == {"Hour": 7, "Minute": 15} and d["WorkingDirectory"] == "/app dir"
    assert d["ProgramArguments"][-4:] == ["--if-stale", "12", "--trigger", "schedule"] and d["EnvironmentVariables"]["RIFC_TE_HOME"] == "/home dir"
    if sys.platform != "darwin":
        existed = schedule.plist_path().exists()
        r = schedule.install("/app", str(TMP))
        assert r["ok"] is False and "Mac" in r["message"]
        assert schedule.plist_path().exists() == existed, "nothing may be written on a computer without launchd"


def test_ai_is_off_by_default_capped_cached_and_cannot_invent_names(tmp_path, monkeypatch):
    p = db_path(tmp_path)
    store.ensure_schema(p, log=lambda m: None)
    cfg = store.load_config()
    assert ai.extract("Alpha FC declined options for Juan Garcia", "Alpha", cfg, {}, p) == []              # off
    cfg["ai"].update({"enabled": True, "usd_per_mtok_in": 1.0, "usd_per_mtok_out": 5.0, "monthly_usd_cap": 0.0001, "max_calls_per_scan": 2})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.extract("x", None, cfg, {}, p) == [] and "ANTHROPIC_API_KEY" in ai.available(cfg)[1]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    text = "Alpha FC declined contract options for Juan Garcia and Adam Smith."
    answer = {"content": [{"text": json.dumps({"players": [{"name": "Juan Garcia", "type": "option_declined", "sentence": text},
                                                           {"name": "Made Up", "type": "released", "sentence": "x"},
                                                           {"name": "Adam Smith", "type": "bogus_type", "sentence": text}]})}],
              "usage": {"input_tokens": 1000, "output_tokens": 100}}
    calls = {"n": 0}
    def post(req):
        calls["n"] += 1
        return answer
    st_ = {}
    out = ai.extract(text, "Alpha", cfg, st_, p, post=post)
    assert [m.name for m in out] == ["Juan Garcia"], "invented names and unknown types must be dropped"
    assert ai.extract(text, "Alpha", cfg, st_, p, post=post)[0].name == "Juan Garcia" and calls["n"] == 1      # cached
    assert ai.month_spend(p) > 0
    assert ai.extract(text + " More.", "Alpha", cfg, {}, p, post=post) == [] and calls["n"] == 1             # monthly cap reached


# ------------------------------------------------------------------ the real release database -------------------
REAL = Path(__file__).resolve().parent.parent / "data" / "engine.duckdb"


@pytest.mark.skipif(not REAL.exists(), reason="release database not present")
def test_core_data_untouched_on_real_database(tmp_path):
    """Runs the monitor over a copy of the real release database with the real RIFC announcement text and proves the
    engine's own tables are byte for byte unchanged, while the monitor finds real players."""
    import shutil
    from engine import identity as I
    home = tmp_path / "home"
    shutil.copytree(REAL.parent, home / "data", ignore=shutil.ignore_patterns("_rebuild_copies"))
    code = f"""
import os, sys, json, hashlib
os.environ['RIFC_TE_HOME'] = {str(home)!r}
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
import duckdb
from engine import config
from engine.monitor import store, inbox as IB
from engine.monitor.scan import run_scan
from engine.monitor.sources import Source
from engine.monitor.http import Http
from tests.fixtures.real_announcements import RIFC_2025
def digest():
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    out = {{}}
    for t in ('player_seasons','imports','shortlist','model_runs','backtest_runs','identity_registry','club_verifications','transfers_ref','team_seasons'):
        out[t] = hashlib.sha256(con.execute(f'SELECT * FROM {{t}} ORDER BY ALL').df().to_csv().encode()).hexdigest()
    con.close(); return out
before = digest()
page = '<html><head><meta property="og:title" content="Rhode Island FC announces roster decisions"/><meta property="article:published_time" content="2025-12-04T10:00:00-05:00"/></head><body>' + ''.join('<p>'+l+'</p>' for l in RIFC_2025.split(chr(10))) + '</body></html>'
def opener(url, t, mb):
    if url.endswith('robots.txt'): return 404, {{}}, b'', url
    return 200, {{'Content-Type': 'text/html; charset=utf-8'}}, page.encode(), url
s = run_scan(trigger='test', http=Http(opener=opener, sleep=lambda x: None, host_delay=0), backfill=True, echo=False,
             sources=[Source('manual','Fixture','single','https://www.uslchampionship.com/news_article/show/1351058','USL Championship')])
after = digest()
ev = IB.load_events()
print(json.dumps({{'same': before == after, 'status': s['status'], 'n': len(ev), 'matched': int((ev.match_status=='matched').sum()),
                  'by_match': s['by_match'], 'koke': ev[ev.player_text=='Koke Vegas'][['uid','match_status']].values.tolist()}}))
"""
    import subprocess
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["same"] is True, "the monitor changed the engine's own tables"
    assert res["status"] == "ok" and res["n"] >= 25 and res["matched"] >= 20
    assert res["koke"] and res["koke"][0][1] == "matched"
