"""Announcement types and player name extraction.

Ordinary pattern matching, written against real club announcement wording ("declined contract options for
goalkeepers X and Y, defenders ...", "A, B and C are out of contract", "Club signs forward X to a USL Academy
contract"). It reads one clause at a time, finds the cue (declined, exercised, released, signed, ...) and takes the
capitalised names on the correct side of it.

What it can get wrong is recorded: every mention keeps the sentence it came from, and mentions are labelled as
extracted by pattern, so a scout can check the wording. Anything it cannot read is left out, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..normalize import norm_name, strip_accents

EVENT_TYPES = {
    "option_declined": "Option declined",
    "released": "Released / contract ended",
    "out_of_contract": "Out of contract",
    "departure": "Departure / not returning",
    "retirement": "Retirement",
    "loan_out": "Loan out",
    "loan_return": "Loan return / recall",
    "option_exercised": "Option exercised",
    "extension": "Extension / re-signing",
    "under_contract": "Under contract (retained)",
    "signing": "Signing",
    "club_roster_update": "Club roster update (players not read)",
    "performance": "Performance / recognition",
}
# What the announcement does for availability. These are readings of the announcement, not facts about the player.
AVAILABILITY_OPENING = {"option_declined", "released", "out_of_contract", "departure", "loan_return"}
AVAILABILITY_CLOSING = {"option_exercised", "extension", "under_contract", "signing", "loan_out"}
MOVEMENT_TYPES = AVAILABILITY_OPENING | AVAILABILITY_CLOSING | {"retirement"}
PRIORITY = ["option_declined", "option_exercised", "released", "retirement", "loan_return", "loan_out", "extension",
            "under_contract", "signing", "out_of_contract", "departure"]

NAME_PLAIN = "[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’`\\-]+\\.?"
PARTICLE = "(?:de|da|di|dos|das|do|del|della|van|von|der|den|ter|la|le|el|al|bin|ibn|dal|du|mac|mc)"
INITIAL = "[A-ZÀ-Þ]\\.(?:[A-Z]\\.)?"
NAME_RE = re.compile(rf"(?<![A-Za-zÀ-ÿ'’])(?:{INITIAL}|{NAME_PLAIN})(?:\s+(?:{PARTICLE}\s+)?{NAME_PLAIN}){{1,3}}")

# Coarse classes: news says "defender" or "midfielder", the database has finer groups. POSITION_COMPAT maps one to the other.
POSITION_WORDS = {
    "goalkeeper": "GK", "goalkeepers": "GK", "keeper": "GK", "keepers": "GK", "defender": "DEF", "defenders": "DEF",
    "centre-back": "CB", "center-back": "CB", "fullback": "FB", "fullbacks": "FB", "full-back": "FB", "wingback": "FB",
    "midfielder": "MID", "midfielders": "MID", "forward": "FWD", "forwards": "FWD", "striker": "FWD", "strikers": "FWD",
    "winger": "W", "wingers": "W", "attacker": "FWD", "attackers": "FWD",
}
# Deliberately broad: clubs and providers label wingers as midfielders and holding midfielders as defenders. Only a
# real contradiction (a goalkeeper vs an outfielder, a defender vs a forward) counts as a difference.
POSITION_COMPAT = {"GK": {"GK"}, "DEF": {"CB", "FB", "DM"}, "CB": {"CB", "FB", "DM"}, "FB": {"FB", "CB", "W", "CM", "DM"},
                   "MID": {"DM", "CM", "AM", "W", "FB"}, "FWD": {"ST", "W", "AM"}, "W": {"W", "AM", "ST", "FB", "CM"}}
DESCRIPTORS = {"academy", "homegrown", "home-grown", "free", "agent", "veteran", "draft", "pick", "the", "club",
               "their", "both", "all", "talented", "versatile", "experienced", "defensive", "attacking",
               "central", "center", "centre", "wide", "former", "current", "and", "pro", "usl", "mls", "next",
               "contract", "multi-year", "two-year", "one-year", "three-year", "league", "championship", "season",
               "goalkeeper", "goalkeepers", "defender", "defenders", "midfielder", "midfielders", "forward", "forwards",
               "striker", "strikers", "winger", "wingers", "college", "international", "national", "also"}
HARD_STOP = {"usl", "mls", "nwsl", "fc", "sc", "cf", "ii", "championship", "league", "next", "pro",
             "conference", "final", "finals", "cup", "season", "contract", "contracts", "options", "option", "roster",
             "rosters", "announces", "announced", "players", "club", "academy", "january", "february", "march", "april", "may",
             "june", "july", "august", "september", "october", "november", "december", "monday", "tuesday", "wednesday",
             "thursday", "friday", "saturday", "sunday", "photo", "courtesy", "staff", "follow", "read", "more", "news",
             "head", "coach", "assistant", "director", "general", "manager", "president", "stadium",
             "usa", "ncaa", "division", "draft", "transfer", "portal", "tracker", "update", "updates",
             "decisions", "moves", "the", "for", "with", "and", "after", "from", "into", "pawtucket"}

CUES: list[tuple[str, str, re.Pattern]] = []


def _c(kind, typ, pat):
    CUES.append((typ, kind, re.compile(pat, re.I)))


_OPT = r"(?:the\s+|their\s+|its\s+|club'?s\s+|his\s+)*(?:contract\s+)?options?"
# names AFTER the cue
_c("first", "option_declined", rf"\bdeclin\w*\s+(?:to\s+(?:exercise|pick\s+up)\s+)?{_OPT}\s+(?:for|on|of|with)\b")
_c("first", "option_declined", rf"\b(?:did\s+not|didn'?t|will\s+not|won'?t|not)\s+(?:to\s+)?(?:exercise|pick\s+up)\s+{_OPT}\s+(?:for|on|of)\b")
_c("first", "option_exercised", rf"\bexercis\w*\s+{_OPT}\s+(?:for|on|of|with)\b")
_c("first", "option_exercised", rf"\bpick(?:ed|s)?\s+up\s+{_OPT}\s+(?:for|on)\b")
_c("first", "released", r"\b(?:has\s+|have\s+)?(?:releas(?:e|es|ed|ing)|waiv(?:e|es|ed|ing)|part(?:s|ed|ing)?\s+ways\s+with|buy(?:s|ing)?\s+out\s+the\s+contracts?\s+of|bought\s+out\s+the\s+contracts?\s+of|terminat(?:e|es|ed|ing)\s+the\s+contracts?\s+of)\s+(?=\S)")
_c("first", "released", r"\bmutual(?:ly)?\s+(?:agreed\s+to\s+)?terminat\w*\s+(?:the\s+)?(?:contracts?\s+)?(?:of|with|for)\s+")
_c("first", "released", r"\bmutual\s+contract\s+terminations?\s+(?:for|of|with)\s+")
_c("first", "retirement", r"\bretirement\s+of\s+")
_c("first", "retirement", r"\bannounced\s+(?:the\s+)?retire\w*\s+(?:of\s+)?")
_c("first", "loan_out", r"\bloan(?:s|ed)?\s+(?:out\s+)?(?=[A-Za-z])")
_c("first", "loan_return", r"\brecall(?:s|ed)?\s+")
_c("first", "extension", r"\b(?:re-?sign(?:s|ed)?|extend(?:s|ed)?)\s+(?=\S)")
_c("first", "extension", r"\bextension\s+(?:for|with|of)\s+")
_c("first", "extension", r"\bnew\s+(?:\w+-year\s+)?contract\s+(?:for|with)\s+")
_c("first", "under_contract", r"\b(?:retain(?:s|ed)?\s+(?=\S)|(?:previously\s+|currently\s+|already\s+)?under\s+contract\s*[–—:\-]\s*)")
_c("first", "signing", r"\b(?:sign(?:s|ed)?|acquir(?:es?|ed)|add(?:s|ed)|ink(?:s|ed)|land(?:s|ed)|secur(?:es?|ed)|welcom(?:es?|ed)|select(?:s|ed)|trade[sd]?\s+for)\s+(?=\S)")
_c("first", "signing", r"\bsigning\s+of\s+")
# names BEFORE the cue
_c("subject", "out_of_contract", r"\b(?:are|is|were)\s+(?:all\s+|both\s+|currently\s+|now\s+)*out\s+of\s+contract\b")
_c("subject", "out_of_contract", r"\b(?:are|is)\s+(?:all\s+)?(?:set\s+to\s+)?become\s+free\s+agents?\b")
_c("subject", "released", r"\b(?:have|has|were|was)\s+(?:all\s+)?(?:been\s+)?(?:released|waived)\b")
_c("subject", "option_declined", r"\b(?:were|was)\s+not\s+offered\s+(?:new\s+)?(?:contract\s+)?options?\b")
_c("subject", "departure", r"\b(?:will\s+)?(?:not\s+(?:be\s+)?return(?:ing)?|will\s+not\s+be\s+back|have\s+(?:departed|left)|(?:depart|leave|exit)s?\b)")
_c("subject", "retirement", r"\b(?:is|are)\s+retiring\b|\bretires?\b")
_c("subject", "loan_return", r"\breturn(?:s|ed)?\s+from\s+(?:his\s+)?loan\b")
_c("subject", "under_contract", r"\b(?:are|is)\s+(?:all\s+)?(?:currently\s+)?(?:under\s+contract|set\s+to\s+return|returning)\b")
_c("subject", "extension", r"\b(?:re-?sign(?:s|ed)?|extend(?:s|ed)?)\s+(?:with|his\s+contract|through)\b")
_c("subject", "signing", r"\b(?:sign(?:s|ed)?|join(?:s|ed)?|agree[sd]?\s+(?:to\s+)?(?:terms|a\s+deal))\s+(?:with|to|for)\b")

CLAUSE_SPLIT = re.compile(r",?\s+(?:while|whereas|but|as\s+well\s+as)\s+|;\s+|\s+[–—]\s+(?=\w+\s+(?:are|is)\b)", re.I)
REGION_END = re.compile(r"\s(?:to|from|through|until|ahead|following|after|as|who|that|which|for\s+the|for\s+20\d\d|"
                        r"on\s+(?:a|loan)|with\s+(?:the|an?)\b|in\s+20\d\d)\b", re.I)
SENT_SPLIT = re.compile(r"(?<=[a-z0-9\)\"”’])[.!?]\s+(?=[A-Z])")
PHOTO = re.compile(r"\|?\s*Photo\s+(?:courtesy|credit)[^\n|]*", re.I)

STAFF = re.compile(r"\b(head\s+coach|assistant\s+coach|goalkeeper\s+coach|sporting\s+director|general\s+manager|technical\s+director|"
                   r"club\s+president|\bceo\b|chief\s+\w+\s+officer|coaching\s+staff|names?\s+\w+(?:\s+\w+){0,3}\s+(?:head\s+)?coach)\b", re.I)
ROSTER_HEAD = re.compile(r"\b(?:roster\s+(?:decisions?|updates?|moves|changes|announcements?)|contract\s+option\s+decisions?|"
                         r"option\s+decisions?|player\s+(?:movement|transactions?)|transaction\s+tracker|who'?s\s+in,?\s+who'?s\s+out|"
                         r"offseason\s+(?:moves|roster))\b", re.I)
PERFORMANCE = re.compile(r"\b(?:voted|earns?|wins?|claims?|honou?red|player\s+of\s+the\s+(?:week|month|year)|team\s+of\s+the\s+"
                         r"(?:week|month|season|year)|hat[- ]trick|brace|golden\s+(?:boot|glove)|all[- ](?:league|conference)|"
                         r"finalists?|nominee|goal\s+of\s+the|save\s+of\s+the|young\s+player|defender\s+of\s+the|"
                         r"goalkeeper\s+of\s+the|rookie\s+of\s+the|scores?\s+(?:twice|brace)|man\s+of\s+the\s+match)\b", re.I)


@dataclass
class Mention:
    name: str
    event_type: str
    position: str | None = None
    quote: str = ""
    club_text: str | None = None
    extraction: str = "pattern"
    flags: list = field(default_factory=list)
    extra_clubs: list = field(default_factory=list)   # other database clubs named in the same paragraph
    article_clubs: list = field(default_factory=list)  # database clubs named anywhere else in the text


STAFF_BEFORE = re.compile(r"(?:coach|manager|director|president|chief|executive|ceo|chairman|owner|commissioner)\s*$", re.I)


def title_club(title: str, clubs=None, league_hint=None) -> str | None:
    """The club that makes the announcement, read from the headline: a database club if one is named, otherwise the
    capitalised words before the verb ("Nashville SC sign ...", "Charleston Battery's X voted ...")."""
    if clubs is not None:
        f = clubs.find_in_text(title, league_hint)
        if f:
            return f[0].name
    m = re.match(r"^\s*((?:[A-Z][\w.&\-]*\s+){0,3}[A-Z][\w.&\-]*)(?:’s|'s)\s", title + " ")
    if m:
        return m.group(1).strip()
    hits = [h for h in _cue_hits(title) if h[1] == "first"]          # "<Club> signs ...": the club comes first
    cut = min((h[2] for h in hits), default=None)
    m2 = re.search(r"\b(?:announces?|names?|reveals?|unveils?)\b", title, re.I)
    if m2 and (cut is None or m2.start() < cut):
        cut = m2.start()
    if cut:
        phrase = title[:cut].strip()
        toks = phrase.split()
        if (1 <= len(toks) <= 5 and "," not in phrase and not re.search(r"\band\b|&", phrase, re.I)
                and all(t[:1].isupper() or t.lower() in ("fc", "sc", "of", "de") for t in toks)):
            return phrase
    return None


def headline_type(title: str) -> str | None:
    """Main announcement type read from the headline alone (None: not a recruitment announcement)."""
    t = title or ""
    cues = _cue_hits(t)
    if cues:
        main = sorted(cues, key=lambda c: PRIORITY.index(c[0]) if c[0] in PRIORITY else 99)[0][0]
        if STAFF.search(t) and not re.search(r"\b(?:forward|midfielder|defender|goalkeeper|striker|winger|player)\b", t, re.I):
            return None
        return main
    if ROSTER_HEAD.search(t):
        return "club_roster_update"
    if PERFORMANCE.search(t):
        return "performance"
    return None


def _cue_hits(text: str):
    hits = []
    for typ, kind, rx in CUES:
        for m in rx.finditer(text):
            hits.append((typ, kind, m.start(), m.end()))
    return hits


def _clean_candidate(c: str) -> str | None:
    toks = [t.rstrip(".") if not re.fullmatch(INITIAL, t) else t for t in c.split()]
    while toks and (toks[0].lower().strip(".,") in DESCRIPTORS):
        toks.pop(0)
    while toks and toks[-1].lower().strip(".,") in DESCRIPTORS:
        toks.pop()
    if len(toks) < 2:
        return None
    return " ".join(toks)


def names_in(region: str, club_keys: set[str], club_tokens: set[str]) -> list[tuple[str, str | None]]:
    """Names in a list like 'goalkeeper Koke Vegas, midfielders Amos Shapiro-Thompson and Kevin Vang'.
    Returns (name, position group hint)."""
    region = re.sub(r"\([^)]*\)", " ", region)
    region = PHOTO.sub(" ", region)
    pos_marks = [(m.end(), POSITION_WORDS[m.group(0).lower()]) for m in re.finditer(
        r"\b(?:" + "|".join(sorted((re.escape(k) for k in POSITION_WORDS), key=len, reverse=True)) + r")\b", region, re.I)]
    out = []
    for m in NAME_RE.finditer(region):
        raw = m.group(0)
        name = _clean_candidate(raw)
        if not name:
            continue
        toks = [t.lower().strip(".,'’") for t in name.split()]
        if any(t in HARD_STOP for t in toks) or any(ch.isdigit() for ch in name):
            continue
        from .clubs import club_key
        ck = club_key(name)
        if ck in club_keys or (club_tokens and set(ck.split()) <= club_tokens):
            continue
        start = m.start() + raw.find(name.split()[0])
        if STAFF_BEFORE.search(region[max(0, start - 30):start]):
            continue                                        # "head coach Pascal Jansen" is staff, not a player
        pos = None
        for end, grp in pos_marks:
            if end <= start:
                pos = grp
        # a position word directly in front of the name takes priority; reset after a sentence style break
        out.append((name, pos))
    return out


def extract_mentions(text: str, clubs=None, league_hint: str | None = None, default_club: str | None = None) -> list[Mention]:
    """All player mentions with an announcement type, from a headline or article text. One mention per player:
    when an article says the same thing twice (exercised, then 'set to return'), the stronger cue is kept."""
    if not text:
        return []
    text = PHOTO.sub(" ", text)
    club_keys, club_tokens, found = set(), set(), []
    if clubs is not None:
        found = clubs.find_in_text(text, league_hint)
        club_keys = {c.key for c in found}
        for c in found:
            club_tokens |= set(c.key.split())
    mentions: list[Mention] = []
    for para in re.split(r"\n+", text):
        para_clubs = [c.name for c in clubs.find_in_text(para, league_hint)] if clubs is not None else []
        for sent in SENT_SPLIT.split(para):
            sent = sent.strip()
            if len(sent) < 12:
                continue
            for clause in CLAUSE_SPLIT.split(sent):
                hits = _cue_hits(clause)
                if not hits:
                    continue
                hits.sort(key=lambda h: (PRIORITY.index(h[0]) if h[0] in PRIORITY else 99, h[2]))
                used: list[tuple[int, int]] = []
                for typ, kind, s, e in hits:
                    if any(not (e <= a or s >= b) for a, b in used):
                        continue
                    used.append((s, e))
                    if kind == "first":
                        region = clause[e:]
                        mm = REGION_END.search(region)
                        if mm:
                            region = region[:mm.start()]
                    else:
                        region = clause[:s]
                    clubs_in_clause = clubs.find_in_text(clause, league_hint) if clubs is not None else []
                    for name, pos in names_in(region, club_keys | {c.key for c in clubs_in_clause}, club_tokens):
                        # the club that announces: one named in the clause, else the headline's, else the only club in the text
                        club_text = (clubs_in_clause[0].name if clubs_in_clause else
                                     default_club or (found[0].name if len(found) == 1 else None))
                        mt = Mention(name, typ, pos, sent[:320], club_text)
                        # other clubs in the same paragraph can corroborate who he is (for example where he played before)
                        mt.extra_clubs = [n for n in dict.fromkeys(para_clubs + [c.name for c in clubs_in_clause]) if n != mt.club_text][:4]
                        mt.article_clubs = [c.name for c in found if c.name != mt.club_text and c.name not in mt.extra_clubs][:6]
                        mentions.append(mt)
    best: dict[str, Mention] = {}
    for m in mentions:
        k = norm_name(m.name)
        cur = best.get(k)
        rank = PRIORITY.index(m.event_type) if m.event_type in PRIORITY else 99
        if cur is None or rank < (PRIORITY.index(cur.event_type) if cur.event_type in PRIORITY else 99):
            if cur is not None:
                m.extra_clubs = list(dict.fromkeys(m.extra_clubs + cur.extra_clubs))[:4]
                m.article_clubs = list(dict.fromkeys(m.article_clubs + cur.article_clubs))[:6]
            best[k] = m
        elif cur is not None:                     # the same statement twice (headline and body): keep the clubs of both
            cur.extra_clubs = list(dict.fromkeys(cur.extra_clubs + m.extra_clubs + ([m.club_text] if m.club_text and m.club_text != cur.club_text else [])))[:4]
    return list(best.values())
