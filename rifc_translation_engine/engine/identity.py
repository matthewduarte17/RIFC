"""Player identity layer: one player, one stable ID, one profile with every season.

The translation model links season rows into players in engine/transfers.py (union find with
guards). That linking is the model's and is not changed here. This layer reads the same result
and builds the recruitment side on top of it:

* a stable player ID (P + 7 hex) kept in the database table identity_registry, keyed by each
  season row's natural key (provider, name, club, league, season), so the ID survives rebuilds
  and new imports;
* separate records: player identity, player season records, club stints, provider, current
  club verification;
* profile level corrections where the evidence says the model's link is wrong: a player can
  appear only once in one league season export, so two clubs in the same league season inside
  one identity are two different people. Those identities are split for profiles and flagged.
  The model keeps its links until you confirm the split (Identity Review page, then refit);
* identity confidence and review flags;
* recency: last recorded club and season, kept apart from the current club, which is only
  ever "verified" by a manual entry with a date and source.

Nothing here edits player_seasons, the model, its pairs or its backtest.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from . import db
from .config import DATA_DIR, MODEL_DIR
from .normalize import norm_name, norm_team, strip_accents
from .schema import USLC

DECISIONS = DATA_DIR / "identity_decisions.json"
VERSION = 6
LEAGUE_SHORT = {"USL Championship": "USLC", "USL League One": "USL1", "MLS Next Pro": "MLSNP"}

SQL = """
CREATE TABLE IF NOT EXISTS identity_registry (natkey VARCHAR PRIMARY KEY, uid VARCHAR, assigned_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS club_verifications (
  id INTEGER, uid VARCHAR, status VARCHAR, current_club VARCHAR, verified_on DATE, source VARCHAR,
  contract_expiry VARCHAR, contract_as_of DATE, note VARCHAR, entered_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS player_identity (uid VARCHAR, display_name VARCHAR, aliases VARCHAR, birth_year DOUBLE,
  nationality VARCHAR, height_cm DOUBLE, first_season VARCHAR, last_season VARCHAR, last_club VARCHAR,
  last_league VARCHAR, n_records INTEGER, identity_level VARCHAR, flags VARCHAR, model_pid BIGINT);
CREATE TABLE IF NOT EXISTS player_stints (uid VARCHAR, provider VARCHAR, league VARCHAR, season VARCHAR, team VARCHAR,
  minutes DOUBLE, matches DOUBLE, position_raw VARCHAR, import_id INTEGER, wyscout_team_field VARCHAR,
  contract_expires VARCHAR, export_date VARCHAR);
"""
STATUSES = ["At a club (verified)", "Free agent (verified)", "Retired or left the pro game", "Other"]


def natkey(r) -> str:
    by = "" if pd.isna(r["birth_year"]) else int(r["birth_year"])
    ht = "" if pd.isna(r.get("height_cm")) else int(r["height_cm"])
    nat = r.get("nationality") if isinstance(r.get("nationality"), str) else ""
    return f"{r['provider']}|{r['name_norm']}|{r['team']}|{r['league']}|{r['season']}|{by}|{nat}|{ht}"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
@dataclass
class Identity:
    rows: pd.DataFrame          # season rows used by the model, with model pid and uid
    records: pd.DataFrame       # player league season records per uid (same aggregation as the model)
    players: pd.DataFrame       # one row per uid
    stints: pd.DataFrame        # club stints
    review: pd.DataFrame        # flags for manual review
    similar: pd.DataFrame       # same name, kept as separate players
    audit: dict = field(default_factory=dict)
    league_latest: dict = field(default_factory=dict)
    latest_season: str | None = None


def _export_dates() -> dict:
    im = db.query("SELECT import_id, notes, imported_at FROM imports")
    out = {}
    for r in im.itertuples():
        m = re.search(r"(20\d\d-\d\d-\d\d)", str(r.notes or ""))
        out[r.import_id] = m.group(1) if m else (pd.to_datetime(r.imported_at).date().isoformat()
                                                if pd.notna(r.imported_at) else None)
    return out


def _decisions() -> list:
    try:
        return json.loads(DECISIONS.read_text()).get("decisions", [])
    except Exception:
        return []


def save_decision(action: str, uids: list[str], note: str = "", extra: dict | None = None):
    d = {"about": "Manual identity decisions from the Identity Review page. 'link' joins separate profiles for "
                  "display; 'confirm' records that a scout checked an identity; 'separate' confirms a split. "
                  "None of these change the model until a matching rule is written and the model is refitted.",
         "decisions": _decisions()}
    d["decisions"].append({"action": action, "uids": uids, "note": note, "at": datetime.now().isoformat(timespec="seconds"),
                           **(extra or {})})
    DECISIONS.write_text(json.dumps(d, indent=1))


def remove_decision(i: int):
    d = {"decisions": _decisions()}
    if 0 <= i < len(d["decisions"]):
        d["decisions"].pop(i)
    DECISIONS.write_text(json.dumps(d, indent=1))


def _resplit(g: pd.DataFrame) -> dict:
    """Sub identities inside one model identity that holds two clubs in one league season.
    Rows join only when no two of their rows sit at different clubs in the same league season:
    first by club, then by identical birth year. Returns row_id -> sub index."""
    rows = g.to_dict("records")
    parent = list(range(len(rows)))
    members = {i: [i] for i in range(len(rows))}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def conflict(a, b):
        for i in members[a]:
            for j in members[b]:
                x, y = rows[i], rows[j]
                if x["league"] == y["league"] and x["season"] == y["season"] and x["team"] != y["team"]:
                    return True
        return False

    def union(i, j):
        a, b = find(i), find(j)
        if a == b or conflict(a, b):
            return
        parent[b] = a
        members[a] += members.pop(b)

    for i in range(len(rows)):
        for j in range(i):
            if rows[i]["team"] == rows[j]["team"]:
                union(j, i)
    for i in range(len(rows)):
        for j in range(i):
            if pd.notna(rows[i]["birth_year"]) and rows[i]["birth_year"] == rows[j]["birth_year"]:
                union(j, i)
    roots = sorted({find(i) for i in range(len(rows))})
    return {rows[i]["row_id"]: roots.index(find(i)) for i in range(len(rows))}


def build(eng, path=None, progress=lambda m: None) -> Identity:
    from .transfers import (aggregate_player_league_seasons, drop_duplicate_profiles, resolve_identities,
                            team_context)
    progress("Linking season rows")
    # model rows (active imports) plus rows from datasets marked "Profiles and discovery only"
    ps = db.query("SELECT t.*, i.active AS _model_row FROM player_seasons t JOIN imports i USING (import_id) "
                  "WHERE i.active OR COALESCE(i.display_only, FALSE)", path=path)
    ps = ps[ps["name_norm"].notna() & ps["season_start"].notna() & ps["league"].notna()]
    ps, dup_report = drop_duplicate_profiles(ps)
    # display only rows: one player under two Wyscout profiles (name spelt with and without a space, minutes within
    # 2%) is shown once. Model rows are never touched here.
    from .datasets import near_duplicates
    disp_rows = ps[~ps["_model_row"].astype(bool)]
    near_dropped = []
    if not disp_rows.empty:
        nd = near_duplicates(disp_rows)
        for a, b, na, nb, ma, mb in nd[["row_id", "row_id_b", "player_name", "player_name_b", "minutes",
                                        "minutes_b"]].values:
            good_a = bool(re.search(r"\. \S", str(na))) or " " in str(na)
            good_b = bool(re.search(r"\. \S", str(nb))) or " " in str(nb)
            drop = b if (good_a and not good_b) or (good_a == good_b and ma >= mb) else a
            near_dropped.append(int(drop))
        ps = ps[~ps["row_id"].isin(near_dropped)]
    tr = db.load_table("transfers_ref", path, active_only=True)
    act = ps["_model_row"].astype(bool)
    if (~act).any():
        # the model's own links (active rows only), then the profile links with display only rows added
        psa, _ = resolve_identities(ps[act], tr)
        mp = dict(zip(psa["row_id"], psa["pid"]))
        ps, _ = resolve_identities(ps, tr)
        ps["mpid"] = ps["row_id"].map(mp)
    else:
        ps, _ = resolve_identities(ps, tr)
        ps["mpid"] = ps["pid"]
    ps = ps[ps["minutes"].fillna(0) > 0].copy()
    for c in ps.columns:                       # numpy object strings: much faster than arrow for row loops
        if str(ps[c].dtype) in ("string", "str") or "string" in str(ps[c].dtype):
            ps[c] = ps[c].astype(object)
    ps["natkey"] = ps.apply(natkey, axis=1)
    # two different players can share every field above (same name, club, season): add minutes to tell them apart
    clash_nk = ps.groupby("natkey")["pid"].nunique()
    clash_nk = set(clash_nk[clash_nk > 1].index)
    m_ = ps["natkey"].isin(clash_nk)
    ps.loc[m_, "natkey"] = ps.loc[m_, "natkey"] + "|" + ps.loc[m_, "minutes"].round().astype(int).astype(str)
    exp = _export_dates()
    ps["export_date"] = ps["import_id"].map(exp)

    # same as the model? (the identity layer must start from the model's links)
    model_pids = set(eng.agg["pid"]) if eng is not None and not eng.agg.empty else set(ps["pid"])
    same_as_model = set(ps.loc[ps["_model_row"].astype(bool), "mpid"]) == model_pids
    bridged = int((ps.dropna(subset=["mpid"]).groupby("pid")["mpid"].nunique() > 1).sum())

    # profile level splits: two clubs in one league season inside one model identity
    progress("Checking identities")
    st = ps.drop_duplicates(["pid", "league", "season", "provider", "team"])
    clash = st.groupby(["pid", "league", "season", "provider"])["team"].nunique()
    clash_pids = set(clash[clash > 1].index.get_level_values(0))
    ps["sub"] = 0
    for pid in clash_pids:
        g = ps[ps["pid"] == pid]
        m = _resplit(g)
        ps.loc[g.index, "sub"] = g["row_id"].map(m)
    ps["cluster"] = ps["pid"].astype(str) + "." + ps["sub"].astype(str)

    # stable IDs
    with db.connect(path) as con:
        con.execute(SQL)
        reg = con.execute("SELECT * FROM identity_registry").df()
    known = dict(zip(reg["natkey"], reg["uid"])) if not reg.empty else {}
    used = set(known.values())
    uid_of = {}
    new_rows = []
    order = ps.sort_values(["season_start", "natkey"])
    claimed = {}
    for cl, g in order.groupby("cluster", sort=False):
        have = [known[k] for k in g["natkey"] if k in known]
        uid = None
        if have:
            cand = pd.Series(have).value_counts()
            for u in cand.index:                      # a uid split across clusters stays with its largest part
                if u not in claimed:
                    uid = u
                    break
        if uid is None:
            seed = g["natkey"].iloc[0]
            k = 0
            while True:
                uid = "P" + hashlib.sha1(f"{seed}#{k}".encode()).hexdigest()[:7].upper()
                if uid not in used and uid not in claimed:
                    break
                k += 1
        claimed[uid] = cl
        uid_of[cl] = uid
        now = datetime.now()
        new_rows += [(k_, uid, now) for k_ in g["natkey"] if known.get(k_) != uid]
    ps["uid"] = ps["cluster"].map(uid_of)

    # manual links (profile level)
    links = [d for d in _decisions() if d.get("action") == "link"]
    alias = {}
    for d in links:
        us = [u for u in d.get("uids", []) if u in set(ps["uid"])]
        if len(us) >= 2:
            keep = sorted(us)[0]
            for u in us[1:]:
                alias[u] = keep
    if alias:
        ps["uid"] = ps["uid"].map(lambda u: alias.get(u, u))
    if new_rows:
        with db.connect(path) as con:
            con.execute(SQL)
            nr = pd.DataFrame(new_rows, columns=["natkey", "uid", "assigned_at"]).drop_duplicates("natkey")
            con.execute("DELETE FROM identity_registry WHERE natkey IN (SELECT natkey FROM nr)")
            con.execute("INSERT INTO identity_registry SELECT * FROM nr")

    # player season records, same aggregation as the model, keyed by uid
    progress("Building season records")
    x = ps.drop(columns=["pid"]).rename(columns={"uid": "pid"})
    rec = aggregate_player_league_seasons(x).rename(columns={"pid": "uid"})
    teams = db.load_table("team_seasons", path, active_only=True)
    ctx = team_context(teams)
    rec["team_norm"] = rec["team"].map(norm_team)
    if not ctx.empty:
        rec = rec.merge(ctx[["team_norm", "league", "season", "team_attack_idx"]],
                        on=["team_norm", "league", "season"], how="left")
    else:
        rec["team_attack_idx"] = np.nan
    rec["start_share"] = (rec["starts"] / rec["matches"]).where(rec["matches"] > 0)
    rec["rec_key"] = rec["uid"] + "|" + rec["league"] + "|" + rec["season"].astype(str) + "|" + rec["provider"]
    rec["key"] = rec["player_name"] + "|" + rec["team"].astype(str) + "|" + rec["league"] + "|" + rec["season"]
    pid_of = ps.groupby("uid")["pid"].first()
    rec["model_pid"] = rec["uid"].map(pid_of)
    disp = (~ps["_model_row"].astype(bool)).groupby([ps["uid"], ps["league"], ps["season"].astype(str), ps["provider"]]).all()
    rec["display_only"] = [bool(disp.get((u, l, str(s_), p_), False)) for u, l, s_, p_ in
                           zip(rec["uid"], rec["league"], rec["season"], rec["provider"])]

    # stints
    stints = ps.sort_values("minutes", ascending=False).drop_duplicates(
        ["uid", "league", "season", "provider", "team"])[
        ["uid", "provider", "league", "season", "season_start", "team", "minutes", "matches", "position_raw",
         "position_group", "import_id", "current_team", "contract_expires", "export_date", "player_name"]].rename(
        columns={"current_team": "wyscout_team_field"}).sort_values(["uid", "season_start", "minutes"],
                                                                    ascending=[True, True, False])

    # league coverage
    league_latest = rec.sort_values("season_start").groupby("league")["season"].last().to_dict()
    full = rec[~rec["display_only"]]
    league_latest_full = full.sort_values("season_start").groupby("league")["season"].last().to_dict()
    latest_season = rec.sort_values("season_start")["season"].iloc[-1] if not rec.empty else None

    # identity level and flags
    progress("Scoring identities")
    review, players = [], []
    decs = _decisions()
    confirmed_set = {u for d in decs if d.get("action") == "confirm" for u in d.get("uids", [])}
    linked_set = set(alias.values())
    pid_parts = ps.groupby("pid")["uid"].nunique().to_dict()
    cols = ["uid", "cluster", "pid", "mpid", "_model_row", "birth_year", "height_cm", "name_norm", "player_name", "nationality", "season",
            "league", "team", "minutes", "season_start", "position_group", "foot"]
    recs = ps.sort_values(["uid", "season_start", "minutes"], ascending=[True, True, False])[cols].to_dict("records")
    groups = {}
    for r in recs:
        groups.setdefault(r["uid"], []).append(r)

    def mode(vals):
        vals = [v for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))]
        return max(set(vals), key=vals.count) if vals else None

    for uid, g in groups.items():
        flags = []
        pid = g[0]["pid"]
        if pid in clash_pids:
            flags.append(f"Split for profiles: the model linked {pid_parts.get(pid, 2)} players who appear at "
                         "different clubs in the same league season (one export lists a player once)")
        bys = [r["birth_year"] for r in g if pd.notna(r["birth_year"])]
        if len(bys) < len(g) and len(g) > 1:
            flags.append("Birth year missing on some records")
        hs = [r["height_cm"] for r in g if pd.notna(r["height_cm"])]
        if hs and max(hs) - min(hs) > 3:
            flags.append(f"Height differs by {max(hs) - min(hs):.0f} cm")
        if len({r["name_norm"] for r in g}) > 1:
            flags.append("Linked across name spellings: " + ", ".join(sorted({r["player_name"] for r in g})))
        nats = [r["nationality"] for r in g if isinstance(r["nationality"], str) and r["nationality"]]
        if len(set(nats)) > 1:
            flags.append("Nationality differs across records")
        mins = {}
        for r in g:
            mins[(r["season"], r["league"])] = mins.get((r["season"], r["league"]), 0) + (r["minutes"] or 0)
        for s_ in sorted({k[0] for k in mins}):
            if sum(1 for (ss, _), v in mins.items() if ss == s_ and v > 1500) > 1:
                flags.append(f"Over 1,500 minutes in two leagues in {s_}")
        spells = list(dict.fromkeys((r["league"], r["season"], r["team"]) for r in g))
        if len(spells) == 1:
            level = "Single season"
        elif any(f.startswith(("Split", "Over 1,500")) for f in flags):
            level = "Uncertain"
        elif flags:
            level = "Probable"
        else:
            level = "Verified"
        if uid in confirmed_set:
            level = "Confirmed by scout"
        if uid in linked_set:
            flags.append("Profiles joined by a manual link")
        last = max(g, key=lambda r: (r["season_start"], r["minutes"] or 0))
        names = [r["player_name"] for r in g]
        disp = max(set(names), key=lambda n: (len(n.split()[0].replace(".", "")) > 1, names.count(n), len(n)))
        seen_sp = {}
        for r in g:
            seen_sp.setdefault((r["league"], r["season"], r["team"]), r["minutes"] or 0)
        players.append({
            "uid": uid, "display_name": disp, "aliases": sorted(set(names) | {r["name_norm"] for r in g}),
            "birth_year": float(mode(bys)) if bys else np.nan, "nationality": mode(nats),
            "height_cm": float(np.median(hs)) if hs else np.nan, "foot": mode([r["foot"] for r in g]),
            "first_season": g[0]["season"], "last_season": last["season"], "last_season_start": last["season_start"],
            "last_club": last["team"], "last_league": last["league"], "last_position": last["position_group"],
            "last_partial": not bool(last["_model_row"]),
            "n_records": len({(r["league"], r["season"]) for r in g}), "n_seasons": len({r["season"] for r in g}),
            "total_minutes": float(sum(seen_sp.values())), "identity_level": level, "flags": flags,
            "model_pid": next((int(r["mpid"]) for r in g if pd.notna(r["mpid"])), -1), "cluster": g[0]["cluster"], "leagues": sorted({r["league"] for r in g}),
            "clubs": list(dict.fromkeys(r["team"] for r in g)),
        })
        if level in ("Uncertain", "Probable"):
            review.append({"uid": uid, "player": disp, "level": level, "flags": "; ".join(flags),
                           "seasons": ", ".join(f"{LEAGUE_SHORT.get(lg, lg)} {ss} ({tm})" for lg, ss, tm in spells)})
    players = pd.DataFrame(players)

    # recency
    overall_year = int(str(latest_season)[:4]) if latest_season else None
    def recency(r):
        lg = r["last_league"]
        lg_latest, lg_full = league_latest.get(lg), league_latest_full.get(lg)
        if r["last_season"] == lg_latest and str(lg_latest) == str(latest_season):
            return "In the latest data"
        if r["last_season"] == lg_latest:
            return f"{lg} {latest_season} not imported"
        if lg_full is not None and r["last_season"] == lg_full and str(lg_latest) != str(lg_full):
            return f"Not in the partial {lg} {lg_latest} export"
        return f"Not in later {lg} data"
    players["recency"] = players.apply(recency, axis=1)
    # recent enough for the current recruitment pool: in his league's latest season, his league's next season not
    # imported, or missing only from a partial export (absence there proves nothing)
    players["recent"] = players["recency"].eq("In the latest data") | players["recency"].str.endswith("not imported") \
        | players["recency"].str.startswith("Not in the partial")
    players["seasons_since"] = players["last_season"].map(lambda s: overall_year - int(str(s)[:4])
                                                          if overall_year else np.nan)
    players["is_rifc_now"] = (players["last_league"] == USLC) & players["last_club"].fillna("").str.lower().str.contains(
        "rhode island") & (players["last_season"] == latest_season)

    # same name kept separate
    sim = []
    pl = players.set_index("uid")
    nn = ps.drop_duplicates(["uid"])[["uid", "name_norm"]]
    for name, gg in ps.groupby("name_norm"):
        us = gg["uid"].unique()
        if len(us) < 2:
            continue
        for i in range(len(us)):
            for j in range(i):
                a, b = pl.loc[us[i]], pl.loc[us[j]]
                ga, gb = ps[ps["uid"] == us[i]], ps[ps["uid"] == us[j]]
                reasons = []
                ya, yb = a["birth_year"], b["birth_year"]
                if pd.notna(ya) and pd.notna(yb) and abs(ya - yb) > 1:
                    reasons.append(f"birth years {int(ya)} and {int(yb)}")
                if a["nationality"] and b["nationality"] and a["nationality"] != b["nationality"]:
                    reasons.append(f"nationality {a['nationality']} and {b['nationality']}")
                if pd.notna(a["height_cm"]) and pd.notna(b["height_cm"]) and abs(a["height_cm"] - b["height_cm"]) > 7:
                    reasons.append(f"height {a['height_cm']:.0f} and {b['height_cm']:.0f} cm")
                both = ga.merge(gb, on=["league", "season"])
                if not both.empty:
                    reasons.append("both appear in the same league season")
                if a["model_pid"] == b["model_pid"]:
                    reasons.append("split for profiles (two clubs in one league season)")
                close = (pd.notna(ya) and pd.notna(yb) and abs(ya - yb) <= 1) and not reasons
                sim.append({"name": a["display_name"], "uid_a": us[i], "uid_b": us[j],
                            "a": f"{a['last_club']}, {a['last_league']} {a['last_season']} (born {ya:.0f})"
                            if pd.notna(ya) else f"{a['last_club']}, {a['last_league']} {a['last_season']}",
                            "b": f"{b['last_club']}, {b['last_league']} {b['last_season']} (born {yb:.0f})"
                            if pd.notna(yb) else f"{b['last_club']}, {b['last_league']} {b['last_season']}",
                            "kept_apart_because": "; ".join(reasons) or "the model's guards kept them apart "
                                                                     "(no single deciding field)",
                            "needs_review": bool(close)})
    similar = pd.DataFrame(sim)

    # audit
    old_entries = len(eng.agg[eng.agg["position_group"].notna() & (eng.agg["position_group"] != "GK")]) \
        if eng is not None else len(rec)
    outfield = rec[rec["position_group"].notna() & (rec["position_group"] != "GK")]
    audit = {
        "built_at": datetime.now().isoformat(timespec="seconds"), "same_links_as_model": same_as_model,
        "season_rows": int(len(ps)), "duplicate_profile_rows_ignored": int(len(dup_report)),
        "model_identities": int(ps["mpid"].nunique()), "player_ids": int(ps["uid"].nunique()),
        "search_entries_before": int(old_entries),
        "players_outfield": int(outfield["uid"].nunique()),
        "season_entries_folded": int(old_entries - outfield["uid"].nunique()),
        "multi_season_players": int((players["n_records"] > 1).sum()),
        "clash_identities": len(clash_pids), "clash_extra_profiles": int(ps[ps["pid"].isin(clash_pids)]["uid"].nunique()
                                                                        - len(clash_pids)),
        "levels": players["identity_level"].value_counts().to_dict(),
        "review_open": len(review),
        "multi_club_same_season": int((stints.groupby(["uid", "league", "season"])["team"].nunique() > 1).sum()),
        "multi_league_same_season": int((stints.groupby(["uid", "season"])["league"].nunique() > 1).sum()),
        "same_name_separate": int(len(similar)), "same_name_close_age": int(similar["needs_review"].sum())
        if not similar.empty else 0,
        "near_duplicate_rows_hidden": len(near_dropped), "manual_links": len(alias), "display_only_rows": int((~ps["_model_row"].astype(bool)).sum()),
        "model_identities_joined_by_display_rows": bridged,
    }
    if eng is not None and not eng.pairs.empty:
        c = eng.pairs[eng.pairs["pair_type"] == "cross"]
        touch = eng.pairs[eng.pairs["pid"].isin(set(ps.loc[ps["pid"].isin(clash_pids), "mpid"].dropna()))]
        audit.update({"model_cross_pairs": int(len(c)), "model_within_pairs": int((eng.pairs["pair_type"] == "within").sum()),
                      "pairs_touching_split_identities": int(len(touch)),
                      "cross_pairs_touching_split_identities": int((touch["pair_type"] == "cross").sum())})
    out = Identity(ps, rec, players, stints, pd.DataFrame(review), similar, audit, league_latest, latest_season)
    _write_tables(out, path)
    return out


def _write_tables(idn: Identity, path=None):
    """Plain tables for other tools (and the club database later)."""
    p = idn.players.copy()
    p["aliases"] = p["aliases"].map(json.dumps)
    p["flags"] = p["flags"].map(json.dumps)
    pi = p[["uid", "display_name", "aliases", "birth_year", "nationality", "height_cm", "first_season", "last_season",
            "last_club", "last_league", "n_records", "identity_level", "flags", "model_pid"]]
    s = idn.stints[["uid", "provider", "league", "season", "team", "minutes", "matches", "position_raw", "import_id",
                    "wyscout_team_field", "contract_expires", "export_date"]]
    with db.connect(path) as con:
        con.execute(SQL)
        con.execute("DELETE FROM player_identity")
        con.execute("INSERT INTO player_identity SELECT * FROM pi")
        con.execute("DELETE FROM player_stints")
        con.execute("INSERT INTO player_stints SELECT * FROM s")


def cache_file(eng) -> str:
    dec = DECISIONS.stat().st_mtime if DECISIONS.exists() else 0
    try:
        disp = db.query("SELECT import_id FROM imports WHERE COALESCE(display_only, FALSE) ORDER BY import_id")
        disp = ",".join(map(str, disp["import_id"]))
    except Exception:
        disp = ""
    h = hashlib.sha1(f"{eng.fingerprint}|{eng.run_id}|{dec}|{disp}|{VERSION}".encode()).hexdigest()[:12]
    return str(MODEL_DIR / f"identity_{h}.pkl")


def load_or_build(eng, progress=lambda m: None) -> Identity:
    from pathlib import Path
    f = Path(cache_file(eng))
    if f.exists():
        try:
            return pickle.loads(f.read_bytes())
        except Exception:
            pass
    for old in MODEL_DIR.glob("identity_*.pkl"):
        old.unlink(missing_ok=True)
    idn = build(eng, progress=progress)
    f.write_bytes(pickle.dumps(idn))
    return idn


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def latest_eligible(idn: Identity, min_minutes: float = 0, outfield: bool = True) -> pd.DataFrame:
    """Each player's own latest record with at least min_minutes (never the league's latest season by default).
    Ties in one season: the record with most minutes."""
    r = idn.records
    if outfield:
        r = r[r["position_group"].notna() & (r["position_group"] != "GK")]
    r = r[r["minutes"] >= min_minutes]
    return r.sort_values(["season_start", "minutes"], ascending=[False, False]).drop_duplicates("uid")


def records_of(idn: Identity, uid: str) -> pd.DataFrame:
    return idn.records[idn.records["uid"] == uid].sort_values(["season_start", "minutes"], ascending=[False, False])


def fold(s) -> str:
    return re.sub(r"\s+", " ", strip_accents(str(s or "")).lower().replace(".", " ")).strip()


def search(idn: Identity, q: str, limit: int = 12) -> pd.DataFrame:
    """One row per player. Ranked: exact name, name starts with, a name word starts with, alternate name,
    club, league. Unrelated names never fill the list: no match, no result."""
    p = idn.players
    q = fold(q)
    if not q:
        return p.iloc[0:0]
    toks = q.split()
    rows = []
    for r in p.itertuples():
        names = [fold(r.display_name)] + [fold(a) for a in r.aliases]
        words = {w for n in names for w in n.split()}
        score = 0
        if q in names:
            score = 100
        elif any(n.startswith(q) for n in names):
            score = 80
        elif all(any(w.startswith(t) for w in words) for t in toks):
            score = 70
        elif any(q in n for n in names):
            score = 55
        else:
            clubs = " ".join(fold(c) for c in r.clubs)
            lgs = " ".join(fold(x) + " " + fold(LEAGUE_SHORT.get(x, "")) for x in r.leagues)
            if all(t in clubs for t in toks):
                score = 40
            elif all(t in lgs or t in clubs for t in toks):
                score = 25
        if score:
            rows.append((score, r.last_season_start, r.total_minutes, r.uid))
    if not rows:
        return p.iloc[0:0]
    rows.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    top = rows[0][0]
    keep = [u for s, _, _, u in rows if s >= min(top, 55) or top < 55][:limit]
    return p.set_index("uid").loc[keep].reset_index()


def label(pr) -> str:
    seasons = f"{pr['first_season']} to {pr['last_season']}" if pr["first_season"] != pr["last_season"] else pr["last_season"]
    part = " | partial dataset" if pr.get("last_partial") else ""
    return (f"{pr['display_name']} | {pr['last_club']} | {pr['last_league']} | {seasons} ({pr['n_records']} "
            f"record{'s' if pr['n_records'] != 1 else ''}){part}")


# ---------------------------------------------------------------------------
# Current club verification (manual, append only; history is never overwritten)
# ---------------------------------------------------------------------------
def verifications(uid: str | None = None, path=None) -> pd.DataFrame:
    with db.connect(path) as con:
        con.execute(SQL)
        q = "SELECT * FROM club_verifications" + (" WHERE uid = ?" if uid else "") + " ORDER BY entered_at DESC"
        return con.execute(q, [uid] if uid else []).df()


def add_verification(uid, status, current_club, verified_on, source, contract_expiry="", contract_as_of=None,
                     note="", path=None):
    with db.connect(path) as con:
        con.execute(SQL)
        nid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM club_verifications").fetchone()[0]
        con.execute("INSERT INTO club_verifications VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [nid, uid, status, current_club, verified_on, source, contract_expiry or None, contract_as_of,
                     note, datetime.now()])


def club_status(idn: Identity, uid: str, path=None, ver: pd.DataFrame | None = None) -> dict:
    """Last recorded club (from data) and current club (only from a manual verification)."""
    p = idn.players.set_index("uid").loc[uid]
    v = (ver[ver["uid"] == uid] if ver is not None else verifications(uid, path))
    st = idn.stints[idn.stints["uid"] == uid].sort_values("season_start")
    last = st[st["season"] == p["last_season"]]
    wy = last["wyscout_team_field"].dropna()
    ce = last["contract_expires"].dropna()
    out = {"last_club": p["last_club"], "last_season": p["last_season"], "last_league": p["last_league"],
           "recency": p["recency"], "verified": False, "status": "Current club unverified",
           "current_club": None, "verified_on": None, "source": None,
           "contract_expiry": ce.iloc[0] if len(ce) else None,
           "contract_source": f"Wyscout export of {last['export_date'].iloc[0]}" if len(ce) else None,
           "wyscout_team_field": wy.iloc[0] if len(wy) else None,
           "wyscout_date": last["export_date"].iloc[0] if len(last) else None}
    if v is not None and not v.empty:
        x = v.iloc[0]
        out.update({"verified": True, "status": x["status"], "current_club": x["current_club"],
                    "verified_on": str(x["verified_on"])[:10] if pd.notna(x["verified_on"]) else None,
                    "source": x["source"]})
        if x["contract_expiry"]:
            out["contract_expiry"] = x["contract_expiry"]
            out["contract_source"] = f"Manual, as of {str(x['contract_as_of'])[:10]}" if pd.notna(x["contract_as_of"]) \
                else "Manual"
    return out


def verified_uids(path=None) -> set:
    v = verifications(path=path)
    return set(v["uid"]) if not v.empty else set()


def split_rule_for_model(idn: Identity, model_pid: int, reason: str) -> dict:
    """A do_not_link rule (data/identity_overrides.json format) separating the profile level parts of one model
    identity. The model uses it after the next refit."""
    g = idn.rows[idn.rows["mpid"] == model_pid]
    groups = []
    for _, h in g.groupby("uid"):
        groups.append([{"team": r.team, "league": r.league, "season": str(r.season)}
                       for r in h.drop_duplicates(["team", "league", "season"]).itertuples()])
    return {"player": g["player_name"].iloc[0], "reason": reason, "groups": groups}


def apply_split_to_model(idn: Identity, model_pid: int, reason: str) -> bool:
    f = DATA_DIR / "identity_overrides.json"
    d = json.loads(f.read_text()) if f.exists() else {"do_not_link": []}
    rule = split_rule_for_model(idn, model_pid, reason)
    for r in d.get("do_not_link", []):
        if r.get("player") == rule["player"] and r.get("groups") == rule["groups"]:
            return False
    d.setdefault("do_not_link", []).append(rule)
    f.write_text(json.dumps(d, indent=1, ensure_ascii=False))
    return True


def model_rules() -> list:
    f = DATA_DIR / "identity_overrides.json"
    try:
        return json.loads(f.read_text()).get("do_not_link", [])
    except Exception:
        return []
