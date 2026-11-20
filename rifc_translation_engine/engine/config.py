"""Engine settings. Thresholds are deliberately conservative and visible in the UI.

Change them here or in the Translation Model page; every model run stores the
settings it used so results stay reproducible.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

ROOT = Path(os.environ.get("RIFC_TE_HOME", Path(__file__).resolve().parent.parent))
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # untouched copies of every uploaded file
MODEL_DIR = DATA_DIR / "models"
DB_PATH = DATA_DIR / "engine.duckdb"

for _d in (DATA_DIR, RAW_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass
class ModelSettings:
    # Pair construction
    min_minutes_each_side: int = 450        # both seasons of a pair need this many minutes
    max_gap_years: float = 0.9              # time between end of source and start of destination season
    include_within_league_pairs: bool = True  # same league, next season: informs regression to the mean and aging
    allow_cross_provider_for_stable_metrics: bool = True  # goals, assists, shots only

    # Readiness gates (projection is refused below these)
    min_cross_league_pairs: int = 30        # per metric and position group
    min_league_pairs: int = 8               # cross league pairs touching the source league (and the destination league)
    min_test_pairs_for_validation: int = 20 # out of time pairs required to call a model validated
    coverage_tolerance: float = 0.10        # 80% interval must cover between 70% and 90% in backtest
    baseline_tolerance: float = 0.05        # model MAE may be at most 5% worse than the best simple baseline

    # Regularisation (ridge penalties, i.e. Gaussian priors on coefficient blocks)
    alpha_slope: float = 1.0
    alpha_position: float = 2.0
    alpha_league: float = 4.0
    alpha_position_league: float = 20.0
    alpha_pair: float = 30.0
    alpha_age: float = 5.0
    alpha_context: float = 5.0
    auto_tune: bool = True                  # pick a global penalty multiplier with time based CV

    # Intervals
    interval_levels: tuple = (0.5, 0.8)

    # Percentiles / benchmarks
    benchmark_min_minutes: int = 900

    def to_dict(self):
        return asdict(self)


DEFAULT_SETTINGS = ModelSettings()
