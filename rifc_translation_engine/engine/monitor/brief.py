"""The daily brief. Only what is new or changed since the previous brief, and only what needs a decision. Each item says
what changed (the source), why it matters (our records and the review factors), and what to review next. Nothing is
added to reach a count: a quiet day gives a short brief."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd

from .. import config
from . import store
from .classify import AVAILABILITY_CLOSING, AVAILABILITY_OPENING, EVENT_TYPES
from .inbox import enrich, load_events, sort_for_review
from .profile import Ctx, inference_for

NEXT_STEP = {
    "option_declined": "Confirm his contract status and whether he is free to sign; review recent video before contacting anyone.",
    "released": "Confirm the contract was ended and he is free to sign; review recent video.",
    "out_of_contract": "Check whether the club is still negotiating; review recent video and note the date.",
    "departure": "Find out where he is going and under what terms; update your availability note.",
    "retirement": "No recruitment action; update your notes.",
    "loan_out": "Note the parent club holds his rights; revisit when the loan ends.",
    "loan_return": "Check his status with the parent club.",
    "option_exercised": "Update his availability note: he is likely under contract next season.",
    "extension": "Update his availability note: likely off the market; decide whether to keep him on the shortlist.",
    "under_contract": "Update his availability note.",
    "signing": "Update where he is; check whether this changes his role or minutes.",
    "performance": "Watch the match or review his latest numbers.",
    "club_roster_update": "Open the article and check which of the club's players are named.",
}


def _link(sources: list, n: int = 2) -> str:
    return " · ".join(f"[{(s['source'] or 'source').replace('_', ' ')}]({s['url']})" for s in sources[:n])


def _short(q: str, n: int = 170, around: str | None = None) -> str:
    q = (q or "").replace("\n", " ").strip()
    if len(q) <= n:
        return q
    i = q.lower().find((around or "").split(" ")[-1].lower()) if around else -1
    if i > n // 2:
        start = max(0, i - n // 2)
        return "…" + q[start:start + n].split(" ", 1)[-1].rsplit(" ", 1)[0] + "…"
    return q[:n].rsplit(" ", 1)[0] + "…"


def _item(r, section: str) -> dict:
    c = r.card if isinstance(r.card, dict) and r.card.get("available") else None
    rec = (c or {}).get("record") or {}
    who = (c or {}).get("name") or r.player_text
    ident = ""
    if c:
        ident = (f" ({(c.get('position') or 'position not recorded')}, last record {rec.get('league','')} {rec.get('season','')} "
                 f"{rec.get('team','')}, {int(rec['minutes']):,} min)" if rec.get("minutes") else f" ({c.get('position') or 'position not recorded'})")
    flagged = "" if (c is None or not c.get("last_partial")) else " Partial dataset."
    if pd.notna(r.announced_at):
        age = (datetime.now() - pd.Timestamp(r.announced_at).to_pydatetime()).days
        if age > 45:
            flagged += f" Announcement is {age} days old."
    shortlisted = bool(c and c.get("shortlist"))
    inf = inference_for(r.event_type, shortlisted)
    return {
        "event_id": int(r.event_id), "section": section, "tier": r.tier,
        "title": f"{who}{ident}: {EVENT_TYPES.get(r.event_type, r.event_type)}" + (f" at {r.club_text}" if r.club_text else ""),
        "source_says": f"{_short(r.sources[0]['quote'] if r.sources else r.quote, 170, r.player_text)}"
                       + (f" ({r.sources[0]['published']})" if r.sources and r.sources[0]["published"] else ""),
        "our_records": ("Our data: " + (f"last season on record {c['last_season']}" if c else "") +
                        (f"; {int(c['seasons_since_last_record'])} season(s) behind his league's latest" if c and c.get('seasons_since_last_record') else "") +
                        (f"; contract expiry {c['contract_expiry']} ({c['contract_source']})" if c and c.get("contract_expiry") else "") + "." + flagged) if c else
                       "Not in our database: a news lead only, no statistical profile.",
        "why": "; ".join(r.factors[:3]) if r.factors else "",
        "inference": " ".join(inf[:2]),
        "next": NEXT_STEP.get(r.event_type, "Open the source and review."),
        "links": _link(r.sources),
        "updated": bool(r.updated_flag), "uncertain": r.match_status != "matched",
    }


def _window_start(today: date, path=None) -> datetime:
    prev = store.query("SELECT MAX(generated_at) g FROM rm_briefs WHERE brief_date < ?", [today], path=path)
    g = prev["g"].iloc[0] if not prev.empty else None
    return pd.Timestamp(g).to_pydatetime() if pd.notna(g) else datetime.combine(today, datetime.min.time()) - timedelta(hours=36)


def source_health(path=None, max_hours: float = 30) -> dict:
    st = store.query("SELECT * FROM rm_source_state ORDER BY source_id", path=path)
    sc = store.query("SELECT * FROM rm_scans ORDER BY scan_id DESC LIMIT 1", path=path)
    last_ok = store.query("SELECT MAX(finished_at) t FROM rm_scans WHERE status IN ('ok','partial')", path=path)["t"].iloc[0]
    problems = []
    for r in st.itertuples():
        if r.last_status in ("failed", "blocked"):
            problems.append(f"{r.name}: {r.last_status} ({r.last_error}); {r.consecutive_failures} scan(s) in a row")
        elif r.last_success is not None and pd.notna(r.last_success) and (datetime.now() - pd.Timestamp(r.last_success).to_pydatetime()) > timedelta(hours=max_hours * 2):
            problems.append(f"{r.name}: last success {str(r.last_success)[:16]}")
    stale = None
    if pd.isna(last_ok):
        stale = "No scan has completed yet."
    elif datetime.now() - pd.Timestamp(last_ok).to_pydatetime() > timedelta(hours=max_hours):
        stale = f"The last completed scan was {str(last_ok)[:16]}: older than {max_hours:.0f} hours."
    last = None if sc.empty else {"status": sc.iloc[0]["status"], "at": str(sc.iloc[0]["finished_at"] or sc.iloc[0]["started_at"])[:16]}
    return {"problems": problems, "stale": stale, "last_scan": last, "last_success": None if pd.isna(last_ok) else str(last_ok)[:16]}


def build_brief(ctx: Ctx, today: date | None = None, path=None, save: bool = True) -> dict:
    today = today or date.today()
    since = _window_start(today, path)
    ev = load_events(path)
    ev = enrich(ev, ctx) if len(ev) else ev
    sections: dict[str, list] = {"shortlist": [], "signals": [], "closing": [], "performance": [], "leads": []}
    pending = 0
    if len(ev):
        mon = set(ev.loc[(ev["inbox_status"] == "Monitor") & ev["uid"].notna(), "uid"])
        followed = ctx.followed() | mon
        fresh = ev[(ev["first_seen"] >= since) | (ev["updated_flag"].fillna(False) & (ev["last_seen"] >= since))]
        fresh = fresh[fresh["inbox_status"].isin(["New", "Review", "Monitor"])]
        pending = int((ev["match_status"].eq("review") & ~ev["user_decided"].fillna(False)).sum())
        for r in sort_for_review(fresh).itertuples():
            uid = r.uid if isinstance(r.uid, str) else None
            if r.match_status == "review":
                continue                                               # handled under "needs your attention"
            if r.event_type == "club_roster_update":
                if r.tier in ("Medium", "High"):
                    sections["signals"].append(_item(r, "signals"))
                continue
            if uid and r.shortlisted:
                sections["shortlist"].append(_item(r, "shortlist"))
            elif r.event_type == "performance":
                if uid in followed:
                    sections["performance"].append(_item(r, "performance"))
            elif uid and r.event_type in AVAILABILITY_OPENING and r.tier in ("High", "Medium"):
                sections["signals"].append(_item(r, "signals"))
            elif uid and r.event_type in AVAILABILITY_CLOSING and uid in followed:
                sections["closing"].append(_item(r, "closing"))
            elif not uid and r.event_type in AVAILABILITY_OPENING:
                sections["leads"].append(r)
    leads = sections.pop("leads")
    health = source_health(path)
    uncertain = []
    if len(ev):
        q = ev[(ev["match_status"] == "review") & ~ev["user_decided"].fillna(False) & ev["inbox_status"].eq("New")]
        for r in q.head(6).itertuples():
            cands = json.loads(r.candidates_json or "[]")
            uncertain.append({"event_id": int(r.event_id), "text": f"{r.player_text} ({r.type_label}, {r.club_text or 'club not read'}): "
                              + (", ".join(f"{c['name']} ({c['last_club']} {c['last_season']})" for c in cands[:3]) or "no candidate"),
                              "links": _link(r.sources, 1)})
    payload = {"date": str(today), "since": since.isoformat(timespec="minutes"), "sections": sections,
               "lead_count": len(leads), "leads": [{"name": r.player_text, "type": r.type_label, "club": r.club_text,
                                                    "links": _link(r.sources, 1)} for r in leads[:6]],
               "uncertain": uncertain, "uncertain_total": pending, "health": health, "warnings": ctx.warnings}
    md = render_markdown(payload)
    payload["markdown"] = md
    if save:
        d = config.DATA_DIR / "briefs"
        d.mkdir(exist_ok=True)
        (d / f"brief_{today}.md").write_text(md)
        with store.conn(path) as con:
            con.execute("DELETE FROM rm_briefs WHERE brief_date = ?", [today])
            con.execute("INSERT INTO rm_briefs VALUES (?,?,?,?)", [today, datetime.now(), md, json.dumps({k: v for k, v in payload.items() if k != "markdown"}, default=str)])
    return payload


TITLES = {"shortlist": "Shortlisted players", "signals": "New departure and roster signals worth reviewing",
          "closing": "Signings and extensions affecting players you follow", "performance": "Performance news on players you follow"}


def render_markdown(p: dict) -> str:
    L = [f"# RIFC recruitment brief, {p['date']}", ""]
    h = p["health"]
    n_items = sum(len(v) for v in p["sections"].values())
    if n_items == 0 and not p["uncertain"] and not p["lead_count"]:
        L.append("Nothing new needs review since the last brief.")
        L.append("")
    for k in ("shortlist", "signals", "closing", "performance"):
        items = p["sections"][k]
        if not items:
            continue
        L += [f"## {TITLES[k]}", ""]
        for it in items:
            L.append(f"**{it['title']}**" + ("  _(updated)_" if it["updated"] else ""))
            L.append(f"- **Source says:** {it['source_says']}")
            L.append(f"- **Our records:** {it['our_records']}")
            if it["why"]:
                L.append(f"- **Why it matters:** {it['why']}.")
            if it["inference"]:
                L.append(f"- **Our reading (inference):** {it['inference']}")
            L.append(f"- **Review next:** {it['next']}")
            L.append(f"- Sources: {it['links']}")
            L.append("")
    if p["lead_count"]:
        L += ["## News leads not in our database", ""]
        L.append(f"{p['lead_count']} player(s) named in departure or contract announcements are not in our data (no statistical profile exists):")
        for x in p["leads"]:
            L.append(f"- {x['name']}: {x['type']}" + (f", {x['club']}" if x["club"] else "") + f" ({x['links']})")
        L.append("")
    if p["uncertain"]:
        L += ["## Uncertain matches needing your answer", ""]
        L.append(f"{p['uncertain_total']} announcement(s) could not be linked to one player safely. Open the Recruitment Inbox, filter 'Match needs review'.")
        for x in p["uncertain"]:
            L.append(f"- {x['text']} ({x['links']})")
        L.append("")
    attention = []
    if h["stale"]:
        attention.append(h["stale"])
    attention += h["problems"]
    attention += p.get("warnings", [])
    if attention:
        L += ["## Source and coverage problems", ""] + [f"- {a}" for a in attention] + [""]
    L.append(f"_Last successful scan: {h['last_success'] or 'never'}. Window: since {p['since'][:16].replace('T', ' ')}. "
             "Review priority is a reading order, not a fit rating._")
    return "\n".join(L)
