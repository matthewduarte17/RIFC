"""SYNTHETIC TEST FIXTURES. NOT REAL DATA.

Generates fake league exports with known, planted league effects so the test
suite can check that the code recovers them. Player names are 'Test Player
0001' etc. Never import these files into the real database; the tests use a
throwaway database in a temp folder. Nothing here is a statement about any
real league, club or player.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

LEAGUES = {"Test League A": 0.0, "Test League B": 0.25, "Test League C": -0.20}  # log scale effects
SEASONS = list(range(2019, 2026))
POS = ["CB", "FB", "CM", "AM", "W", "ST"]
WYS_POS = {"CB": "LCB", "FB": "RB", "CM": "LCMF", "AM": "AMF", "W": "LWF", "ST": "CF"}
BASE = {"goals": {"CB": 0.05, "FB": 0.06, "CM": 0.1, "AM": 0.2, "W": 0.25, "ST": 0.45},
        "assists": {"CB": 0.03, "FB": 0.1, "CM": 0.1, "AM": 0.2, "W": 0.18, "ST": 0.12}}


def _letters(i):
    s = ""
    for _ in range(4):
        s = chr(97 + i % 26) + s
        i //= 26
    return s.title()


def generate(out_dir: Path, seed=7, n_players=900, move_prob=0.25):
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    players = []
    for i in range(n_players):
        pos = rng.choice(POS)
        players.append({"name": f"Testplayer {_letters(i)}", "pos": pos, "ability": rng.normal(0, 0.35),
                        "birth": int(rng.integers(1990, 2004)),
                        "league": rng.choice(list(LEAGUES)), "first": int(rng.choice(SEASONS[:4]))})
    rows = []
    for p in players:
        league = p["league"]
        for s in SEASONS:
            if s < p["first"]:
                continue
            if s > p["first"] and rng.random() < move_prob:
                league = rng.choice([L for L in LEAGUES if L != league])
            minutes = int(np.clip(rng.normal(1700, 700), 100, 3000))
            age = s - p["birth"]
            aging = -0.01 * (age - 26) ** 2 / 4
            r = {"Player": p["name"], "Team": f"{league} Club {hash((p['name'], league)) % 10}",
                 "Position": WYS_POS[p["pos"]], "Age": age, "Minutes played": minutes,
                 "Matches played": max(1, minutes // 80), "league": league, "season": s}
            for m in ("goals", "assists"):
                lam = BASE[m][p["pos"]] * np.exp(p["ability"] + LEAGUES[league] + aging) * minutes / 90
                r[m.title()] = int(rng.poisson(lam))
            rows.append(r)
    df = pd.DataFrame(rows)
    files = []
    for (lg, s), g in df.groupby(["league", "season"]):
        f = out_dir / f"{lg.replace(' ', '_')}_{s}.csv"
        g.drop(columns=["league", "season"]).to_csv(f, index=False)
        files.append((f, lg, str(s)))
    return files
