"""Canonical schema for the RIFC Player Translation Engine.

Everything the engine stores is expressed in these canonical fields. Provider
exports (Wyscout, FBref, StatsBomb, Hudl, custom sheets) are mapped onto them at
import time. Original files are preserved untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Identity / context fields for a player season row
# ---------------------------------------------------------------------------
PLAYER_ID_FIELDS = {
    "player_name": "Player name (required)",
    "provider_player_id": "Provider player ID, if the export has one",
    "birth_date": "Date of birth",
    "age": "Age (see 'age reference' import option)",
    "nationality": "Nationality / birth country",
    "position_raw": "Position as written by the provider (required)",
    "team": "Club for this row (required)",
    "league": "Competition (required, can be set for the whole file)",
    "season": "Season label, e.g. 2025 or 2024-25 (required, can be set for the whole file)",
    "minutes": "Minutes played (required)",
    "matches": "Matches / appearances",
    "starts": "Starts",
    "foot": "Preferred foot",
    "height_cm": "Height (cm)",
    "current_team": "Current club at export time (Wyscout 'Team'); blank = no club",
    "contract_expires": "Contract expiry date",
}

TEAM_FIELDS = {
    "team": "Club (required)",
    "league": "Competition (required, can be set for the whole file)",
    "season": "Season (required, can be set for the whole file)",
    "matches": "Matches played (required)",
    "goals_for": "Goals scored",
    "goals_against": "Goals conceded",
    "xg_for": "Expected goals for",
    "xg_against": "Expected goals against",
    "shots_for": "Shots",
    "possession_pct": "Average possession %",
    "penalties_awarded": "Penalties awarded (attempts)",
    "penalties_scored": "Penalty goals",
    "formation": "Primary formation",
}

TRANSFER_FIELDS = {
    "player_name": "Player name (required)",
    "birth_date": "Date of birth",
    "from_team": "Club left",
    "from_league": "League left (required)",
    "from_season": "Last season in the old league (required)",
    "to_team": "Club joined",
    "to_league": "League joined (required)",
    "to_season": "First season in the new league (required)",
    "transfer_date": "Transfer date",
    "transfer_type": "Permanent / loan / free",
}


# ---------------------------------------------------------------------------
# Canonical metrics
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str            # attacking / possession / defensive
    kind: str             # "rate" (count per 90) or "pct" (0 to 100)
    definition_stable: bool  # same meaning across data providers?
    positions: tuple = ()  # position groups where the metric is a primary indicator


ATT, POS_, DEF = "Attacking", "Possession", "Defensive"
ALL_OUTFIELD = ("CB", "FB", "DM", "CM", "AM", "W", "ST")

METRICS: dict[str, Metric] = {m.key: m for m in [
    Metric("goals", "Goals", ATT, "rate", True, ("AM", "W", "ST")),
    Metric("np_goals", "Non penalty goals", ATT, "rate", True, ("AM", "W", "ST")),
    Metric("xg", "Expected goals (xG)", ATT, "rate", False, ("AM", "W", "ST")),
    Metric("npxg", "Non penalty xG", ATT, "rate", False, ("AM", "W", "ST")),
    Metric("assists", "Assists", ATT, "rate", True, ("FB", "CM", "AM", "W", "ST")),
    Metric("xa", "Expected assists (xA)", ATT, "rate", False, ("FB", "CM", "AM", "W", "ST")),
    Metric("goal_contributions", "Goal contributions (G+A)", ATT, "rate", True, ("AM", "W", "ST")),
    Metric("shots", "Shots", ATT, "rate", True, ("AM", "W", "ST")),
    Metric("key_passes", "Key passes", ATT, "rate", False, ("FB", "CM", "AM", "W")),
    Metric("sca", "Shot creating actions", ATT, "rate", False, ("CM", "AM", "W", "ST")),
    Metric("prog_passes", "Progressive passes", POS_, "rate", False, ("CB", "FB", "DM", "CM", "AM")),
    Metric("prog_carries", "Progressive carries", POS_, "rate", False, ("FB", "CM", "AM", "W")),
    Metric("succ_dribbles", "Successful dribbles", POS_, "rate", False, ("FB", "AM", "W", "ST")),
    Metric("pass_pct", "Pass completion %", POS_, "pct", False, ("CB", "FB", "DM", "CM")),
    Metric("final_third_entries", "Final third entries", POS_, "rate", False, ("CB", "FB", "DM", "CM")),
    Metric("touches_att_box", "Touches in attacking box", POS_, "rate", False, ("AM", "W", "ST")),
    Metric("tackles", "Tackles", DEF, "rate", False, ("CB", "FB", "DM", "CM")),
    Metric("interceptions", "Interceptions", DEF, "rate", False, ("CB", "FB", "DM", "CM")),
    Metric("aerials_won", "Aerial duels won", DEF, "rate", False, ("CB", "DM", "ST")),
    Metric("ground_duels_won", "Ground duels won", DEF, "rate", False, ("CB", "FB", "DM", "CM")),
    Metric("recoveries", "Ball recoveries", DEF, "rate", False, ("CB", "FB", "DM", "CM")),
    # Added 6 Nov 2026 from columns already in the Wyscout exports (Phase 8 position views)
    Metric("passes_to_box", "Passes to penalty area", ATT, "rate", False, ("FB", "CM", "AM", "W")),
    Metric("crosses", "Crosses", POS_, "rate", False, ("FB", "W")),
    Metric("accurate_crosses", "Accurate crosses", POS_, "rate", False, ("FB", "W")),
    Metric("def_duels_won", "Defensive duels won", DEF, "rate", False, ("CB", "FB", "DM", "CM")),
    Metric("def_duel_win_pct", "Defensive duel success %", DEF, "pct", False, ("CB", "FB", "DM", "CM")),
    Metric("succ_def_actions", "Successful defensive actions", DEF, "rate", False, ("CB", "FB", "DM", "CM")),
    Metric("aerial_win_pct", "Aerial duel success %", DEF, "pct", False, ("CB", "ST")),
]}

# Supporting counts that are stored but not projected directly
SUPPORT_FIELDS = ["penalty_goals", "penalties_taken"]

# ---------------------------------------------------------------------------
# Position groups
# ---------------------------------------------------------------------------
POSITION_GROUPS = ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
POSITION_LABELS = {
    "GK": "Goalkeeper", "CB": "Centre back", "FB": "Fullback / wingback",
    "DM": "Defensive midfielder", "CM": "Central midfielder",
    "AM": "Attacking midfielder", "W": "Winger", "ST": "Striker",
}

# Provider position codes and common text -> group
_POSITION_MAP = {
    # Wyscout
    "GK": "GK", "CB": "CB", "LCB": "CB", "RCB": "CB", "LCB3": "CB", "RCB3": "CB",
    "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB", "LB5": "FB", "RB5": "FB",
    "DMF": "DM", "LDMF": "DM", "RDMF": "DM",
    "CMF": "CM", "LCMF": "CM", "RCMF": "CM", "LCMF3": "CM", "RCMF3": "CM",
    "AMF": "AM", "LAMF": "W", "RAMF": "W",
    "LW": "W", "RW": "W", "LWF": "W", "RWF": "W", "LM": "W", "RM": "W",
    "CF": "ST", "SS": "ST", "ST": "ST",
    # StatsBomb / generic
    "CDM": "DM", "CAM": "AM", "CM": "CM", "DM": "DM", "AM": "AM", "W": "W", "FB": "FB",
    "WB": "FB", "FW": "ST", "DF": "CB", "MF": "CM",
    # Words
    "GOALKEEPER": "GK", "KEEPER": "GK", "CENTRE BACK": "CB", "CENTER BACK": "CB",
    "CENTRAL DEFENDER": "CB", "LEFT BACK": "FB", "RIGHT BACK": "FB", "FULLBACK": "FB",
    "FULL BACK": "FB", "WINGBACK": "FB", "WING BACK": "FB",
    "DEFENSIVE MIDFIELDER": "DM", "DEFENSIVE MIDFIELD": "DM", "HOLDING MIDFIELDER": "DM",
    "CENTRAL MIDFIELDER": "CM", "CENTRAL MIDFIELD": "CM", "MIDFIELDER": "CM",
    "ATTACKING MIDFIELDER": "AM", "ATTACKING MIDFIELD": "AM", "NUMBER 10": "AM",
    "WINGER": "W", "LEFT WINGER": "W", "RIGHT WINGER": "W", "WIDE MIDFIELDER": "W",
    "LEFT MIDFIELDER": "W", "RIGHT MIDFIELDER": "W",
    "STRIKER": "ST", "CENTRE FORWARD": "ST", "CENTER FORWARD": "ST", "FORWARD": "ST",
    "DEFENDER": "CB",
}


def position_group(raw) -> str | None:
    """Map a provider position string to a position group.

    Multi position strings (Wyscout "CF, LWF", FBref "FW,MF") use the first
    listed position, which is the provider's primary position.
    """
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in {"NAN", "NONE"}:
        return None
    if s in POSITION_GROUPS:
        return s
    first = s.replace(";", ",").replace("/", ",").split(",")[0].strip()
    if first in _POSITION_MAP:
        return _POSITION_MAP[first]
    if s in _POSITION_MAP:
        return _POSITION_MAP[s]
    for word, grp in sorted(_POSITION_MAP.items(), key=lambda kv: -len(kv[0])):
        if len(word) > 3 and word in s:
            return grp
    return None


# ---------------------------------------------------------------------------
# Column aliases for auto detection.  Each canonical target maps to a list of
# (alias, unit) where unit is "total", "p90" or "pct".  Matching is done on a
# normalised header (lowercase, no punctuation).
# ---------------------------------------------------------------------------
@dataclass
class Alias:
    target: str
    unit: str = "value"   # value | total | p90 | pct
    names: list = field(default_factory=list)


PLAYER_ALIASES: list[Alias] = [
    Alias("player_name", "value", ["player", "player name", "name", "full name", "player_name"]),
    Alias("provider_player_id", "value", ["player id", "player_id", "id", "wyscout id", "playerid"]),
    Alias("birth_date", "value", ["birth date", "birthday", "date of birth", "dob", "born date"]),
    Alias("age", "value", ["age"]),
    Alias("nationality", "value", ["nation", "nationality", "birth country", "passport country", "country"]),
    Alias("position_raw", "value", ["position", "pos", "positions", "primary position", "role"]),
    Alias("team", "value", ["team within selected timeframe", "squad", "team", "club", "team name"]),
    Alias("league", "value", ["league", "competition", "comp", "competition name"]),
    Alias("season", "value", ["season", "season name", "year"]),
    Alias("minutes", "value", ["minutes played", "min", "minutes", "mins", "minutes_played"]),
    Alias("matches", "value", ["matches played", "mp", "apps", "appearances", "games", "matches"]),
    Alias("starts", "value", ["starts", "games started", "starting xi", "starting lineups"]),
    Alias("foot", "value", ["foot", "preferred foot"]),
    Alias("height_cm", "value", ["height"]),
    Alias("current_team", "value", ["current team", "current club", "team"]),
    Alias("contract_expires", "value", ["contract expires", "contract expiry", "contract until"]),
    # attacking
    Alias("goals", "total", ["goals", "gls"]),
    Alias("goals", "p90", ["goals per 90", "gls per 90", "goals p90", "goals/90"]),
    Alias("np_goals", "total", ["non penalty goals", "npg", "g pk", "gpk"]),
    Alias("np_goals", "p90", ["non penalty goals per 90", "npg per 90", "g pk per 90", "np goals per 90"]),
    Alias("xg", "total", ["xg", "expected goals"]),
    Alias("xg", "p90", ["xg per 90", "xg/90", "expected goals per 90"]),
    Alias("npxg", "total", ["npxg", "non penalty xg"]),
    Alias("npxg", "p90", ["npxg per 90", "np xg per 90", "non penalty xg per 90"]),
    Alias("assists", "total", ["assists", "ast"]),
    Alias("assists", "p90", ["assists per 90", "ast per 90", "assists/90"]),
    Alias("xa", "total", ["xa", "xag", "expected assists", "xassists"]),
    Alias("xa", "p90", ["xa per 90", "xag per 90", "expected assists per 90"]),
    Alias("goal_contributions", "total", ["g+a", "goals + assists", "ga"]),
    Alias("goal_contributions", "p90", ["g+a per 90", "goals + assists per 90"]),
    Alias("shots", "total", ["shots", "sh", "total shots"]),
    Alias("shots", "p90", ["shots per 90", "sh per 90", "sh/90", "shots/90"]),
    Alias("key_passes", "total", ["key passes", "kp"]),
    Alias("key_passes", "p90", ["key passes per 90", "kp per 90"]),
    Alias("sca", "total", ["sca", "shot creating actions"]),
    Alias("sca", "p90", ["sca90", "sca per 90", "shot creating actions per 90"]),
    Alias("penalty_goals", "total", ["pk", "penalty goals", "penalties scored", "penalty goals scored"]),
    Alias("penalties_taken", "total", ["pkatt", "penalties taken", "penalty attempts", "penalties attempted"]),
    # possession
    Alias("prog_passes", "total", ["prgp", "progressive passes"]),
    Alias("prog_passes", "p90", ["progressive passes per 90", "prgp per 90"]),
    Alias("prog_carries", "total", ["prgc", "progressive carries", "progressive runs"]),
    Alias("prog_carries", "p90", ["progressive carries per 90", "progressive runs per 90", "prgc per 90"]),
    Alias("succ_dribbles", "total", ["successful dribbles", "succ", "take ons won", "dribbles completed"]),
    Alias("succ_dribbles", "p90", ["successful dribbles per 90", "succ per 90", "take ons won per 90"]),
    Alias("pass_pct", "pct", ["accurate passes %", "accurate passes, %", "cmp%", "pass completion %",
                               "pass completion", "passing accuracy", "pass %", "pass accuracy"]),
    Alias("final_third_entries", "total", ["passes to final third", "1/3", "final third entries",
                                           "passes into final third", "final third passes"]),
    Alias("final_third_entries", "p90", ["passes to final third per 90", "final third entries per 90",
                                         "passes into final third per 90"]),
    Alias("touches_att_box", "total", ["touches in box", "att pen", "touches in penalty area",
                                       "touches att pen"]),
    Alias("touches_att_box", "p90", ["touches in box per 90", "touches in penalty area per 90"]),
    # defensive
    Alias("tackles", "total", ["tackles", "tkl", "tackles won", "tklw"]),
    Alias("tackles", "p90", ["tackles per 90", "tkl per 90"]),
    Alias("interceptions", "total", ["interceptions", "int"]),
    Alias("interceptions", "p90", ["interceptions per 90", "int per 90"]),
    Alias("aerials_won", "total", ["aerial duels won", "aerials won"]),
    Alias("aerials_won", "p90", ["aerial duels won per 90", "aerials won per 90"]),
    Alias("ground_duels_won", "total", ["ground duels won"]),
    Alias("ground_duels_won", "p90", ["ground duels won per 90"]),
    Alias("recoveries", "total", ["recoveries", "ball recoveries", "recov"]),
    Alias("recoveries", "p90", ["recoveries per 90", "ball recoveries per 90"]),
    Alias("passes_to_box", "p90", ["passes to penalty area per 90", "passes into penalty area per 90"]),
    Alias("crosses", "p90", ["crosses per 90"]),
    Alias("crosses", "total", ["crosses"]),
    Alias("def_duel_win_pct", "pct", ["defensive duels won %", "defensive duels won, %"]),
    Alias("succ_def_actions", "p90", ["successful defensive actions per 90"]),
    Alias("aerial_win_pct", "pct", ["aerial duels won %", "aerial duels won, %"]),
]

# Wyscout style "volume per 90" + "success %" pairs that let us derive a won/
# successful per 90 count. (volume header, pct header, canonical target)
DERIVED_SUCCESS_PAIRS = [
    ("aerial duels per 90", "aerial duels won %", "aerials_won"),
    ("dribbles per 90", "successful dribbles %", "succ_dribbles"),
    ("crosses per 90", "accurate crosses %", "accurate_crosses"),
    ("defensive duels per 90", "defensive duels won %", "def_duels_won"),
]

TEAM_ALIASES: list[Alias] = [
    Alias("team", "value", ["team", "squad", "club", "team name"]),
    Alias("league", "value", ["league", "competition", "comp"]),
    Alias("season", "value", ["season", "year"]),
    Alias("matches", "value", ["matches", "mp", "matches played", "games", "gp"]),
    Alias("goals_for", "value", ["goals", "gf", "goals for", "goals scored"]),
    Alias("goals_against", "value", ["ga", "goals against", "goals conceded", "conceded goals"]),
    Alias("xg_for", "value", ["xg", "xg for", "xgf", "expected goals"]),
    Alias("xg_against", "value", ["xga", "xg against", "expected goals against"]),
    Alias("shots_for", "value", ["shots", "sh", "total shots"]),
    Alias("possession_pct", "value", ["possession", "poss", "possession %", "possession, %"]),
    Alias("penalties_awarded", "value", ["pkatt", "penalties awarded", "penalties taken", "penalties"]),
    Alias("penalties_scored", "value", ["pk", "penalty goals", "penalties scored"]),
    Alias("formation", "value", ["formation", "system"]),
]

TRANSFER_ALIASES: list[Alias] = [
    Alias("player_name", "value", ["player", "player name", "name"]),
    Alias("birth_date", "value", ["birth date", "date of birth", "dob", "birthday"]),
    Alias("from_team", "value", ["from team", "from club", "left", "previous club", "from"]),
    Alias("from_league", "value", ["from league", "previous league", "from competition"]),
    Alias("from_season", "value", ["from season", "previous season", "season before"]),
    Alias("to_team", "value", ["to team", "to club", "joined", "new club", "to"]),
    Alias("to_league", "value", ["to league", "new league", "to competition"]),
    Alias("to_season", "value", ["to season", "new season", "season after", "season"]),
    Alias("transfer_date", "value", ["date", "transfer date"]),
    Alias("transfer_type", "value", ["type", "transfer type", "fee"]),
]

DATASET_TYPES = {
    "player_season": ("Player season stats (one row per player per team per season)", PLAYER_ALIASES),
    "team_season": ("Team season stats (one row per team per season)", TEAM_ALIASES),
    "transfer": ("Transfer list (one row per move)", TRANSFER_ALIASES),
}

PROVIDERS = ["Wyscout", "FBref", "StatsBomb", "Hudl", "Opta", "Club internal", "Other"]

USLC = "USL Championship"
RIFC_TEAM_HINTS = ("rhode island", "rifc")
