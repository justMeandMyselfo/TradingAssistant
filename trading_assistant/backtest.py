"""Simple backtests: DCA vs lump-sum on historical prices."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .data.provider import DataProvider

_FREQ_DAYS = {"daily": 1, "weekly": 5, "biweekly": 10, "monthly": 21}


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    invested: float
    final_value: float
    total_return_pct: float
    num_purchases: int
    equity_curve: pd.Series   # portfolio value over time


def backtest_dca(provider: DataProvider, symbol: str, amount: float,
                 frequency: str = "monthly", period: str = "2y") -> BacktestResult:
    df = provider.history(symbol, period=period)
    closes = df["Close"]
    step = _FREQ_DAYS.get(frequency, 21)

    shares = 0.0
    invested = 0.0
    purchases = 0
    values = []
    for i, (ts, price) in enumerate(closes.items()):
        if i % step == 0:
            shares += amount / price
            invested += amount
            purchases += 1
        values.append(shares * price)
    curve = pd.Series(values, index=closes.index, name="value")
    final = float(curve.iloc[-1])
    return BacktestResult(symbol=symbol, strategy=f"DCA {frequency}",
                          invested=invested, final_value=final,
                          total_return_pct=(final / invested - 1) * 100 if invested else 0.0,
                          num_purchases=purchases, equity_curve=curve)


def backtest_lump_sum(provider: DataProvider, symbol: str, amount: float,
                      period: str = "2y") -> BacktestResult:
    df = provider.history(symbol, period=period)
    closes = df["Close"]
    shares = amount / float(closes.iloc[0])
    curve = closes * shares
    curve.name = "value"
    final = float(curve.iloc[-1])
    return BacktestResult(symbol=symbol, strategy="Lump sum",
                          invested=amount, final_value=final,
                          total_return_pct=(final / amount - 1) * 100 if amount else 0.0,
                          num_purchases=1, equity_curve=curve)
