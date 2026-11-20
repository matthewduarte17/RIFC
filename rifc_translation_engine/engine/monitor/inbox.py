"""The recruitment inbox: reading events with their evidence, your decisions, match answers, shortlist and CSV.

Your decisions live in rm_events (status, note, user_decided) and are mirrored in rm_event_decisions, keyed by the
event's stable fingerprint, so a scan never overwrites them and a rebuilt event gets them back.
"""
from __future__ import annotations

import io
import json
from datetime import datetime

import pandas as pd

from .. import recruit as R
from . import store
from .classify import AVAILABILITY_CLOSING, AVAILABILITY_OPENING, EVENT_TYPES
from .match import name_key
from .profile import Ctx, inference_for, player_card, review_priority

STATUSES = store.STATUSES


def load_events(path=None) -> pd.DataFrame:
    ev = store.query("SELECT * FROM rm_events", path=path)
    if ev.empty:
        ev["sources"] = []
        return ev
    evd = store.query("SELECT v.event_id, v.quote, v.seen_at, i.item_id, i.title, i.url, i.source_id, i.published_at, i.body_status, "
                      "i.discovered_at FROM rm_event_evidence v JOIN rm_items i USING (item_id) ORDER BY i.published_at", path=path)
    src = {}
    for r in evd.itertuples():
        src.setdefault(int(r.event_id), []).append({
            "title": r.title, "url": r.url, "source": r.source_id, "published": str(r.published_at)[:10] if pd.notna(r.published_at) else "",
            "discovered": str(r.discovered_at)[:16], "quote": r.quote, "body_status": r.body_status})
    ev["sources"] = ev["event_id"].map(lambda i: src.get(int(i), []))
    ev["type_label"] = ev["event_type"].map(lambda t: EVENT_TYPES.get(t, t))
    return ev


def set_status(event_id: int, status: str, note: str | None = None, path=None):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    now = datetime.now()
    with store.conn(path) as con:
        r = con.execute("SELECT fingerprint, user_note FROM rm_events WHERE event_id = ?", [int(event_id)]).fetchone()
        if r is None:
            raise KeyError(event_id)
        note = r[1] if note is None else note
        con.execute("UPDATE rm_events SET inbox_status=?, status_changed_at=?, user_decided=TRUE, user_note=?, updated_flag=FALSE WHERE event_id=?",
                    [status, now, note or "", int(event_id)])
        con.execute("DELETE FROM rm_event_decisions WHERE fingerprint = ?", [r[0]])
        con.execute("INSERT INTO rm_event_decisions VALUES (?,?,?,?)", [r[0], status, note or "", now])


def acknowledge_update(event_id: int, path=None):
    with store.conn(path) as con:
        con.execute("UPDATE rm_events SET updated_flag=FALSE WHERE event_id=?", [int(event_id)])


def confirm_match(event_id: int, uid: str, path=None, note: str = ""):
    """You say: this name at this club is this player. Remembered for every later scan and applied to waiting events."""
    now = datetime.now()
    with store.conn(path) as con:
        e = con.execute("SELECT name_norm, club_norm FROM rm_events WHERE event_id = ?", [int(event_id)]).fetchone()
        con.execute("DELETE FROM rm_match_decisions WHERE name_norm=? AND club_norm=? AND uid=?", [e[0], e[1] or "", uid])
        con.execute("INSERT INTO rm_match_decisions VALUES (?,?,?,?,?,?)", [e[0], e[1] or "", uid, "same", now, note])
        con.execute("UPDATE rm_events SET uid=?, match_status='matched', match_grade='Confirmed by you' WHERE event_id=?", [uid, int(event_id)])
        con.execute("UPDATE rm_events SET uid=?, match_status='matched', match_grade='Confirmed by you' "
                    "WHERE name_norm=? AND COALESCE(club_norm,'')=? AND match_status IN ('review','unmatched') AND NOT user_decided",
                    [uid, e[0], e[1] or ""])


def reject_match(event_id: int, uid: str | None = None, path=None, note: str = ""):
    """You say: this is NOT that player (or, with no uid, none of the candidates). The event becomes a news lead."""
    now = datetime.now()
    with store.conn(path) as con:
        e = con.execute("SELECT name_norm, club_norm, uid, candidates_json FROM rm_events WHERE event_id = ?", [int(event_id)]).fetchone()
        uids = [uid] if uid else [c["uid"] for c in json.loads(e[3] or "[]")] + ([e[2]] if e[2] else [])
        for u in set(uids):
            con.execute("DELETE FROM rm_match_decisions WHERE name_norm=? AND club_norm=? AND uid=?", [e[0], e[1] or "", u])
            con.execute("INSERT INTO rm_match_decisions VALUES (?,?,?,?,?,?)", [e[0], e[1] or "", u, "different", now, note])
        if e[2] in uids:
            con.execute("UPDATE rm_events SET uid=NULL, match_status='unmatched', match_grade=NULL, match_reasons=? WHERE event_id=?",
                        [json.dumps(["You said this is not the candidate we linked."]), int(event_id)])
        elif uid is None:
            con.execute("UPDATE rm_events SET match_status='unmatched', match_grade=NULL, match_reasons=? WHERE event_id=?",
                        [json.dumps(["You said none of the candidates is this player: kept as a news lead."]), int(event_id)])


def add_to_shortlist(ctx: Ctx, uid: str, role: str | None = None, note: str = "", path=None) -> str:
    """Uses the existing shortlist workflow (engine.recruit.shortlist_add): same table, same role names."""
    pg = ctx.idn.players.set_index("uid").loc[uid, "last_position"] if ctx.ready else None
    role = role or R.DEFAULT_ROLE.get(pg, list(R.ROLES)[0])
    R.shortlist_add(uid, role, note)
    return role


def enrich(ev: pd.DataFrame, ctx: Ctx) -> pd.DataFrame:
    """Adds player card fields, review priority and data freshness. Computed on demand, never stored, so it always
    reflects the shortlist and data as they are now."""
    if ev.empty:
        return ev
    ev = ev.copy()
    cards, prio = {}, []
    club_sl: dict[str, int] = {}
    if ctx.ready and not ctx.shortlist.empty:
        sl = ctx.shortlist["key"]
        for u in sl:
            if u in ctx.idn.players["uid"].values:
                lc = ctx.idn.players.set_index("uid").loc[u, "last_club"]
                club_sl[lc] = club_sl.get(lc, 0) + 1
    rows = []
    for r in ev.itertuples():
        uid = r.uid if isinstance(r.uid, str) else None
        card = None
        if uid and ctx.ready:
            if uid not in cards:
                cards[uid] = player_card(ctx, uid)
            card = cards[uid]
        n_sl = 0
        if r.event_type == "club_roster_update" and r.club_text:
            from .clubs import club_key
            n_sl = sum(v for k, v in club_sl.items() if club_key(k) == (ctx.catalog.canon(r.club_text) if ctx.catalog else club_key(r.club_text)))
        p = review_priority({"event_type": r.event_type, "announced_at": r.announced_at}, card, n_sl)
        rows.append((card, p))
    ev["card"] = [c for c, _ in rows]
    ev["tier"] = [p["tier"] for _, p in rows]
    ev["points"] = [p["points"] for _, p in rows]
    ev["factors"] = [p["factors"] for _, p in rows]
    ev["shortlisted"] = [bool(c and c.get("shortlist")) for c, _ in rows]
    ev["position_group"] = [(c or {}).get("position_group") for c, _ in rows]
    ev["age_at_season"] = [((c or {}).get("record") or {}).get("age_at_season") for c, _ in rows]
    ev["league"] = [((c or {}).get("record") or {}).get("league") or (c or {}).get("last_league") or r_hint
                    for (c, _), r_hint in zip(rows, ev["league_hint"])]
    ev["last_season"] = [(c or {}).get("last_season") for c, _ in rows]
    ev["seasons_behind"] = [(c or {}).get("seasons_since_last_record") for c, _ in rows]
    ev["freshness"] = ev["seasons_behind"].map(lambda v: "No profile" if v is None or pd.isna(v) else
                                               "Current season" if v == 0 else "1 season old" if v == 1 else "2+ seasons old")
    ev["minutes"] = [((c or {}).get("record") or {}).get("minutes") for c, _ in rows]
    ev["display_name"] = [(c or {}).get("name") for c, _ in rows]
    return ev


TIER_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Lead": 3}


def sort_for_review(ev: pd.DataFrame) -> pd.DataFrame:
    if ev.empty:
        return ev
    e = ev.copy()
    e["_t"] = e["tier"].map(TIER_ORDER).fillna(9)
    e["_u"] = (~e["updated_flag"].fillna(False)).astype(int)
    e["_n"] = (e["inbox_status"] != "New").astype(int)
    return e.sort_values(["_n", "_t", "_u", "points", "announced_at"], ascending=[True, True, True, False, False]).drop(columns=["_t", "_u", "_n"])


def source_links(row) -> str:
    return " | ".join(s["url"] for s in row["sources"])


def to_csv(ev: pd.DataFrame) -> str:
    """One line per event with the three kinds of information in separate columns."""
    rows = []
    for r in ev.itertuples():
        c = r.card if isinstance(r.card, dict) and r.card.get("available") else {}
        rec = c.get("record") or {}
        sl = c.get("shortlist") or {}
        prj = c.get("projection") or {}
        src = r.sources or []
        rows.append({
            "event_id": r.event_id, "review_status": r.inbox_status, "your_note": r.user_note,
            "match_status": r.match_status, "match_grade": r.match_grade, "player_id": r.uid or "",
            # what the source says
            "SOURCE_player_as_written": r.player_text, "SOURCE_announcement_type": r.type_label,
            "SOURCE_club": r.club_text or "", "SOURCE_announced": str(r.announced_at)[:10],
            "SOURCE_quote": (src[0]["quote"] if src else r.quote or ""), "SOURCE_links": " | ".join(s["url"] for s in src),
            "SOURCE_n_sources": r.n_sources, "first_seen": str(r.first_seen)[:16],
            # what our database records
            "DB_name": c.get("name", ""), "DB_position": c.get("position", ""), "DB_age_at_season": rec.get("age_at_season", ""),
            "DB_latest_record": f"{rec.get('league','')} {rec.get('season','')} {rec.get('team','')}".strip(),
            "DB_minutes": rec.get("minutes", ""), "DB_data_freshness": r.freshness, "DB_partial_dataset": rec.get("partial_dataset", ""),
            "DB_contract_expiry": f"{c.get('contract_expiry') or ''} ({c.get('contract_source') or ''})" if c.get("contract_expiry") else "",
            "DB_shortlisted": bool(sl), "DB_shortlist_role": sl.get("role", ""), "DB_shortlist_note": sl.get("note", ""),
            "DB_projection_limits": " / ".join(prj.get("limits", [])),
            # what the system infers
            "INFERRED_priority_tier": r.tier, "INFERRED_priority_factors": "; ".join(r.factors),
            "INFERRED_meaning": " ".join(inference_for(r.event_type, bool(sl))),
            "INFERRED_match_reasons": "; ".join(json.loads(r.match_reasons or "[]")),
        })
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue()
