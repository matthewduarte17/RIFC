"""Reading a roster page's text (a changedetection.io snapshot) into player names, and comparing two snapshots.

A roster page is not an announcement. A name appearing or disappearing is only a signal that something may have
happened; the monitor records it as UNCONFIRMED until an announcement supports it (see watch.py).

Formats handled: '9 Brandon Diene FWD' lines, name-only lines under 'Forwards' style headings, table rows
('Brandon Diene F 6-1 180 27 Dakar, Senegal'), and '#9' then name on the next line. Staff sections are skipped.
Anything unclear is skipped rather than guessed, and structure checks stop a broken page from reading as a mass exit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .classify import HARD_STOP, NAME_RE, POSITION_WORDS
from .match import name_key

PLAYER_HEADINGS = re.compile(r"^(?:first[- ]team\s+)?(goalkeepers?|keepers?|defenders?|defense|midfielders?|midfield|forwards?|strikers?|attackers?|"
                             r"wingers?|players?|roster|squad|on loan|loans?|injured(?: reserve)?|out on loan|supplemental|reserves?|"
                             r"first team|senior team)\b[\s:]*$", re.I)
STAFF_HEADINGS = re.compile(r"\b(staff|coach(?:es|ing)?|technical|front office|management|medical|trainer|executive|ownership|"
                            r"administration|operations)\b", re.I)
STAFF_WORDS = re.compile(r"\b(head coach|assistant coach|goalkeeper coach|coach|manager|director|trainer|physio|analyst|president|"
                         r"owner|chairman|ceo|officer|equipment|therapist|scout)\b", re.I)
POS_TOKENS = {"gk", "df", "mf", "fw", "f", "m", "d", "g", "cb", "lb", "rb", "cm", "dm", "am", "lw", "rw", "st", "cf", "fwd", "def", "mid",
              "gk.", "goalkeeper", "defender", "midfielder", "forward", "striker", "winger"}
STAFF_HEADING_FULL = re.compile(r"^(?:technical|coaching|medical|front office|club|team|management|executive|operations|performance|"
                                r"administrative|support|football)?\s*(?:staff|coaches|management|personnel|office|department|leadership)\s*:?$", re.I)
TABLE_HEADER = {"name", "pos", "position", "ht", "height", "wt", "weight", "age", "hometown", "no", "no.", "#", "nat", "country", "birthdate",
                "dob", "previous", "club", "nationality", "jersey", "player", "born", "college", "last"}
LOCATION = re.compile(r"[A-Za-zÀ-ÿ.' -]+,\s*[A-Z][A-Za-zÀ-ÿ.' -]+$")


@dataclass
class RosterName:
    name: str
    key: str
    section: str          # goalkeepers / defenders / ... / unlabelled
    line: str


def _clean_line(line: str) -> str:
    l = re.sub(r"^[\s*•\-–—#]+", "", line.strip())
    l = re.sub(r"^\d{1,2}\s*[.)-]?\s+", "", l)
    return l.strip()


def _name_from_line(l: str) -> str | None:
    """The name at the start of a roster line, or the whole line when it is only a name."""
    m = NAME_RE.match(l)
    if not m:
        return None
    name = m.group(0).rstrip(".")
    rest = l[m.end():].strip(" ,:|-–")
    toks = name.split()
    # strip a trailing position token the regex swallowed ("Brandon Diene FWD")
    while toks and toks[-1].lower().rstrip(".") in POS_TOKENS:
        toks.pop()
    if len(toks) < 2 or len(toks) > 4:
        return None
    if any(t.lower().strip(".,'’") in HARD_STOP for t in toks):
        return None
    if rest:
        first = rest.split()[0].lower().rstrip(".,")
        # what follows must look like roster data (position, height, number, age), not more prose
        if not (first in POS_TOKENS or re.match(r"^[\d'\"(#]", first) or first in {"gk", "fwd", "def", "mid"} or first.isdigit()):
            if len(rest.split()) > 6 or "," not in rest and first[:1].isalpha() and len(rest) > 25:
                return None
    return " ".join(toks)


def parse_roster_text(text: str, exclude_keys: set | None = None) -> list[RosterName]:
    """exclude_keys: normalised club names (clubs.club_key). Pages carry club pickers and fixtures lists; those are not players."""
    from .clubs import club_key
    out, seen = [], set()
    exclude_keys = exclude_keys or set()
    section, in_staff = "unlabelled", False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        l = _clean_line(line)
        if not l:
            continue
        low = l.lower().rstrip(":")
        if PLAYER_HEADINGS.match(low):
            section, in_staff = re.sub(r"[^a-z ]", "", low).strip() or "unlabelled", False
            continue
        if STAFF_HEADING_FULL.match(l) or (len(l.split()) <= 5 and STAFF_HEADINGS.search(l) and not NAME_RE.fullmatch(l)):
            in_staff = True
            continue
        if {w.lower().strip(".:,") for w in l.split()} <= TABLE_HEADER:
            continue                                         # a table's column titles
        if in_staff or STAFF_WORDS.search(l) or LOCATION.fullmatch(l) or len(l) > 90 or re.search(r"\d{4}", l) and not re.match(r"^\S+ \S+", l):
            continue
        name = _name_from_line(l)
        if not name:
            continue
        k = name_key(name)
        if k in seen or club_key(name) in exclude_keys or club_key(l) in exclude_keys:
            continue
        seen.add(k)
        out.append(RosterName(name, k, section, l[:120]))
    return out


@dataclass
class RosterDiff:
    added: list
    removed: list
    old_n: int
    new_n: int
    anomaly: str | None = None

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed


def diff_rosters(old: list[RosterName], new: list[RosterName], min_names: int = 5) -> RosterDiff:
    """Names that appeared or disappeared. A page that suddenly lists far fewer names (an error page, a redesign, a
    widget that did not load) is reported as an anomaly with no names, never as a mass departure."""
    ok, nk = {r.key: r for r in old}, {r.key: r for r in new}
    anomaly = None
    if len(old) >= min_names and len(new) < min_names:
        anomaly = f"The page now lists {len(new)} names (it listed {len(old)}): treated as a layout or loading problem, not as departures."
    elif len(old) >= min_names and len(new) < 0.6 * len(old):
        anomaly = f"The page lists {len(new)} names, down from {len(old)} (over 40% fewer): probably a layout change; check by hand."
    elif len(old) >= min_names and len(new) > 1.6 * len(old):
        anomaly = f"The page lists {len(new)} names, up from {len(old)}: probably a layout change or a new section; check by hand."
    elif len(old) < min_names:
        anomaly = f"Too few names to compare reliably ({len(old)} before)."
    if anomaly:
        return RosterDiff([], [], len(old), len(new), anomaly)
    return RosterDiff([nk[k] for k in nk if k not in ok], [ok[k] for k in ok if k not in nk], len(old), len(new))


def looks_like_roster(names: list[RosterName], text: str) -> tuple[bool, str]:
    """Is this really a player list? Needs enough names AND structure (position headings or jersey numbers). A page that
    only has a few capitalised phrases (a coach, a menu) is not a roster, however many words look like names."""
    n = len(names)
    if n < 8:
        return False, f"only {n} name-like entries"
    labelled = sum(1 for r in names if r.section != "unlabelled")
    numbered = sum(1 for r in names if re.match(r"^[#*\-\s]*\d{1,2}\b", r.line))
    if labelled >= 0.6 * n or numbered >= 0.6 * n:
        return True, f"{n} names with position headings or jersey numbers"
    return False, f"{n} name-like entries but no position headings or jersey numbers"
