"""Behavioral guardrails.

Most retail underperformance is behavioral, not analytical: panic-selling
dips and chasing tops. These tools add friction exactly where it helps:

* Circuit breaker — if the portfolio drops hard within a short window,
  sells are soft-locked for a cooling-off period (overridable, but you have
  to do it consciously and re-read your own theses first).
* Panic-cost simulator — shows what selling at past dips would have cost,
  as a reality check before a fear-driven exit.

Config (data/config.json): cb_drop_pct (default 0.10), cb_window_days (7),
cb_lock_hours (24).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from .notify import load_config
from .portfolio.manager import PortfolioManager

DEFAULT_DROP_PCT = 0.10
DEFAULT_WINDOW_DAYS = 7
DEFAULT_LOCK_HOURS = 24


def settings() -> tuple[float, int, float]:
    cfg = load_config()
    return (float(cfg.get("cb_drop_pct", DEFAULT_DROP_PCT)),
            int(cfg.get("cb_window_days", DEFAULT_WINDOW_DAYS)),
            float(cfg.get("cb_lock_hours", DEFAULT_LOCK_HOURS)))


def record_snapshot(manager: PortfolioManager, total_value: float,
                    today: date | None = None) -> None:
    """Store one portfolio-value point per day (idempotent) and trim history."""
    today = today or date.today()
    hist = manager.portfolio.value_history
    hist[today.isoformat()] = round(total_value, 2)
    if len(hist) > 730:
        for key in sorted(hist)[:len(hist) - 730]:
            del hist[key]
    manager.save()


def check_circuit_breaker(manager: PortfolioManager,
                          now: datetime | None = None) -> str | None:
    """Engage the sell lock if the portfolio fell more than cb_drop_pct from
    its high of the last cb_window_days. Returns a message when engaged or
    already active, else None."""
    drop_pct, window_days, lock_hours = settings()
    now = now or datetime.utcnow()
    g = manager.portfolio.guardrail

    if g.is_locked(now):
        return (f"Cooling-off active until {g.locked_until} UTC — {g.reason}")

    hist = manager.portfolio.value_history
    if len(hist) < 2:
        return None
    cutoff = (now.date() - timedelta(days=window_days)).isoformat()
    window = {d: v for d, v in hist.items() if d >= cutoff}
    if len(window) < 2:
        return None
    peak_day, peak = max(window.items(), key=lambda kv: kv[1])
    current = window[max(window)]
    if peak <= 0:
        return None
    drawdown = current / peak - 1.0
    if drawdown <= -drop_pct:
        g.locked_until = (now + timedelta(hours=lock_hours)).isoformat()
        g.reason = (f"Portfolio fell {abs(drawdown):.1%} since {peak_day} "
                    f"(threshold {drop_pct:.0%} in {window_days}d). "
                    "Selling into panic locks in the loss.")
        manager.save()
        return (f"Circuit breaker ENGAGED for {lock_hours:.0f}h: {g.reason}")
    return None


def release_circuit_breaker(manager: PortfolioManager) -> None:
    g = manager.portfolio.guardrail
    g.locked_until = None
    g.reason = ""
    manager.save()


@dataclass
class DipOutcome:
    trough_date: str
    drawdown: float        # depth at the trough vs prior peak, negative
    recovery_return: float # trough → latest price, what a panic-seller forfeited


def panic_cost(close: pd.Series, n_dips: int = 3,
               min_depth: float = 0.05) -> list[DipOutcome]:
    """Find the deepest dips in the price history and compute what selling at
    each trough would have cost versus simply holding to today."""
    close = close.dropna()
    if len(close) < 40:
        return []
    running_max = close.cummax()
    dd = close / running_max - 1.0
    # Group consecutive days under the same peak; one trough per group.
    groups = (running_max != running_max.shift(1)).cumsum()
    outcomes: list[DipOutcome] = []
    last = float(close.iloc[-1])
    for _, idx in dd.groupby(groups).groups.items():
        seg = dd.loc[idx]
        trough_ts = seg.idxmin()
        depth = float(seg.min())
        if depth > -min_depth:
            continue
        trough_price = float(close.loc[trough_ts])
        outcomes.append(DipOutcome(
            trough_date=str(pd.Timestamp(trough_ts).date()),
            drawdown=depth,
            recovery_return=last / trough_price - 1.0))
    outcomes.sort(key=lambda o: o.drawdown)
    return outcomes[:n_dips]
