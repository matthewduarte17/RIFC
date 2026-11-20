"""What this database already records about a matched player, kept apart from what the news says and from what the
monitor infers. Everything here is read from the existing engine (identity layer, shortlist, projections, club
verifications); nothing is calculated that the engine does not already produce, and nothing is invented when a value
is missing (it is shown as 'not recorded').
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import db
from .. import identity as I
from .. import recruit as R
from ..schema import METRICS, POSITION_LABELS
from .classify import AVAILABILITY_CLOSING, AVAILABILITY_OPENING, EVENT_TYPES, UNCONFIRMED_TYPES
from .clubs import ClubCatalog
from .match import Matcher, Roster


@dataclass
class Ctx:
    eng: object = None
    idn: object = None
    disc: pd.DataFrame | None = None
    roster: Roster | None = None
    catalog: ClubCatalog | None = None
    shortlist: pd.DataFrame = field(default_factory=pd.DataFrame)
    verified: set = field(default_factory=set)
    warnings: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.idn is not None and self.roster is not None

    def followed(self, include_verified: bool = True) -> set:
        s = set(self.shortlist["key"]) if not self.shortlist.empty else set()
        return s | (self.verified if include_verified else set())


def load_context(progress=lambda m: None, with_projections: bool = True) -> Ctx:
    """Loads the engine, identity layer and projections exactly as the app does. Failures are recorded as
    warnings, never raised, so a scan can still fetch and store news."""
    ctx = Ctx()
    try:
        from ..api import Engine
        eng = Engine.load()
    except Exception as exc:
        eng = None
        ctx.warnings.append(f"The model could not be loaded ({type(exc).__name__}). Players are not matched until it is.")
    if eng is None:
        ctx.warnings.append("No fitted model found, so the identity layer cannot be built. News is stored; matching waits.")
        return ctx
    ctx.eng = eng
    try:
        idn = I.load_or_build(eng, progress=progress)
        ctx.idn = idn
        ctx.roster = Roster(idn)
        teams = idn.records[["team", "league"]].drop_duplicates().itertuples(index=False)
        ctx.catalog = ClubCatalog(list(teams))
        ctx.shortlist = R.shortlist()
        ctx.verified = I.verified_uids()
    except Exception as exc:
        ctx.warnings.append(f"The identity layer could not be built ({type(exc).__name__}: {exc}).")
        return ctx
    if with_projections:
        try:
            from .. import discovery as D
            tag = "_" + I.cache_file(eng).split("identity_")[-1].split(".")[0]
            ctx.disc = D.load_or_build(eng, records=idn.records, tag=tag)
        except Exception as exc:
            ctx.warnings.append(f"Projections could not be loaded ({type(exc).__name__}); cards show records only.")
    return ctx


def matcher(ctx: Ctx, decisions: pd.DataFrame | None = None) -> Matcher:
    return Matcher(ctx.roster, ctx.catalog, decisions)


def _num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except Exception:
        return None


def player_card(ctx: Ctx, uid: str) -> dict:
    """The 'what our database records' block, with season and export dates on every dated fact."""
    if not ctx.ready or uid not in ctx.idn.players["uid"].values:
        return {"uid": uid, "available": False}
    pl = ctx.idn.players.set_index("uid").loc[uid]
    rec = I.records_of(ctx.idn, uid)
    latest_any = rec.iloc[0] if len(rec) else None
    elig = rec[(rec["minutes"] >= 450) & rec["position_group"].notna() & (rec["position_group"] != "GK")]
    use = elig.iloc[0] if len(elig) else latest_any
    cs = I.club_status(ctx.idn, uid, ver=I.verifications())
    latest = (ctx.idn.league_latest or {}).get(pl["last_league"])
    seasons_since = None
    if latest and str(latest).isdigit() and str(pl["last_season"]).isdigit():
        seasons_since = int(latest) - int(pl["last_season"])
    card = {
        "uid": uid, "available": True, "name": pl["display_name"], "birth_year": _num(pl["birth_year"]),
        "nationality": pl["nationality"], "height_cm": _num(pl["height_cm"]), "foot": pl.get("foot"),
        "position": POSITION_LABELS.get(pl["last_position"], pl["last_position"]) if pl["last_position"] else None,
        "position_group": pl["last_position"], "identity_level": pl["identity_level"], "identity_flags": list(pl["flags"] or []),
        "last_club": pl["last_club"], "last_league": pl["last_league"], "last_season": str(pl["last_season"]),
        "league_latest_season": str(latest) if latest else None, "seasons_since_last_record": seasons_since,
        "recency": pl["recency"], "n_records": int(pl["n_records"]), "total_minutes": _num(pl["total_minutes"]),
        "last_partial": bool(pl.get("last_partial")), "club_status": cs,
        "contract_expiry": cs.get("contract_expiry"), "contract_source": cs.get("contract_source"),
    }
    if use is not None:
        card["record"] = {"season": str(use["season"]), "league": use["league"], "team": use["team"],
                          "position_group": use["position_group"], "minutes": _num(use["minutes"]),
                          "matches": _num(use.get("matches")), "age_at_season": _num(use.get("age_at_season")),
                          "partial_dataset": bool(use.get("display_only")), "eligible_for_projection": bool(len(elig))}
    sl = ctx.shortlist
    row = sl[sl["key"] == uid] if not sl.empty else sl
    card["shortlist"] = ({"role": row.iloc[0]["role"], "note": row.iloc[0]["note"] or "",
                          "added_at": str(row.iloc[0]["added_at"])[:10]} if len(row) else None)
    card["projection"] = projection_block(ctx, uid, use)
    return card


def projection_block(ctx: Ctx, uid: str, use) -> dict:
    """Existing neutral Championship projection outputs for the player's latest eligible record, with their limits."""
    out = {"available": False, "rows": [], "limits": []}
    if use is None:
        out["limits"].append("No season record with a position and minutes.")
        return out
    if use["position_group"] == "GK":
        out["limits"].append("Goalkeepers are not projected by this model.")
        return out
    if ctx.disc is None:
        out["limits"].append("Projection table not available in this run.")
        return out
    d = ctx.disc[(ctx.disc["uid"] == uid) & (ctx.disc["league"] == use["league"]) & (ctx.disc["season"].astype(str) == str(use["season"]))]
    if d.empty:
        out["limits"].append("This season record is not in the projection table.")
        return out
    r = d.sort_values("minutes", ascending=False).iloc[0]
    role = r.get("role")
    out.update({"role": role, "season": str(r["season"]), "league": r["league"], "minutes": _num(r["minutes"]),
                "display_only": bool(r.get("display_only"))})
    for m in R.PRIMARY.get(role, []):
        proj, now = _num(r.get(f"{m}_proj")), _num(r.get(f"{m}_now"))
        if proj is None and now is None:
            continue
        out["rows"].append({"metric": METRICS[m].label, "kind": METRICS[m].kind, "now": now, "proj": proj,
                            "lo80": _num(r.get(f"{m}_lo80")), "hi80": _num(r.get(f"{m}_hi80")),
                            "conf": r.get(f"{m}_conf"), "status": r.get(f"{m}_status"),
                            "pctl": _num(r.get(f"{m}_pctl"))})
    out["available"] = any(x["proj"] is not None for x in out["rows"])
    lim = out["limits"]
    lim.append(f"Neutral Championship projection (average team) from his {r['league']} {r['season']} per 90 over "
               f"{int(r['minutes']):,} minutes; not a prediction for any club, and not a fit rating for RIFC.")
    if r["minutes"] < 900:
        lim.append(f"Under 900 minutes: ranges are wide.")
    if out["display_only"]:
        lim.append("That season comes from a partial dataset (profiles and discovery only). The model was not trained on it.")
    if any((x["conf"] in ("Low", "None")) for x in out["rows"] if x["proj"] is not None):
        lim.append("Some metrics are Low confidence (not validated, or few past moves): treat as rough.")
    if not out["available"]:
        lim.append("No metric could be projected (insufficient data).")
    return out


def review_priority(event: dict, card: dict | None, club_shortlisted: int = 0) -> dict:
    """Transparent review order. NOT a fit rating and not validated: it only decides what to look at first.
    Returns {'tier': High|Medium|Low|Lead, 'points': int, 'factors': [str]}."""
    et = event["event_type"]
    factors, pts = [], 0
    if not card or not card.get("available"):
        if et == "club_roster_update":
            if club_shortlisted:
                return {"tier": "Medium", "points": 3, "factors": [f"{club_shortlisted} shortlisted player(s) were last recorded at this club (inference: the source names none of them)"]}
            return {"tier": "Low", "points": 0, "factors": ["Club level announcement; the source does not name players we could read"]}
        if et in AVAILABILITY_OPENING or et == "retirement":
            return {"tier": "Lead", "points": 0, "factors": ["Not in our database: a news lead only, no statistical profile"]}
        return {"tier": "Lead", "points": 0, "factors": ["Not in our database"]}
    sl = card.get("shortlist")
    if sl:
        pts += 4
        factors.append("On the shortlist" + (f" ({sl['role']})" if sl.get("role") else ""))
    elif (card.get("club_status") or {}).get("verified"):
        pts += 1
        factors.append("You recorded a club verification for him")
    if et in AVAILABILITY_OPENING:
        pts += 2
        factors.append(f"{EVENT_TYPES[et]}: may change his availability (needs checking)")
    elif et in UNCONFIRMED_TYPES:
        pass
    elif et in AVAILABILITY_CLOSING:
        factors.append(f"{EVENT_TYPES[et]}: availability update, not a lead by itself")
    unconfirmed = et in UNCONFIRMED_TYPES
    if unconfirmed:
        factors.append("Roster page change only: no announcement supports it yet (unconfirmed)")
    rec = card.get("record") or {}
    mins = rec.get("minutes") or 0
    if mins >= 450:
        pts += 1
        factors.append(f"{int(mins):,} minutes in his latest eligible season")
    if mins >= 1500:
        pts += 1
        factors.append("Regular minutes (1,500+)")
    prj = card.get("projection") or {}
    good = [x for x in prj.get("rows", []) if x["proj"] is not None and x["conf"] in ("High", "Medium") and x["status"] == "validated"]
    if good:
        pts += 1
        factors.append(f"{len(good)} validated projection(s) at Medium or High confidence")
        top = [x for x in good if x["pctl"] is not None and x["pctl"] >= 75]
        if top:
            pts += 1
            factors.append("Projects in the top quarter of Championship players in his role on: " + ", ".join(x["metric"] for x in top[:3]))
    elif prj.get("available"):
        factors.append("Projections exist but none is validated at Medium or High confidence")
    ann = event.get("announced_at")
    rs = str(rec.get("season") or card.get("last_season") or "")
    if ann is not None and not pd.isna(ann) and rs.isdigit():
        try:
            a_year = pd.Timestamp(ann).year
            if a_year < int(rs):
                pts -= 3
                factors.append(f"Announcement ({pd.Timestamp(ann):%b %Y}) is older than his latest record ({rs} season): "
                               "it may already be superseded; check where he is now")
        except Exception:
            pass
    since = card.get("seasons_since_last_record")
    if since is not None and since >= 1:
        pts -= 1
        factors.append(f"Data is {since} season(s) behind the latest in his league: older than this announcement")
    elif since == 0:
        factors.append("Latest season on record is current")
    if rec.get("partial_dataset"):
        factors.append("His latest season is from a partial dataset")
    if et in AVAILABILITY_CLOSING and not sl:
        pts = min(pts, 2)
    if unconfirmed:
        pts = min(pts, 4 if sl else 2)
    tier = "High" if pts >= 6 else "Medium" if pts >= 3 else "Low"
    if sl and tier == "Low":
        tier = "Medium"
    if unconfirmed and tier == "High":
        tier = "Medium"                                   # an unconfirmed page change is never top priority on its own
    return {"tier": tier, "points": pts, "factors": factors}


def inference_for(event_type: str, shortlisted: bool) -> list[str]:
    """What the monitor infers from the announcement type, and what it does not establish."""
    base = {
        "option_declined": ["The club chose not to keep him on that option. He may become a free agent or move; the club can still re-sign him.",
                            "Not established: that he is a free agent, affordable, interested, or eligible to sign."],
        "released": ["His contract with that club appears to have ended early. Check whether he is free to sign.",
                     "Not established: affordability, interest, or eligibility."],
        "out_of_contract": ["His contract is listed as expired or expiring. Clubs often keep negotiating with such players.",
                            "Not established: that he will reach free agency."],
        "departure": ["He appears to be leaving. Where he goes is not stated unless the source says so.",
                      "Not established: free agency, fee, or interest."],
        "retirement": ["He has retired or is retiring. Not a recruitment lead."],
        "loan_out": ["He is on loan: the parent club keeps his rights. Not available unless the parent club agrees."],
        "loan_in": ["He has joined a club on loan: his parent club keeps his rights. Update where he plays; he is not available to sign unless the parent club agrees."],
        "loan_return": ["He is returning from a loan; his status with the parent club is not stated."],
        "option_exercised": ["His club kept him for the next season. This lowers the chance he is available now."],
        "extension": ["His club extended or re-signed him. Update availability: likely off the market."],
        "under_contract": ["Listed as under contract for next season. Update availability: likely off the market."],
        "signing": ["He signed with a club. Update where he is; not a lead unless it changes our view of him."],
        "club_roster_update": ["The headline says the club made roster decisions. It does not name the players we could read."],
        "performance": ["A performance or award item about a player you follow."],
        "roster_added": ["His name now appears on the club's roster page. That is not an announcement: it may be a signing not yet announced, "
                         "a loan, an academy call-up, or a page edit.", "Not established: that he signed, on what terms, or that he left his previous club."],
        "roster_removed": ["His name no longer appears on the club's roster page. That is not an announcement: it may be a departure, a loan, "
                           "a move to another list, or a page edit.", "Not established: that he left, is a free agent, or is available."],
    }
    out = list(base.get(event_type, []))
    if shortlisted and event_type in AVAILABILITY_OPENING:
        out.append("He is on your shortlist: confirm his status before anything else.")
    return out


def context_from(eng, idn, disc=None) -> Ctx:
    """A context from objects the app already loaded (no second load of the model or identity layer)."""
    ctx = Ctx(eng=eng, idn=idn, disc=disc)
    ctx.roster = Roster(idn)
    ctx.catalog = ClubCatalog(list(idn.records[["team", "league"]].drop_duplicates().itertuples(index=False)))
    ctx.shortlist = R.shortlist()
    ctx.verified = I.verified_uids()
    return ctx
