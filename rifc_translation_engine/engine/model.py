"""Phase 2b: cross league translation model.

One model per metric, fitted only on paired before/after seasons.

Transformed scale
  rate metrics : t = log(per90 + c)    (c = small offset so zeros are allowed)
  % metrics    : t = logit(pct / 100)

Model (weighted ridge regression = Gaussian priors on coefficient blocks,
i.e. a hierarchical model with partial pooling):

  t_dst = intercept
        + b * t_src  + b_pos[g] * t_src  + b_rel * t_src * log(src minutes / 1800)   regression to the mean
        + pos[g]                                                                    position level
        + lg[dst] - lg[src]                                                         league effect (learned)
        + (lg_pos[dst, g] - lg_pos[src, g])                                         position specific league effect
        + pair[src -> dst]                                                          league pair deviation
        + age terms + team context terms + role term                               supporting covariates
        + noise

League effects are estimated jointly from every observed move, so a league
pair with few direct movers borrows strength from indirect paths, and effects
with little data are pulled toward zero (no adjustment) by the priors rather
than assumed. Nothing in this file contains a hand set league factor.

Noise variance depends on destination minutes (per 90 rates from fewer minutes
are noisier): var = s0 + k * 900 / minutes, fitted on leave one out residuals.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from scipy.stats import norm

from .config import ModelSettings
from .schema import METRICS

REF_AGE = 26.0

# Underlying numbers that may carry more signal than the outcome itself.
# Used only when both sides of the pair have them; validated separately in the backtest.
SUPPORT_METRICS = {           # options in order of preference; the first fully available one is used
    "goals": [["npxg"], ["xg"]], "np_goals": [["npxg"], ["xg"]], "assists": [["xa"]],
    "goal_contributions": [["npxg", "xa"], ["xg", "xa"]],
}


SUPPORT_LABELS = {"xg": "xG", "npxg": "npxG", "xa": "xA"}


def variant_label(key: str) -> str:
    from .schema import METRICS as _M
    base, _, sup = key.partition("+")
    lab = _M[base].label if base in _M else base
    if not sup:
        return lab
    return f"{lab}, using {' + '.join(SUPPORT_LABELS.get(x, x) for x in sup.split(','))}"


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------
@dataclass
class Transform:
    kind: str
    c: float = 0.0

    def fwd(self, v):
        v = np.asarray(v, dtype=float)
        if self.kind == "pct":
            return logit(np.clip(v / 100.0, 0.01, 0.99))
        return np.log(np.clip(v, 0, None) + self.c)

    def inv(self, t):
        t = np.asarray(t, dtype=float)
        if self.kind == "pct":
            return expit(t) * 100.0
        return np.clip(np.exp(t) - self.c, 0, None)


def make_transform(metric: str, reference_values: pd.Series) -> Transform:
    kind = METRICS[metric].kind
    if kind == "pct":
        return Transform("pct")
    v = reference_values.dropna()
    mean = float(v[v > 0].mean()) if (v > 0).any() else 0.1
    return Transform("rate", c=max(0.005, 0.25 * mean))


# ---------------------------------------------------------------------------
# Design matrix
# ---------------------------------------------------------------------------
@dataclass
class Design:
    leagues: list
    positions: list
    pairs: list
    use_context: bool
    use_role: bool
    t_center: float
    support: dict = field(default_factory=dict)   # metric -> (Transform, centre)
    columns: list = field(default_factory=list)
    blocks: list = field(default_factory=list)   # block name per column

    def build(self, df: pd.DataFrame, t_src: np.ndarray) -> np.ndarray:
        n = len(df)
        cols, names, blocks = [], [], []

        def add(vec, name, block):
            cols.append(np.asarray(vec, dtype=float))
            names.append(name)
            blocks.append(block)

        tc = t_src - self.t_center
        add(np.ones(n), "intercept", "intercept")
        add(tc, "slope", "slope")
        logmin = np.log(np.clip(df["src_minutes"].to_numpy(float), 90, None) / 1800.0)
        add(tc * logmin, "slope x log(src minutes)", "slope")
        pos = df["position_group"].to_numpy()
        for g in self.positions:
            ind = (pos == g).astype(float)
            add(ind, f"pos:{g}", "position")
            add(ind * tc, f"slope:{g}", "position_slope")
        src, dst = df["src_league"].to_numpy(), df["dst_league"].to_numpy()
        # Destination and source league effects are separate: the source effect
        # interacts with regression to the mean, so it is not simply minus the
        # destination effect.
        for L in self.leagues:
            add((dst == L).astype(float), f"dst:{L}", "league")
        for L in self.leagues:
            add((src == L).astype(float), f"src:{L}", "league")
        for L in self.leagues:
            for g in self.positions:
                add((dst == L) * (pos == g), f"dst_pos:{L}|{g}", "position_league")
                add((src == L) * (pos == g), f"src_pos:{L}|{g}", "position_league")
        for (a, b) in self.pairs:
            add(((src == a) & (dst == b)).astype(float), f"pair:{a}->{b}", "pair")
        for sm, (tr, ctr) in self.support.items():
            ts = tr.fwd(df[f"src_{sm}"].to_numpy(float)) - ctr
            add(ts, f"support:{sm}", "support")
            add(ts * logmin, f"support:{sm} x log(src minutes)", "support")
        age = df["age_dst"].to_numpy(float)
        a = np.where(np.isnan(age), 0.0, (age - REF_AGE) / 4.0)
        add(a, "age", "age")
        add(a ** 2, "age^2", "age")
        if self.use_context:
            add(np.nan_to_num(df["src_team_idx"].to_numpy(float)), "src team attack index", "context")
            add(np.nan_to_num(df["dst_team_idx"].to_numpy(float)), "dst team attack index", "context")
        if self.use_role:
            ss = df["dst_start_share"].to_numpy(float)
            add(np.where(np.isnan(ss), 0.0, ss - 0.75), "dst start share", "role")
        self.columns, self.blocks = names, blocks
        return np.column_stack(cols)


# ---------------------------------------------------------------------------
# Fitted model
# ---------------------------------------------------------------------------
@dataclass
class MetricModel:
    metric: str
    transform: Transform
    design: Design
    beta: np.ndarray
    A_inv: np.ndarray
    sigma2_bar: float
    s0: float
    k: float
    penalty_mult: float
    tuned: bool
    n_pairs: int
    n_cross: int
    cross_by_position: dict
    cross_by_league: dict
    league_edges: set
    train_dst_end_max: float
    loo_coverage80: float

    # ---- prediction -------------------------------------------------------
    def predict_t(self, df: pd.DataFrame, dst_minutes=None):
        t_src = self.transform.fwd(df[f"src_{self.metric}"].to_numpy(float))
        X = self.design.build(df, t_src)
        mu = X @ self.beta
        dm = df["dst_minutes"].to_numpy(float) if dst_minutes is None else np.broadcast_to(
            np.asarray(dst_minutes, float), (len(df),))
        param_var = self.sigma2_bar * np.einsum("ij,jk,ik->i", X, self.A_inv, X)
        noise_var = self.s0 + self.k * 900.0 / np.clip(dm, 90, None)
        return mu, param_var + noise_var, X, t_src

    def predict(self, df: pd.DataFrame, dst_minutes=None, levels=(0.5, 0.8)) -> pd.DataFrame:
        mu, var, _, _ = self.predict_t(df, dst_minutes)
        sd = np.sqrt(var)
        out = pd.DataFrame({"pred": self.transform.inv(mu)}, index=df.index)
        for lv in levels:
            z = norm.ppf(0.5 + lv / 2)
            out[f"lo{int(lv * 100)}"] = self.transform.inv(mu - z * sd)
            out[f"hi{int(lv * 100)}"] = self.transform.inv(mu + z * sd)
        out["t_mu"], out["t_sd"] = mu, sd
        return out

    def drivers(self, row: pd.DataFrame) -> pd.DataFrame:
        """Break the prediction into steps on the transformed scale.

        Returns rows (driver, change on transformed scale, cumulative value on the
        metric scale). 'Regression to the mean' collects the slope, intercept and
        position terms: what the same league history alone says.
        """
        mu, _, X, t_src = self.predict_t(row)
        contrib = X[0] * self.beta
        blocks = np.array(self.design.blocks)
        groups = [
            ("Regression to the mean (sample size, position norms)", {"intercept", "slope", "position", "position_slope"}),
            ("Underlying numbers (xG, xA)", {"support"}),
            ("League step (learned from movers)", {"league", "position_league", "pair"}),
            ("Age", {"age"}),
            ("Team attacking context", {"context"}),
            ("Role (start share)", {"role"}),
        ]
        steps, cur = [], float(t_src[0])
        steps.append(("Current per 90", 0.0, float(self.transform.inv(cur))))
        base = float(contrib[np.isin(blocks, list(groups[0][1]))].sum())
        delta0 = base - cur
        cur = base
        steps.append((groups[0][0], delta0, float(self.transform.inv(cur))))
        for name, bl in groups[1:]:
            d = float(contrib[np.isin(blocks, list(bl))].sum())
            if abs(d) < 1e-9 and not np.isin(blocks, list(bl)).any():
                continue
            cur += d
            steps.append((name, d, float(self.transform.inv(cur))))
        return pd.DataFrame(steps, columns=["driver", "change (model scale)", "value after step"])

    def league_effects(self) -> pd.DataFrame:
        """Destination effect = how the league shifts a player's output once he plays there.
        Source effect = how much output in that league is discounted when read as evidence.
        Both on the model scale (log for rate metrics), relative to zero, with standard errors."""
        cols = self.design.columns
        rows = []
        for L in self.design.leagues:
            i, j = cols.index(f"dst:{L}"), cols.index(f"src:{L}")
            var = np.diag(self.A_inv) * self.sigma2_bar
            rows.append({"league": L, "destination effect": self.beta[i], "dest se": np.sqrt(max(var[i], 0)),
                         "source effect": self.beta[j], "source se": np.sqrt(max(var[j], 0)),
                         "cross league pairs": self.cross_by_league.get(L, 0)})
        return pd.DataFrame(rows)

    def coefficients(self) -> pd.DataFrame:
        se = np.sqrt(np.clip(np.diag(self.A_inv) * self.sigma2_bar, 0, None))
        return pd.DataFrame({"term": self.design.columns, "block": self.design.blocks,
                             "coef": self.beta, "se": se})

    def connected(self, a: str, b: str) -> bool:
        if a == b:
            return True
        adj = {}
        for x, y in self.league_edges:
            adj.setdefault(x, set()).add(y)
            adj.setdefault(y, set()).add(x)
        seen, stack = {a}, [a]
        while stack:
            u = stack.pop()
            for v in adj.get(u, ()):
                if v == b:
                    return True
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        return False


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------
def _weights(df):
    a, b = df["src_minutes"].to_numpy(float), df["dst_minutes"].to_numpy(float)
    hm = 2 * a * b / (a + b)
    return np.clip(hm / 1800.0, 0.25, 1.5)


def _penalties(blocks, s: ModelSettings, mult: float):
    base = {"intercept": 0.0, "slope": s.alpha_slope, "position": s.alpha_position,
            "position_slope": s.alpha_position * 2, "league": s.alpha_league,
            "position_league": s.alpha_position_league, "pair": s.alpha_pair,
            "age": s.alpha_age, "context": s.alpha_context, "role": s.alpha_context,
            "support": s.alpha_slope}
    lam = np.array([base[b] * (mult if b != "intercept" else 1.0) for b in blocks])
    lam[lam == 0] = 1e-6
    return lam


def _solve(X, y, w, lam):
    XtW = X.T * w
    A = XtW @ X + np.diag(lam)
    A_inv = np.linalg.pinv(A)
    beta = A_inv @ (XtW @ y)
    return beta, A_inv


def _make_design(train: pd.DataFrame, t_src, settings: ModelSettings, support: dict | None = None):
    cross = train[train["pair_type"] == "cross"]
    leagues = sorted(set(train["src_league"]) | set(train["dst_league"]))
    positions = sorted(train["position_group"].dropna().unique())
    pc = cross.groupby(["src_league", "dst_league"]).size()
    pairs = [k for k, v in pc.items() if v >= 3]
    use_context = (train["src_team_idx"].notna() & train["dst_team_idx"].notna()).mean() >= 0.5
    use_role = train["dst_start_share"].notna().mean() >= 0.5
    sup = {}
    for sm, tr in (support or {}).items():
        sup[sm] = (tr, float(np.mean(tr.fwd(train[f"src_{sm}"].to_numpy(float)))))
    return Design(leagues, positions, pairs, bool(use_context), bool(use_role), float(np.mean(t_src)), sup)


def _fit_once(train, metric, transform, settings, mult, support=None):
    t_src = transform.fwd(train[f"src_{metric}"].to_numpy(float))
    y = transform.fwd(train[f"dst_{metric}"].to_numpy(float))
    design = _make_design(train, t_src, settings, support)
    X = design.build(train, t_src)
    w = _weights(train)
    lam = _penalties(design.blocks, settings, mult)
    beta, A_inv = _solve(X, y, w, lam)
    return design, X, y, w, beta, A_inv


def _cv_mult(pairs, metric, transform, settings, support=None):
    """Time based CV for the global penalty multiplier. Returns (mult, tuned)."""
    grid = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
    years = sorted({int(np.floor(v)) for v in pairs["dst_season_start"]})
    folds = []
    for Y in years[-4:]:
        train = pairs[pairs["dst_season_end"] < Y + 0.2]
        test = pairs[(np.floor(pairs["dst_season_start"]) == Y) & (pairs["pair_type"] == "cross")]
        if (train["pair_type"] == "cross").sum() >= settings.min_cross_league_pairs and len(test) >= 5:
            folds.append((train, test))
    if not folds:
        return 1.0, False
    scores = []
    for m in grid:
        err, wt = 0.0, 0.0
        for train, test in folds:
            design, *_rest = _fit_once(train, metric, transform, settings, m, support)
            beta, A_inv = _rest[3], _rest[4]
            ts = transform.fwd(test[f"src_{metric}"].to_numpy(float))
            Xt = design.build(test, ts)
            yt = transform.fwd(test[f"dst_{metric}"].to_numpy(float))
            wt_ = _weights(test)
            err += float(np.sum(wt_ * (yt - Xt @ beta) ** 2))
            wt += float(wt_.sum())
        scores.append(err / wt)
    return grid[int(np.argmin(scores))], True


def fit_metric(pairs: pd.DataFrame, metric: str, settings: ModelSettings,
               reference_values: pd.Series, tune: bool | None = None,
               support_refs: dict | None = None) -> MetricModel | None:
    """support_refs: {support metric: reference values}. Pairs missing a support
    value on the source side are dropped for the supported variant."""
    pairs = pairs[pairs["position_group"].notna()]
    support = None
    if support_refs:
        for sm in support_refs:
            pairs = pairs[pairs[f"src_{sm}"].notna()]
        support = {sm: make_transform(sm, ref) for sm, ref in support_refs.items()}
    pairs = pairs.reset_index(drop=True)
    if pairs.empty or (pairs["pair_type"] == "cross").sum() < 5:
        return None
    transform = make_transform(metric, reference_values)
    tune = settings.auto_tune if tune is None else tune
    mult, tuned = _cv_mult(pairs, metric, transform, settings, support) if tune else (1.0, False)
    design, X, y, w, beta, A_inv = _fit_once(pairs, metric, transform, settings, mult, support)

    # leave one out residuals (exact for weighted ridge)
    resid = y - X @ beta
    h = w * np.einsum("ij,jk,ik->i", X, A_inv, X)
    loo = resid / np.clip(1 - h, 0.05, None)
    dm = pairs["dst_minutes"].to_numpy(float)
    cross = (pairs["pair_type"] == "cross").to_numpy()
    use = cross if cross.sum() >= 20 else np.ones(len(pairs), bool)
    Z = np.column_stack([np.ones(use.sum()), 900.0 / dm[use]])
    coef, *_ = np.linalg.lstsq(Z, loo[use] ** 2, rcond=None)
    s0, k = float(coef[0]), float(coef[1])
    if k < 0 or s0 < 0:
        k = max(k, 0.0)
        s0 = max(float(np.mean(loo[use] ** 2) - k * np.mean(900.0 / dm[use])), 1e-4)
    sigma2_bar = float(np.sum(w * resid ** 2) / max(np.sum(w) - 1, 1))
    pv = sigma2_bar * np.einsum("ij,jk,ik->i", X, A_inv, X)
    sd = np.sqrt(pv + s0 + k * 900.0 / dm)
    cov80 = float(np.mean(np.abs(loo[use]) <= norm.ppf(0.9) * sd[use]))

    cp = pairs[cross]
    by_league = pd.concat([cp["src_league"], cp["dst_league"]]).value_counts().to_dict()
    edges = set(map(tuple, cp[["src_league", "dst_league"]].drop_duplicates().values.tolist()))
    return MetricModel(metric, transform, design, beta, A_inv, sigma2_bar, s0, k, mult, tuned,
                       len(pairs), int(cross.sum()), cp["position_group"].value_counts().to_dict(),
                       by_league, edges, float(pairs["dst_season_end"].max()), cov80)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
@dataclass
class Readiness:
    status: str              # 'insufficient' | 'unvalidated' | 'validated'
    reasons: list

    @property
    def can_project(self):
        return self.status != "insufficient"


def readiness(model: MetricModel | None, position: str, src_league: str, dst_league: str,
              settings: ModelSettings, validation: dict | None = None,
              min_position_pairs: int = 10) -> Readiness:
    reasons = []
    if model is None:
        return Readiness("insufficient", ["No translation model for this metric (not enough paired seasons)."])
    if model.n_cross < settings.min_cross_league_pairs:
        reasons.append(f"Only {model.n_cross} cross league pairs for this metric "
                       f"(minimum {settings.min_cross_league_pairs}).")
    npos = model.cross_by_position.get(position, 0)
    if npos < min_position_pairs:
        reasons.append(f"Only {npos} cross league pairs for {position} (minimum {min_position_pairs}).")
    for lg, label in ((src_league, "source"), (dst_league, "destination")):
        n = model.cross_by_league.get(lg, 0)
        if n < settings.min_league_pairs:
            reasons.append(f"{label.title()} league '{lg}' has {n} cross league pairs "
                           f"(minimum {settings.min_league_pairs}).")
    if src_league != dst_league and not model.connected(src_league, dst_league):
        reasons.append(f"No chain of observed moves links {src_league} to {dst_league}.")
    if reasons:
        return Readiness("insufficient", reasons)
    if not validation:
        return Readiness("unvalidated", ["Model fitted but not yet backtested. Run the backtest."])
    v = validation
    notes = []
    if v.get("n_test", 0) < settings.min_test_pairs_for_validation:
        notes.append(f"Backtest has {v.get('n_test', 0)} out of time pairs "
                     f"(need {settings.min_test_pairs_for_validation}).")
    else:
        if v["mae_model"] > v["mae_persistence"]:
            notes.append("Backtest: model error is worse than simply carrying the current per 90 over.")
        else:
            names = {"mae_pair_ratio": "a simple league pair conversion ratio",
                     "mae_dest_mean": "the target league positional average"}
            best = min(names, key=lambda k: v.get(k, np.inf))
            if v["mae_model"] > v[best] * (1 + getattr(settings, "baseline_tolerance", 0.05)):
                notes.append(f"Backtest: {names[best]} was {v['mae_model'] / v[best] - 1:.0%} more accurate, so "
                             "the player's own record adds little here at this sample size.")
        if abs(v["coverage80"] - 0.8) > settings.coverage_tolerance:
            notes.append(f"Backtest: 80% intervals covered {v['coverage80']:.0%} of outcomes (miscalibrated).")
    if notes:
        return Readiness("unvalidated", notes)
    return Readiness("validated", [f"Backtest on {v['n_test']} out of time pairs: MAE {v['mae_model']:.3f} vs "
                                   f"{v['mae_persistence']:.3f} carrying current per 90 over; 80% interval "
                                   f"coverage {v['coverage80']:.0%}."])
