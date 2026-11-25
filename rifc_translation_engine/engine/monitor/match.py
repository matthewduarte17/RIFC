"""Connecting a name in the news to a player ID that already exists in this database.

This uses the identity layer as it is (engine/identity.py): player IDs, aliases, club histories, identity
confidence and flags. It never creates, merges or splits players.

Rules (the reason each exists is in the result's `reasons`):
  * A similar name is never enough. A link needs a compatible name AND corroboration from the club
    (the player's recorded clubs) AND it must be the only such candidate.
  * Two or more candidates, a partly matching club, a position that disagrees, or a player whose own identity
    is flagged (Uncertain / Probable / flags) all go to the review queue instead of being linked.
  * The database spells names the Wyscout way ("B. Diene"), news spells them in full ("Brandon Diene"). Initial and
    surname plus the right club is accepted; initial and surname alone is not.
  * Your earlier answers (same player / different player) are applied first and never overwritten by a scan.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from ..normalize import strip_accents
from .classify import POSITION_COMPAT
from .clubs import ClubCatalog, club_key

PARTICLES = {"de", "da", "di", "dos", "das", "do", "del", "della", "van", "von", "der", "den", "ter", "la", "le", "el",
             "al", "bin", "ibn", "dal", "du", "mac", "mc"}


SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def name_tokens(name: str) -> list[str]:
    t = strip_accents(str(name or "")).lower().replace("'", "").replace("’", "")
    t = re.sub(r"[^a-z\s\.\-]", " ", t)
    toks = []
    for w in t.replace("-", " - ").split():
        if w == "-":
            if toks:
                toks[-1] += "-"
            continue
        w = w.strip(".")
        if not w:
            continue
        if toks and toks[-1].endswith("-"):
            toks[-1] = toks[-1][:-1] + w
        else:
            toks.append(w)
    while len(toks) > 2 and toks[-1] in SUFFIXES:          # "Joel Garcia Jr." is Joel Garcia
        toks.pop()
    return toks


def _surname(toks: list[str]) -> str:
    if not toks:
        return ""
    s = toks[-1]
    i = len(toks) - 2
    while i >= 1 and toks[i] in PARTICLES:
        s = toks[i] + s
        i -= 1
    return s


def name_key(name: str) -> str:
    return " ".join(name_tokens(name))


def surname_key(name: str) -> str:
    return _surname(name_tokens(name))


# Given names that are well known short forms. Used only to let a name be considered; the club must still agree.
NICKNAMES = {"gabi": "gabriel", "memo": "guillermo", "pipe": "felipe", "beto": "alberto", "nacho": "ignacio", "paco": "francisco", "pepe": "jose",
             "quique": "enrique", "kike": "enrique", "fer": "fernando", "santi": "santiago", "nico": "nicolas", "tj": "thomas", "ben": "benjamin",
             "alex": "alexander", "mike": "michael", "matt": "matthew", "will": "william", "bill": "william", "joe": "joseph", "josh": "joshua"}


def _same_given(a: str, b: str) -> bool:
    return NICKNAMES.get(a, a) == NICKNAMES.get(b, b) or NICKNAMES.get(a) == b or NICKNAMES.get(b) == a


def name_compat(news: str, db: str) -> str | None:
    """'exact', 'prefix' (Maxi / Maximiliano), 'initial' (B. / Brandon) or None. Surname must agree."""
    n, d = name_tokens(news), name_tokens(db)
    if len(n) < 2 or len(d) < 2 or _surname(n) != _surname(d):
        return None
    def given(t):
        s = _surname(t)
        out = list(t)
        while out and "".join(out).endswith(s):
            out = out[:-1]
            if "".join(out) + s == "".join(t):
                break
        return [x for x in t[:len(t) - max(1, sum(1 for x in t if x in PARTICLES) + 1)]] if len(t) > 1 else []
    ng, dg = n[:-1], d[:-1]
    ng = [x for x in ng if x not in PARTICLES] or ng
    dg = [x for x in dg if x not in PARTICLES] or dg
    if not ng or not dg:
        return None
    if ng == dg:
        return "exact"
    n0, d0 = ng[0], dg[0]
    if len(n0) == 1 or len(d0) == 1:
        return "initial" if n0[0] == d0[0] else None
    if n0 == d0:
        return "exact"
    if len(n0) >= 3 and len(d0) >= 3 and (n0.startswith(d0) or d0.startswith(n0) or _same_given(n0, d0)):
        return "prefix"
    return None


def name_compat_variant(news: str, db: str) -> str | None:
    """Same given name or initial and a surname that differs by a letter or two (Elmasnouy / Elmasnaouy). Never automatic."""
    n, d = name_tokens(news), name_tokens(db)
    if len(n) < 2 or len(d) < 2:
        return None
    n0, d0 = n[0], d[0]
    ok = (len(n0) == 1 or len(d0) == 1) and n0[0] == d0[0] or n0 == d0
    return "initial" if ok else None


@dataclass
class Candidate:
    uid: str
    display_name: str
    name_level: str                 # exact | prefix | initial
    club_level: str                 # current | recent | older | partial | none
    reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    hits: int = 0                   # how many of the clubs named in the announcement are in his recorded history
    last_club: str = ""
    last_league: str = ""
    last_season: str = ""
    position: str = ""

    def public(self) -> dict:
        return {"uid": self.uid, "name": self.display_name, "name_level": self.name_level,
                "club_level": self.club_level, "reasons": self.reasons, "flags": self.flags,
                "last_club": self.last_club, "last_league": self.last_league, "last_season": self.last_season,
                "position": self.position}


@dataclass
class MatchResult:
    status: str                      # matched | review | unmatched
    uid: str | None = None
    grade: str | None = None         # Strong | Confirmed by you | Possible | Weak
    reasons: list = field(default_factory=list)
    candidates: list = field(default_factory=list)


class Roster:
    """Index over the identity layer's players, built once per scan."""

    def __init__(self, idn):
        self.idn = idn
        p = idn.players
        self.players = p.set_index("uid")
        self.latest = {k: str(v) for k, v in (idn.league_latest or {}).items()}
        self.hist: dict[str, list] = defaultdict(list)
        rec = idn.records
        cols = [c for c in ("uid", "team", "league", "season", "season_start", "current_team") if c in rec]
        for r in rec[cols].itertuples(index=False):
            d = r._asdict()
            if d.get("team"):
                self.hist[d["uid"]].append((club_key(d["team"]), d["league"], str(d["season"]), d.get("season_start"), "season club"))
            if d.get("current_team") and isinstance(d.get("current_team"), str):
                self.hist[d["uid"]].append((club_key(d["current_team"]), d["league"], str(d["season"]), d.get("season_start"),
                                            "Wyscout 'Team' field at export"))
        self.by_surname: dict[str, set] = defaultdict(set)
        self.names: dict[str, list] = {}
        for r in p[["uid", "display_name", "aliases"]].itertuples(index=False):
            names = [r.display_name] + [a for a in (r.aliases or []) if isinstance(a, str)]
            names = list(dict.fromkeys(names))
            self.names[r.uid] = names
            for n in names:
                sk = surname_key(n)
                if sk:
                    self.by_surname[sk].add(r.uid)

    def club_level(self, uid: str, ckey: str, league_hint: str | None):
        """How well the player's recorded history supports the club named in the news."""
        best, why = "none", ""
        rank = {"none": 0, "older": 1, "recent": 2, "current": 3}
        rows = [h for h in self.hist.get(uid, []) if h[0] == ckey]
        for key, lg, season, st, field_ in rows:
            latest = self.latest.get(lg)
            lvl = "older"
            if latest and season == latest:
                lvl = "current"
            elif latest and latest.isdigit() and season.isdigit() and int(season) == int(latest) - 1:
                lvl = "recent"
            if rank[lvl] > rank[best]:
                best, why = lvl, f"{field_}: {lg} {season}"
        return best, why

    def last_record(self, uid: str) -> dict:
        r = self.players.loc[uid]
        return {"last_club": r["last_club"], "last_league": r["last_league"], "last_season": str(r["last_season"]),
                "position": r.get("last_position") or ""}


class Matcher:
    def __init__(self, roster: Roster, catalog: ClubCatalog, decisions: pd.DataFrame | None = None):
        self.r, self.cat = roster, catalog
        self.same: dict[tuple, str] = {}
        self.different: dict[tuple, set] = defaultdict(set)
        if decisions is not None and not decisions.empty:
            for d in decisions.itertuples():
                k = (d.name_norm, d.club_norm or "")
                if d.verdict == "same":
                    self.same[k] = d.uid
                elif d.verdict == "different":
                    self.different[k].add(d.uid)

    def candidates(self, name: str, club_text: str | None, league_hint: str | None, pos_hint: str | None, extra_clubs=(), article_clubs=()) -> list[Candidate]:
        sk = surname_key(name)
        if not sk:
            return []
        ref = self.cat.lookup(club_text, league_hint) if club_text else None
        extra_refs = [r for r in (self.cat.lookup(x, league_hint) for x in extra_clubs if x) if r is not None]
        art_refs = [r for r in (self.cat.lookup(x) for x in article_clubs if x) if r is not None and r.strength == "exact"]
        out = []
        exact_uids = set(self.r.by_surname.get(sk, ()))
        variant_uids = set()
        if not exact_uids and len(sk) >= 6:
            import difflib
            for near in difflib.get_close_matches(sk, list(self.r.by_surname), n=3, cutoff=0.88):
                variant_uids |= set(self.r.by_surname[near])
        for uid in list(exact_uids) + list(variant_uids):
            best = None
            for dbn in self.r.names[uid]:
                lvl = name_compat(name, dbn) if uid in exact_uids else name_compat_variant(name, dbn)
                if lvl and (best is None or ["exact", "prefix", "initial"].index(lvl) < ["exact", "prefix", "initial"].index(best[0])):
                    best = (lvl, dbn)
            if not best:
                continue
            lvl, dbn = best
            rec = self.r.last_record(uid)
            c = Candidate(uid, self.r.players.loc[uid, "display_name"], lvl, "none", **rec)
            c.reasons.append({"exact": f"Full name agrees ('{name}' = '{dbn}').",
                              "prefix": f"Name agrees apart from a short form ('{name}' ~ '{dbn}').",
                              "initial": f"Surname agrees and the database holds the first name as an initial ('{name}' ~ '{dbn}')."}[lvl])
            rank = {"none": 0, "partial": 0, "older": 1, "recent": 2, "current": 3}
            best_ref = None
            seen_keys = set()
            for rf in ([ref] if ref is not None else []) + extra_refs:
                cl, why = self.r.club_level(uid, rf.key, league_hint)
                if rf.strength == "partial" and cl != "none":
                    cl = "partial"
                if cl in ("current", "recent") and rf.key not in seen_keys:
                    seen_keys.add(rf.key)
                    c.hits += 1
                if best_ref is None or rank[cl] > rank[best_ref[1]]:
                    best_ref = (rf, cl, why)
            if best_ref is not None:
                rf, cl, why = best_ref
                c.club_level = cl
                if cl in ("current", "recent", "older"):
                    c.reasons.append(f"Club agrees: {rf.name} is in his recorded history ({why})"
                                     + ("; named in the same sentence" if rf is not ref else "")
                                     + (f"; the announcement says '{club_text if rf is ref else 'a short form'}', which is only {rf.name} in our data" if rf.strength == "short" else "") + ".")
                elif cl == "partial":
                    c.reasons.append(f"Club only partly agrees ('{club_text}' ~ {rf.name}).")
                else:
                    c.reasons.append(f"Club does not agree: {rf.name} is not in his recorded clubs "
                                     f"(last recorded {rec['last_club']}, {rec['last_league']} {rec['last_season']}).")
            if c.club_level in ("none", "partial") and art_refs:
                for rf in art_refs:
                    cl, why = self.r.club_level(uid, rf.key, league_hint)
                    if cl in ("current", "recent", "older"):
                        c.club_level = "article"
                        c.reasons.append(f"A club named elsewhere in the article ({rf.name}) is in his recorded history ({why}), "
                                         "but the sentence about him does not name it: to be confirmed by you.")
                        break
            if best_ref is not None:
                pass
            elif c.club_level == "article":
                pass
            else:
                lg = rec["last_league"]
                c.reasons.append("The announcement's club is not a club in this database, so the club cannot corroborate."
                                 if club_text else "The announcement names no club that could be read.")
                if league_hint and lg == league_hint:
                    c.reasons.append(f"Same league as the source ({lg}).")
            if pos_hint and rec["position"]:
                ok = rec["position"] in POSITION_COMPAT.get(pos_hint, {rec["position"]})
                if not ok:
                    msg = f"Position differs: announcement says {pos_hint}, database says {rec['position']}."
                    hard = (pos_hint == "GK") != (rec["position"] == "GK")      # a goalkeeper vs an outfielder is never a labelling difference
                    if c.hits >= 2 and not hard:      # both clubs the announcement names are in his history: that outweighs a position label
                        c.reasons.append(msg + " Noted, not blocking, because both clubs named in the announcement are in his history.")
                    else:
                        c.flags.append(msg)
            if uid in variant_uids:
                c.flags.append("The surname is spelt differently in the database: confirm it is the same person.")
            pl = self.r.players.loc[uid]
            if pl.get("identity_level") in ("Uncertain", "Probable"):
                c.flags.append(f"His own identity is flagged in the database ({pl['identity_level']}); check Identity Review.")
            elif list(pl.get("flags") or []):
                c.flags.append("Identity flags: " + "; ".join(map(str, pl["flags"]))[:200])
            out.append(c)
        return out

    def match(self, name: str, club_text: str | None, league_hint: str | None = None, pos_hint: str | None = None, extra_clubs=(), article_clubs=(),
              name_only_review: bool = False) -> MatchResult:
        nk = name_key(name)
        ref = self.cat.lookup(club_text, league_hint) if club_text else None
        ck = ref.key if ref else ""
        dk = (nk, ck)
        # 1. what you already decided about this name at this club
        if dk in self.same and self.same[dk] in self.r.players.index:
            uid = self.same[dk]
            return MatchResult("matched", uid, "Confirmed by you",
                               ["You confirmed this name at this club as this player in an earlier review."],
                               [Candidate(uid, self.r.players.loc[uid, "display_name"], "exact", "current",
                                          **self.r.last_record(uid)).public()])
        cands = [c for c in self.candidates(name, club_text, league_hint, pos_hint, extra_clubs, article_clubs)
                 if c.uid not in self.different.get(dk, ())]
        if not cands:
            return MatchResult("unmatched", reasons=["No player in the database has a compatible name."])
        good = [c for c in cands if c.club_level in ("current", "recent")]
        # two similar players at one club: the one who also matches the OTHER club the announcement names (a loan names two)
        if len(good) > 1:
            top = max(c.hits for c in good)
            lead = [c for c in good if c.hits == top]
            if top >= 2 and len(lead) == 1 and not lead[0].flags:
                w = lead[0]
                w.reasons.append(f"Only he matches both clubs named in the announcement; {len(good) - 1} similar player(s) match only one.")
                return MatchResult("matched", w.uid, "Strong", w.reasons, [w.public()] + [c.public() for c in cands if c.uid != w.uid][:4])
        clean = [c for c in good if not c.flags]
        # 2. one clear candidate: compatible name + club corroboration + nobody else comparable
        if len(good) == 1 and len(clean) == 1 and len(cands) == 1:
            c = good[0]
            return MatchResult("matched", c.uid, "Strong", c.reasons + ["Only one player in the database fits."], [c.public()])
        if len(good) == 1 and len(clean) == 1:
            others = [x for x in cands if x.uid != good[0].uid]
            if all(x.club_level == "none" for x in others):
                c = good[0]
                return MatchResult("matched", c.uid, "Strong", c.reasons + [
                    f"{len(others)} other player(s) share the surname but have no link to this club in the database."],
                    [c.public()] + [o.public() for o in others[:4]])
        # 3. everything else is for a person to decide
        ranked = sorted(cands, key=lambda c: ({"current": 0, "recent": 1, "older": 2, "article": 3, "partial": 4, "none": 5}[c.club_level],
                                              ["exact", "prefix", "initial"].index(c.name_level)))
        keep = [c for c in ranked if c.club_level != "none" or c.name_level == "exact"][:5]
        if not keep and name_only_review:
            # a name on a club's roster page: his recorded club can legitimately be a different one (that is the move). A
            # compatible name with a current or recent record in our data is queued for you, never linked.
            latest = {k: v for k, v in self.r.latest.items()}
            recent = [c for c in ranked if c.last_season.isdigit() and latest.get(c.last_league, "0").isdigit()
                      and int(c.last_season) >= int(latest[c.last_league]) - 1][:3]
            if recent:
                for c in recent:
                    c.reasons.append("Name fits and he has a recent record in our data at another club; the page club has no link in our data. "
                                     "This could be a move, or a different player with a similar name.")
                return MatchResult("review", None, "Weak", ["Name only: needs your confirmation."], [c.public() for c in recent])
        if not keep:
            return MatchResult("unmatched", reasons=[f"{len(cands)} player(s) share the surname, but none fits the club "
                                                     "and none has the full name. Treated as a news lead, not a match."],
                               candidates=[c.public() for c in ranked[:3]])
        top = keep[0]
        why = []
        if len(good) > 1:
            why.append(f"{len(good)} players fit the name and club: cannot choose between them.")
        elif good and good[0].flags:
            why.append("One player fits, but " + " ".join(good[0].flags))
        elif top.club_level == "article":
            why.append("The name fits and the article names a club he is known at, but not in the sentence about him.")
        elif top.club_level in ("older", "partial"):
            why.append("The name fits but the club is only in his older history or only partly agrees.")
        else:
            why.append("The full name fits but nothing corroborates the club.")
        grade = "Possible" if top.club_level in ("current", "recent", "older", "article") else "Weak"
        return MatchResult("review", None, grade, why, [c.public() for c in keep])
