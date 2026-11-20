"""Phase 2a: link the same player across imports and build before/after pairs.

A pair is one player's season in league A followed by his next season in league
B (B may equal A when within league pairs are enabled). Pairs are built only
from imported data; nothing is assumed or filled in.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from . import db
from .config import ModelSettings
from .normalize import is_abbreviated, norm_team, short_name
from .schema import METRICS, SUPPORT_FIELDS

RATE_COLS = [m for m in list(METRICS) + SUPPORT_FIELDS if (m not in METRICS or METRICS[m].kind == "rate")]
PCT_COLS = [m for m in METRICS if METRICS[m].kind == "pct"]


class _UF:
    """Union find with guards. Two clusters are never merged when:
    * they hold rows from the same file and team (one export never lists the
      same player twice for one club, so those are different players);
    * both have a known birth country and the countries differ;
    * the merged cluster would span more than one estimated birth year;
    * both have a recorded height and the merged cluster would span more than
      HEIGHT_TOLERANCE_CM (Wyscout heights for one player vary by up to 5 cm
      between files; two different players with one name differ by more);
    * a do-not-link rule in data/identity_overrides.json separates them.
    """

    def __init__(self, items, slot=None, nat=None, year=None, height=None, label=None):
        self.p = {i: i for i in items}
        self.slots = {i: {slot[i]} if slot else set() for i in items}
        self.nats = {i: ({nat[i]} if nat and isinstance(nat.get(i), str) else set()) for i in items}
        self.yr = {i: ((year[i], year[i]) if year and pd.notna(year.get(i)) else None) for i in items}
        self.ht = {i: ((height[i], height[i]) if height and pd.notna(height.get(i)) else None) for i in items}
        self.lab = {i: ({label[i]} if label and label.get(i) else set()) for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def can_union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.slots[ra] & self.slots[rb]:
            return False
        if self.nats[ra] and self.nats[rb] and not (self.nats[ra] & self.nats[rb]):
            return False
        ya, yb = self.yr[ra], self.yr[rb]
        if ya and yb and max(ya[1], yb[1]) - min(ya[0], yb[0]) > 1:
            return False
        ha, hb = self.ht[ra], self.ht[rb]
        if ha and hb and max(ha[1], hb[1]) - min(ha[0], hb[0]) > HEIGHT_TOLERANCE_CM:
            return False
        if self.lab[ra] and self.lab[rb] and self.lab[ra] != self.lab[rb]:
            return False
        return True

    def union(self, a, b):
        if not self.can_union(a, b):
            return False
        ra, rb = self.find(a), self.find(b)
        keep, drop = min(ra, rb), max(ra, rb)
        self.p[drop] = keep
        self.slots[keep] |= self.slots.pop(drop)
        self.nats[keep] |= self.nats.pop(drop)
        ya, yb = self.yr[keep], self.yr.pop(drop)
        self.yr[keep] = (min(ya[0], yb[0]), max(ya[1], yb[1])) if ya and yb else (ya or yb)
        ha, hb = self.ht[keep], self.ht.pop(drop)
        self.ht[keep] = (min(ha[0], hb[0]), max(ha[1], hb[1])) if ha and hb else (ha or hb)
        self.lab[keep] |= self.lab.pop(drop)
        return True


HEIGHT_TOLERANCE_CM = 7


def load_overrides():
    """Manual identity rules (data/identity_overrides.json). Each 'do_not_link' rule lists groups of
    season rows (player name, team, league, season) that are different people and must never share
    an identity. Rows are matched by normalised name, team, league and season."""
    import json
    from .config import DATA_DIR
    f = DATA_DIR / "identity_overrides.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text()).get("do_not_link", [])
    except Exception:
        return []


def override_labels(ps: pd.DataFrame) -> dict:
    """row_id -> label; rows in different groups of one rule get different labels."""
    from .normalize import norm_name
    out = {}
    for k, rule in enumerate(load_overrides()):
        nm = norm_name(rule.get("player"))
        for j, grp in enumerate(rule.get("groups", [])):
            for spell in grp:
                m = ((ps["name_norm"] == nm) & (ps["team"] == spell.get("team")) &
                     (ps["league"] == spell.get("league")) & (ps["season"].astype(str) == str(spell.get("season"))))
                for rid in ps.loc[m, "row_id"]:
                    out[rid] = f"rule{k}_group{j}"
    return out


STAT_KEY = ["minutes", "matches", "goals_total", "assists_total", "xg_total", "shots_total"]


def drop_duplicate_profiles(ps: pd.DataFrame):
    """Wyscout sometimes carries the same season stats under two player profiles
    (different age or birth country, identical minutes and numbers). Keep one copy:
    the profile that links to other seasons, else the more complete one."""
    key = ["import_id", "team", "name_norm"] + [c for c in STAT_KEY if c in ps]
    ps = ps.copy()
    dup = ps.duplicated(subset=key, keep=False) & ps["minutes"].notna()
    if not dup.any():
        return ps, pd.DataFrame()
    seen = ps.groupby("name_norm")[["import_id", "birth_year"]].apply(lambda g: list(zip(g.import_id, g.birth_year)))

    def links(r):
        return sum(1 for iid, by in seen.get(r["name_norm"], []) if iid != r["import_id"]
                   and pd.notna(by) and pd.notna(r["birth_year"]) and abs(by - r["birth_year"]) <= 1)

    d = ps[dup].copy()
    d["_links"] = d.apply(links, axis=1)
    d["_complete"] = (d["height_cm"].notna().astype(int) + d["age"].notna().astype(int)
                      + (d["foot"].fillna("unknown").str.lower() != "unknown").astype(int))
    # agreement with the same name's other seasons (outside the duplicated rows): height within 2 cm
    others = ps[~dup & ps["height_cm"].notna()].groupby("name_norm")["height_cm"].apply(list)

    def agrees(r):
        hs = others.get(r["name_norm"], [])
        return int(pd.notna(r["height_cm"]) and any(abs(h - r["height_cm"]) <= 2 for h in hs))

    d["_agree"] = d.apply(agrees, axis=1)
    d = d.sort_values(["_agree", "_links", "_complete"], ascending=False)
    keep_ids = set(d.drop_duplicates(subset=key, keep="first")["row_id"])
    dropped = d[~d["row_id"].isin(keep_ids)]
    report = dropped[["import_id", "player_name", "team", "league", "season", "age", "nationality", "minutes"]].assign(
        issue="Exact duplicate of another profile's season stats; this copy is ignored.")
    return ps[~ps["row_id"].isin(dropped["row_id"])], report


def resolve_identities(ps: pd.DataFrame, transfers: pd.DataFrame | None = None):
    """Assign a 'pid' to every player season row. Returns (frame, review table)."""
    ps = ps.copy()
    ps["short"] = ps["name_norm"].map(short_name)
    ps["abbr"] = ps["name_norm"].map(is_abbreviated)
    slot = dict(zip(ps["row_id"], zip(ps["import_id"], ps["team"].fillna("?"))))
    nat = dict(zip(ps["row_id"], ps["nationality"].map(lambda v: str(v).split(",")[0].strip().lower()
                                                        if isinstance(v, str) and v.strip() else None)))
    uf = _UF(ps["row_id"].tolist(), slot, nat, dict(zip(ps["row_id"], ps["birth_year"])),
             dict(zip(ps["row_id"], ps["height_cm"])), override_labels(ps))

    # 1) provider IDs
    has_id = ps["provider_player_id"].notna()
    for _, g in ps[has_id].groupby(["provider", "provider_player_id"]):
        ids = g["row_id"].tolist()
        for r in ids[1:]:
            uf.union(ids[0], r)

    # 2) same name, birth years within one year (guards in _UF stop bad merges)
    for _, g in ps[ps["name_norm"].notna()].groupby("name_norm"):
        if len(g) < 2:
            continue
        g = g.sort_values(["birth_year", "season_start"])
        known = g[g["birth_year"].notna()][["row_id", "birth_year"]].values.tolist()
        for i in range(len(known)):
            for j in range(i):
                if known[i][1] - known[j][1] <= 1:
                    uf.union(known[j][0], known[i][0])
        unknown = g[g["birth_year"].isna()]["row_id"].tolist()
        clusters = {uf.find(r) for r, _ in known}
        if unknown and len(clusters) <= 1:     # only when the name is unambiguous
            anchor = known[0][0] if known else unknown[0]
            for r in unknown:
                uf.union(anchor, r)

    # 3) abbreviated names ('J. Smith') to a unique full name with the same birth year
    full = ps[~ps["abbr"] & ps["birth_year"].notna() & ps["short"].notna()]
    full_idx = defaultdict(set)
    for r in full.itertuples():
        full_idx[(r.short, int(r.birth_year))].add(uf.find(r.row_id))
    for r in ps[ps["abbr"] & ps["birth_year"].notna()].itertuples():
        cands = set()
        for dy in (0,):
            cands |= full_idx.get((r.short, int(r.birth_year) + dy), set())
        if len(cands) == 1:
            uf.union(r.row_id, next(iter(cands)))

    # 4) explicit transfer list
    if transfers is not None and not transfers.empty:
        for t in transfers.itertuples():
            def find_rows(league, season):
                m = (ps["league"] == league) & (ps["season"] == season)
                hit = ps[m & (ps["name_norm"] == t.name_norm)]
                if hit.empty and t.name_norm:
                    hit = ps[m & (ps["short"] == short_name(t.name_norm))]
                return hit
            a = find_rows(t.from_league, t.from_season) if t.from_season else pd.DataFrame()
            b = find_rows(t.to_league, t.to_season)
            if len(a) and len(b) and a["name_norm"].nunique() == 1 and b["name_norm"].nunique() == 1:
                uf.union(a["row_id"].iloc[0], b["row_id"].iloc[0])
                for r in list(a["row_id"]) + list(b["row_id"]):
                    uf.union(a["row_id"].iloc[0], r)

    ps["pid"] = ps["row_id"].map(uf.find)

    # Review: clusters with heavy minutes in two different leagues in the same season
    review = []
    for pid, g in ps.groupby("pid"):
        by_season = g.groupby(["season", "provider"])
        for (season, prov), s in by_season:
            if s["league"].nunique() > 1 and (s.groupby("league")["minutes"].sum() > 1500).sum() > 1:
                review.append({"pid": pid, "player": s["player_name"].iloc[0], "season": season,
                               "leagues": ", ".join(sorted(s["league"].unique())),
                               "issue": "Over 1,500 minutes in two leagues in one season. Possibly two "
                                        "different players merged."})
    return ps.drop(columns=["short", "abbr"]), pd.DataFrame(review)


def aggregate_player_league_seasons(ps: pd.DataFrame) -> pd.DataFrame:
    """Combine rows for the same player, league, season and provider (mid season moves)."""
    ps = ps[ps["minutes"].fillna(0) > 0].copy()
    keys = ["pid", "league", "season", "provider"]
    # Same player, same club, same season in two files (overlapping exports) is one spell, not two:
    # keep the fuller row. Different clubs in one season are real mid season moves and are summed.
    ps = ps.sort_values("minutes", ascending=False).drop_duplicates(keys + ["team"], keep="first")
    for m in RATE_COLS:
        ps[f"{m}_total"] = ps[f"{m}_total"].where(ps[f"{m}_total"].notna(), ps[f"{m}_p90"] * ps["minutes"] / 90)
    agg = {"minutes": "sum", "matches": "sum", "starts": lambda s: s.sum(min_count=1),
           "season_start": "first", "season_end": "first", "player_name": "first",
           "birth_year": "first", "age_at_season": "mean", "nationality": "first"}
    for m in RATE_COLS:
        agg[f"{m}_total"] = lambda s: s.sum(min_count=1)
    g = ps.groupby(keys, dropna=False)
    out = g.agg(agg)
    # team, position = from the row with most minutes
    top = ps.sort_values("minutes", ascending=False).drop_duplicates(keys).set_index(keys)
    out["team"] = top["team"]
    out["position_group"] = top["position_group"]
    out["position_raw"] = top["position_raw"]
    for c in ("current_team", "contract_expires"):
        if c in top:
            out[c] = top[c]
    for m in RATE_COLS:
        out[f"{m}_p90"] = out[f"{m}_total"] / out["minutes"] * 90
    for m in PCT_COLS:
        w = ps[f"{m}_p90"].notna() * ps["minutes"]
        ps["_w"] = w
        ps["_wv"] = ps[f"{m}_p90"].fillna(0) * w
        s = ps.groupby(keys, dropna=False)[["_w", "_wv"]].sum()
        out[f"{m}_p90"] = (s["_wv"] / s["_w"]).where(s["_w"] > 0)
    out["n_rows"] = g.size()
    return out.reset_index()


def team_context(teams: pd.DataFrame) -> pd.DataFrame:
    """Relative attacking index per team season: log(team rate / league average rate).

    Uses xG for when the whole league season has it, otherwise goals for. It is
    a within league measure, so provider definitions largely cancel out.
    """
    if teams is None or teams.empty:
        return pd.DataFrame(columns=["team_norm", "league", "season", "team_attack_idx", "attack_basis",
                                     "pens_per_match"])
    t = teams.copy()
    t["team_norm"] = t["team"].map(norm_team)
    t = t[t["matches"] > 0]
    rows = []
    for (lg, ss), g in t.groupby(["league", "season"]):
        basis = "xG" if g["xg_for"].notna().all() else ("goals" if g["goals_for"].notna().all() else None)
        if basis is None:
            continue
        col = "xg_for" if basis == "xG" else "goals_for"
        rate = g[col] / g["matches"]
        lg_rate = g[col].sum() / g["matches"].sum()
        idx = np.log(rate.clip(lower=0.05) / lg_rate)
        pens = g["penalties_awarded"] / g["matches"]
        for (_, r), i, p in zip(g.iterrows(), idx, pens):
            rows.append({"team_norm": r["team_norm"], "team": r["team"], "league": lg, "season": ss,
                         "team_attack_idx": float(i), "attack_basis": basis, "pens_per_match": p,
                         "possession_pct": r["possession_pct"]})
    return pd.DataFrame(rows)


def build_pairs(settings: ModelSettings, db_path=None, ps: pd.DataFrame | None = None,
                teams: pd.DataFrame | None = None, transfers: pd.DataFrame | None = None):
    """Return (pairs, aggregated player seasons, identity review table)."""
    ps = db.load_table("player_seasons", db_path, active_only=True) if ps is None else ps
    teams = db.load_table("team_seasons", db_path, active_only=True) if teams is None else teams
    transfers = db.load_table("transfers_ref", db_path, active_only=True) if transfers is None else transfers
    if ps.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    ps = ps[ps["name_norm"].notna() & ps["season_start"].notna() & ps["league"].notna()]
    ps, dup_report = drop_duplicate_profiles(ps)
    ps, review = resolve_identities(ps, transfers)
    if not dup_report.empty:
        review = pd.concat([review, dup_report], ignore_index=True)
    agg = aggregate_player_league_seasons(ps)
    ctx = team_context(teams)
    agg["team_norm"] = agg["team"].map(norm_team)
    if not ctx.empty:
        agg = agg.merge(ctx[["team_norm", "league", "season", "team_attack_idx"]],
                        on=["team_norm", "league", "season"], how="left")
    else:
        agg["team_attack_idx"] = np.nan
    agg["start_share"] = (agg["starts"] / agg["matches"]).where(agg["matches"] > 0)

    eligible = agg[(agg["minutes"] >= settings.min_minutes_each_side) & agg["position_group"].notna()
                   & (agg["position_group"] != "GK")]
    rows = []
    for pid, g in eligible.groupby("pid"):
        if len(g) < 2:
            continue
        g = g.sort_values("season_start")
        recs = g.to_dict("records")
        for s in recs:
            later = [d for d in recs if d["season_start"] > s["season_start"] + 0.05
                     and -0.6 <= d["season_start"] - s["season_end"] <= settings.max_gap_years]
            if not later:
                continue
            first = min(d["season_start"] for d in later)
            for d in later:
                if d["season_start"] - first > 0.3:
                    continue
                cross = d["league"] != s["league"]
                if not cross and not settings.include_within_league_pairs:
                    continue
                if not cross and d["provider"] != s["provider"]:
                    continue
                row = {"pid": pid, "player_name": s["player_name"], "pair_type": "cross" if cross else "within",
                       "src_league": s["league"], "dst_league": d["league"],
                       "src_season": s["season"], "dst_season": d["season"],
                       "src_season_start": s["season_start"], "dst_season_start": d["season_start"],
                       "src_season_end": s["season_end"], "dst_season_end": d["season_end"],
                       "src_team": s["team"], "dst_team": d["team"],
                       "src_provider": s["provider"], "dst_provider": d["provider"],
                       "position_group": s["position_group"], "dst_position_group": d["position_group"],
                       "src_minutes": s["minutes"], "dst_minutes": d["minutes"],
                       "age_dst": (s["age_at_season"] + (d["season_start"] - s["season_start"]))
                       if pd.notna(s["age_at_season"]) else np.nan,
                       "src_team_idx": s["team_attack_idx"], "dst_team_idx": d["team_attack_idx"],
                       "src_start_share": s["start_share"], "dst_start_share": d["start_share"]}
                for m in METRICS:
                    row[f"src_{m}"] = s[f"{m}_p90"]
                    row[f"dst_{m}"] = d[f"{m}_p90"]
                rows.append(row)
    pairs = pd.DataFrame(rows)
    if not pairs.empty:
        pairs["position_changed"] = pairs["position_group"] != pairs["dst_position_group"]
    return pairs, agg, review


def metric_pairs(pairs: pd.DataFrame, metric: str, settings: ModelSettings) -> pd.DataFrame:
    """Pairs usable for one metric: both sides present and definitions comparable."""
    if pairs.empty:
        return pairs
    p = pairs[pairs[f"src_{metric}"].notna() & pairs[f"dst_{metric}"].notna()]
    same_provider = p["src_provider"] == p["dst_provider"]
    if METRICS[metric].definition_stable and settings.allow_cross_provider_for_stable_metrics:
        return p.copy()
    return p[same_provider].copy()


def coverage_table(pairs: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty:
        return pd.DataFrame()
    cross = pairs[pairs["pair_type"] == "cross"]
    return (cross.groupby(["src_league", "dst_league"]).size().rename("pairs").reset_index()
            .sort_values("pairs", ascending=False))
