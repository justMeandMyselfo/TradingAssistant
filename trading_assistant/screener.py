"""Small-cap screener — the structural retail edge.

A $50B fund cannot build a meaningful position in a $400M company without
moving the price; you can. This screener ranks a small-cap universe on
quality-momentum metrics and flags names whose dollar volume is low enough
that institutions are effectively locked out (the "size advantage" zone) —
while still enforcing a liquidity floor so *you* can get in and out.

The default universe is a curated, editable seed list. Pass your own symbols
(e.g. a Russell 2000 export) for broader coverage. Symbols that fail to load
are skipped silently, so a stale ticker in the list is harmless.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .analysis.metrics import compute_metrics
from .data.provider import DataProvider

# Curated small/mid-cap seed universe by sector. Editable — and meant to be.
DEFAULT_SMALL_CAP_UNIVERSE: list[str] = [
    # Technology / software
    "PLTK", "ALRM", "BMBL", "YELP", "PUBM", "DGII", "PRGS", "ALKT",
    # Industrials / manufacturing
    "MLI", "WTS", "GTLS", "AIN", "ROCK", "TRN", "B", "HURC",
    # Consumer
    "CAKE", "TXRH", "SHAK", "PLAY", "ETSY", "FIGS", "OLLI", "CROX",
    # Healthcare
    "MMSI", "OMCL", "IRTC", "NEOG", "PRCT", "TMDX",
    # Financials
    "PIPR", "VIRT", "WD", "AX", "CASH", "FHI",
    # Energy / materials
    "CEIX", "BTU", "SM", "TALO", "HCC",
    # Real estate / infra
    "STAG", "IIPR", "CTRE",
]

INSTITUTIONAL_ACCESS_THRESHOLD = 20_000_000   # avg daily $ volume; below this,
                                              # big funds struggle to build size
RETAIL_LIQUIDITY_FLOOR = 500_000              # below this even retail should pass


@dataclass
class ScreenHit:
    symbol: str
    score: float
    price: float
    market_cap: float | None
    avg_dollar_volume: float
    momentum_6m: float
    annual_return: float
    annual_volatility: float
    sharpe: float
    max_drawdown: float
    trend_above_sma200: bool
    institutional_locked_out: bool   # the edge flag
    notes: list[str] = field(default_factory=list)


def _score(m, dollar_vol: float, locked_out: bool) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 50.0

    # Quality momentum: reward sustained uptrends, not lottery tickets.
    score += max(-15.0, min(20.0, m.momentum_6m * 60))
    if m.trend_above_sma200:
        score += 10
        notes.append("uptrend (above 200-day average)")
    else:
        score -= 10
    score += max(-8.0, min(12.0, m.sharpe * 8))
    if m.sharpe > 0.8:
        notes.append(f"strong risk-adjusted returns (Sharpe {m.sharpe:.2f})")

    # Penalize chaos: extreme volatility and crash-prone charts.
    if m.annual_volatility > 0.60:
        score -= 12
        notes.append(f"very volatile ({m.annual_volatility:.0%}/yr)")
    score += min(0.0, (0.5 + m.max_drawdown) * 20)   # hits when DD worse than -50%

    # Mean-reversion guard: don't chase short-term spikes.
    if m.momentum_1m > 0.30:
        score -= 6
        notes.append(f"+{m.momentum_1m:.0%} in a month — consider waiting for a pullback")
    if m.rsi_14 > 75:
        score -= 5

    # The edge: institutions can't deploy here, you can.
    if locked_out:
        score += 8
        notes.append(f"≈{dollar_vol/1e6:.1f}M/day traded — too small for big funds "
                     "to enter (your structural edge)")
    return max(0.0, min(100.0, score)), notes


def screen(provider: DataProvider, symbols: list[str] | None = None,
           max_market_cap: float = 10e9,
           min_dollar_volume: float = RETAIL_LIQUIDITY_FLOOR,
           top: int = 15) -> list[ScreenHit]:
    """Rank the universe; returns the top hits sorted by score."""
    hits: list[ScreenHit] = []
    for sym in dict.fromkeys(s.upper() for s in (symbols or DEFAULT_SMALL_CAP_UNIVERSE)):
        try:
            df = provider.history(sym, period="1y")
            m = compute_metrics(df["Close"])
        except Exception:
            continue   # delisted/typo'd tickers are skipped, not fatal

        tail = df.tail(63)   # ~3 months
        dollar_vol = float((tail["Close"] * tail["Volume"]).mean())
        if dollar_vol < min_dollar_volume:
            continue   # too illiquid even for retail

        cap = provider.market_cap(sym)
        if cap is not None and cap > max_market_cap:
            continue   # not small-cap

        locked_out = dollar_vol < INSTITUTIONAL_ACCESS_THRESHOLD
        score, notes = _score(m, dollar_vol, locked_out)
        hits.append(ScreenHit(
            symbol=sym, score=score, price=m.last_price, market_cap=cap,
            avg_dollar_volume=dollar_vol, momentum_6m=m.momentum_6m,
            annual_return=m.annual_return, annual_volatility=m.annual_volatility,
            sharpe=m.sharpe, max_drawdown=m.max_drawdown,
            trend_above_sma200=m.trend_above_sma200,
            institutional_locked_out=locked_out, notes=notes))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top]
