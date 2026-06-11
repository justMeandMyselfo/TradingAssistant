"""Portfolio-level risk: correlation-aware volatility and Monte Carlo
goal-probability simulation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MARKET_PRIOR_RETURN = 0.07   # long-run equity-market prior used for shrinkage
SHRINKAGE = 0.5              # how far historical CAGR is pulled toward the prior


def portfolio_volatility(closes: dict[str, pd.Series], weights: dict[str, float]) -> float:
    """True annualized portfolio volatility from the daily-return covariance
    matrix (unlike a weighted sum of vols, this credits diversification)."""
    df = pd.DataFrame(closes).dropna()
    if df.shape[0] < 30 or df.shape[1] == 0:
        raise ValueError("Need >=30 overlapping observations")
    rets = df.pct_change().dropna()
    cov = rets.cov().values * TRADING_DAYS
    w = np.array([weights.get(c, 0.0) for c in df.columns])
    return float(np.sqrt(w @ cov @ w))


def shrink_return(historical_cagr: float) -> float:
    """Pull backward-looking CAGR toward a market prior so the simulation
    doesn't extrapolate a lucky past into the future."""
    return SHRINKAGE * historical_cagr + (1 - SHRINKAGE) * MARKET_PRIOR_RETURN


@dataclass
class GoalSimulation:
    probability: float            # P(final value >= target value)
    target_value: float
    expected_return_used: float   # after shrinkage
    volatility_used: float
    final_percentiles: dict[int, float]      # {10: v, 50: v, 90: v}
    percentile_curves: pd.DataFrame          # index = months, cols p10/p50/p90


def simulate_goal(capital: float, mu: float, sigma: float, horizon_years: float,
                  target_growth: float, n_paths: int = 5000,
                  seed: int = 42) -> GoalSimulation:
    """Monte Carlo GBM simulation of portfolio value over the horizon."""
    steps = max(1, int(round(horizon_years * 12)))
    dt = 1.0 / 12.0
    sigma = max(sigma, 1e-6)
    rng = np.random.default_rng(seed)
    shocks = rng.normal((mu - 0.5 * sigma**2) * dt, sigma * np.sqrt(dt),
                        size=(n_paths, steps))
    paths = capital * np.exp(np.cumsum(shocks, axis=1))
    paths = np.hstack([np.full((n_paths, 1), capital), paths])

    target_value = capital * (1 + target_growth) ** horizon_years
    final = paths[:, -1]
    probability = float((final >= target_value).mean())

    months = np.arange(steps + 1)
    curves = pd.DataFrame({
        "p10": np.percentile(paths, 10, axis=0),
        "p50": np.percentile(paths, 50, axis=0),
        "p90": np.percentile(paths, 90, axis=0),
    }, index=months)
    return GoalSimulation(
        probability=probability,
        target_value=float(target_value),
        expected_return_used=mu,
        volatility_used=sigma,
        final_percentiles={p: float(np.percentile(final, p)) for p in (10, 50, 90)},
        percentile_curves=curves,
    )
