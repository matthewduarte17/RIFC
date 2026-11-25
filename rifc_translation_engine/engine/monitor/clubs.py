"""Club names: the database writes clubs one way ("Rhode Island", "Quakes II"), news another ("Rhode Island FC",
"San Jose Earthquakes 2"). Matching is exact on a normalised key plus a small alias list; anything looser is
reported as a partial match, which never links a player automatically."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .. import config
from ..normalize import strip_accents

DROP = {"fc", "sc", "cf", "afc", "club", "the", "football", "soccer"}
# news spelling -> database spelling (after club_key). Extend in data/club_aliases.json without code.
SEED_ALIASES = {
    "new york cosmos": "ny cosmos", "sporting club jacksonville": "sporting jax", "sporting jacksonville": "sporting jax",
    "colorado springs switchbacks": "colorado springs", "new england revolution ii": "new england ii",
    "lafc 2": "los angeles ii", "lafc ii": "los angeles ii", "real monarchs slc": "real monarchs",
    "san jose earthquakes ii": "quakes ii", "san jose earthquakes 2": "quakes ii", "loudoun united": "loudoun united",
    "athletic club boise": "athletic club boise", "boise": "athletic club boise", "tormenta": "tormenta",
    "texoma": "texoma", "rifc": "rhode island", "hearts of pine": "portland hearts of pine", "charlotte independence": "charlotte independence",
    "ventura county fc": "ventura county", "inter miami cf ii": "inter miami ii", "inter miami 2": "inter miami ii",
}


def club_key(s) -> str:
    if not s:
        return ""
    t = strip_accents(str(s)).lower().replace("&", " and ")
    t = re.sub(r"\bred bulls?\b", "rb", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    toks = [("ii" if w == "2" else w) for w in t.split() if w not in DROP]
    return " ".join(toks)


def load_aliases() -> dict:
    a = dict(SEED_ALIASES)
    try:
        a.update({club_key(k): club_key(v) for k, v in json.loads((config.DATA_DIR / "club_aliases.json").read_text()).items()})
    except Exception:
        pass
    return a


@dataclass(frozen=True)
class ClubRef:
    key: str                # normalised database key
    name: str               # database spelling
    leagues: tuple          # leagues this club appears in
    strength: str           # exact | partial


class ClubCatalog:
    def __init__(self, teams, aliases: dict | None = None):
        """teams: iterable of (team, league)."""
        self.aliases = aliases if aliases is not None else load_aliases()
        self.by_key: dict[str, dict] = {}
        for team, league in teams:
            if not team:
                continue
            k = club_key(team)
            d = self.by_key.setdefault(k, {"name": str(team), "leagues": set()})
            d["leagues"].add(league)
        self._tok = {k: set(k.split()) for k in self.by_key}

    def canon(self, text) -> str:
        k = club_key(text)
        return self.aliases.get(k, k)

    USL = ("USL Championship", "USL League One")

    def lookup(self, text, league_hint: str | None = None) -> ClubRef | None:
        k = self.canon(text)
        if not k:
            return None
        if k in self.by_key:
            d = self.by_key[k]
            return ClubRef(k, d["name"], tuple(sorted(d["leagues"])), "exact")
        toks = set(k.split())
        # a short form ("Madison", "Birmingham", "Omaha") that is part of exactly one club's name in the whole catalogue
        shorter = [c for c, t in self._tok.items() if t and toks < t]          # every club it could mean, reserve teams included
        if len(shorter) == 1 and not any(toks == t for t in self._tok.values()):
            d = self.by_key[shorter[0]]
            ok_league = (league_hint is None or league_hint in d["leagues"] or
                         (league_hint in self.USL and any(l in self.USL for l in d["leagues"])))
            if ok_league:
                return ClubRef(shorter[0], d["name"], tuple(sorted(d["leagues"])), "short")
        reserve = "ii" in toks
        hits = [c for c, t in self._tok.items() if t and (t <= toks or toks <= t) and (("ii" in t) == reserve)
                and len(t & toks) >= 1]
        hits = [c for c in hits if len(self._tok[c]) >= 2 or len(toks) >= 2]
        if len(hits) == 1:
            d = self.by_key[hits[0]]
            return ClubRef(hits[0], d["name"], tuple(sorted(d["leagues"])), "partial")
        return None

    def find_in_text(self, text: str, league_hint: str | None = None) -> list[ClubRef]:
        """Clubs named in a text, longest names first, no overlaps. One word names (Miami, Alta, Naples) need a
        club suffix (FC, SC, II, 2) or must belong to the source's league, so places are not read as clubs."""
        words = re.findall(r"[A-Za-zÀ-ÿ0-9'’.&\-]+", text)
        low = [club_key(w) for w in words]
        out, used = [], set()
        for n in range(5, 0, -1):
            for i in range(0, len(words) - n + 1):
                if any(j in used for j in range(i, i + n)):
                    continue
                span = [w for w in low[i:i + n]]
                if any(not w for w in span[:1]) and n == 1:
                    continue
                cand = self.canon(" ".join(words[i:i + n]))
                nxt = low[i + n] if i + n < len(low) else ""
                if cand not in self.by_key:
                    cand2 = self.canon(" ".join(words[i:i + n + 1])) if nxt == "" and i + n < len(words) else None
                    if cand2 and cand2 in self.by_key and words[i + n].lower() in ("fc", "sc", "cf", "afc"):
                        cand = cand2
                    else:
                        continue
                d = self.by_key[cand]
                if len(cand.split()) == 1:
                    suffix = i + n < len(words) and words[i + n].lower().strip(".") in ("fc", "sc", "cf", "ii", "2")
                    in_league = bool(league_hint) and league_hint in d["leagues"]
                    if not (suffix or in_league) or not words[i][:1].isupper():
                        continue
                used.update(range(i, i + n))
                out.append((i, ClubRef(cand, d["name"], tuple(sorted(d["leagues"])), "exact")))
        out.sort(key=lambda x: x[0])
        seen, res = set(), []
        for _, c in out:
            if c.key not in seen:
                seen.add(c.key)
                res.append(c)
        return res
