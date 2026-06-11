"""Accountability tracking: every advisor recommendation is logged with its
prices at the time, then continuously measured against simply buying SPY.

Most firms bury this comparison. Keeping it visible tells you whether the
engine actually earns its keep — and trains you to distrust advice (including
this tool's) that can't show a track record.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .data.provider import DataProvider

LOG_PATH = Path("data") / "advice_log.json"
BENCHMARK = "SPY"
MAX_ENTRIES = 200


def _load(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return []
    return []


def _save(entries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries[-MAX_ENTRIES:], indent=2))


def log_report(report, provider: DataProvider, path: Path = LOG_PATH) -> str:
    """Persist an AdviceReport snapshot. Returns the entry id."""
    try:
        bench_price = provider.quote(BENCHMARK).price
    except Exception:
        bench_price = 0.0
    entry = {
        "id": uuid.uuid4().hex[:8],
        "date": date.today().isoformat(),
        "profile": {"risk": report.profile.risk_tolerance,
                    "growth": report.profile.target_annual_growth,
                    "horizon": report.profile.horizon_years,
                    "capital": report.profile.capital,
                    "label": report.profile.label},
        "data_source": report.data_source,
        "benchmark_price": bench_price,
        "picks": [{"symbol": r.asset.symbol, "weight": r.weight,
                   "price": r.metrics.last_price, "amount": r.amount}
                  for r in report.picks],
    }
    entries = _load(path)
    entries.append(entry)
    _save(entries, path)
    return entry["id"]


@dataclass
class TrackedPerformance:
    entry_id: str
    date: str
    label: str
    capital: float
    portfolio_return: float    # since recommendation
    benchmark_return: float    # SPY over the same span
    alpha: float               # portfolio - benchmark
    n_picks: int


def performance(provider: DataProvider, path: Path = LOG_PATH) -> list[TrackedPerformance]:
    """Mark every logged recommendation to market vs the SPY benchmark."""
    out: list[TrackedPerformance] = []
    entries = _load(path)
    if not entries:
        return out
    try:
        bench_now = provider.quote(BENCHMARK).price
    except Exception:
        bench_now = 0.0

    for e in entries:
        rets = []
        for pick in e["picks"]:
            then = pick.get("price") or 0.0
            weight = pick.get("weight") or 0.0
            if then <= 0 or weight <= 0:
                continue
            try:
                now = provider.quote(pick["symbol"]).price
            except Exception:
                continue
            rets.append(weight * (now / then - 1.0))
        if not rets:
            continue
        port_ret = sum(rets)
        bench_then = e.get("benchmark_price") or 0.0
        bench_ret = (bench_now / bench_then - 1.0) if bench_then > 0 and bench_now > 0 else 0.0
        out.append(TrackedPerformance(
            entry_id=e["id"], date=e["date"],
            label=e.get("profile", {}).get("label", "?"),
            capital=float(e.get("profile", {}).get("capital", 0.0)),
            portfolio_return=port_ret, benchmark_return=bench_ret,
            alpha=port_ret - bench_ret, n_picks=len(e["picks"])))
    return out


def summary(perfs: list[TrackedPerformance]) -> dict:
    if not perfs:
        return {"count": 0, "beat_rate": 0.0, "avg_alpha": 0.0}
    beats = sum(1 for p in perfs if p.alpha > 0)
    return {"count": len(perfs),
            "beat_rate": beats / len(perfs),
            "avg_alpha": sum(p.alpha for p in perfs) / len(perfs)}
