"""Risk/return metrics computed from a daily Close price history."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252
RISK_FREE_RATE = 0.04  # annual, used in Sharpe/Sortino


@dataclass
class AssetMetrics:
    annual_return: float       # CAGR over the sample
    annual_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float        # negative number, e.g. -0.32
    var_95: float              # 1-day 95% historical VaR (negative)
    momentum_6m: float         # 6-month price return
    momentum_1m: float
    rsi_14: float
    trend_above_sma200: bool
    last_price: float


def compute_metrics(close: pd.Series, rsi_series: pd.Series | None = None) -> AssetMetrics:
    close = close.dropna()
    if len(close) < 30:
        raise ValueError("Need at least 30 data points to compute metrics")

    rets = close.pct_change().dropna()
    years = len(close) / TRADING_DAYS
    annual_return = float((close.iloc[-1] / close.iloc[0]) ** (1 / max(years, 1e-9)) - 1)
    annual_vol = float(rets.std() * np.sqrt(TRADING_DAYS))

    excess = rets.mean() * TRADING_DAYS - RISK_FREE_RATE
    sharpe = float(excess / annual_vol) if annual_vol > 0 else 0.0
    downside = rets[rets < 0]
    downside_vol = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = float(excess / downside_vol) if downside_vol > 0 else 0.0

    running_max = close.cummax()
    max_dd = float((close / running_max - 1).min())
    var_95 = float(np.percentile(rets, 5))

    def window_return(days: int) -> float:
        if len(close) <= days:
            return float(close.iloc[-1] / close.iloc[0] - 1)
        return float(close.iloc[-1] / close.iloc[-days - 1] - 1)

    if rsi_series is None:
        from .indicators import rsi as _rsi
        rsi_series = _rsi(close)
    sma200 = close.rolling(min(200, len(close) // 2)).mean()

    return AssetMetrics(
        annual_return=annual_return,
        annual_volatility=annual_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        var_95=var_95,
        momentum_6m=window_return(126),
        momentum_1m=window_return(21),
        rsi_14=float(rsi_series.iloc[-1]),
        trend_above_sma200=bool(close.iloc[-1] > sma200.iloc[-1]) if not np.isnan(sma200.iloc[-1]) else True,
        last_price=float(close.iloc[-1]),
    )
