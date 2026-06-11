"""Market event awareness: earnings dates and the macro (FOMC) calendar.

Events feed the alert engine so risk rules become event-aware — e.g. a stop
sitting a few percent below price right before earnings is gap-bait: the
price can open far below the stop and a plain stop-market fills much lower.

FOMC dates are the Federal Reserve's published meeting schedule (the date
listed is the decision day). Always verify before trading around them; the
schedule occasionally changes and future years get added as the Fed
publishes them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .data.provider import DataProvider

# Decision days (second day) of scheduled FOMC meetings.
FOMC_DATES: list[date] = [
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
]


@dataclass
class MarketEvent:
    symbol: str          # "" for macro events
    kind: str            # earnings | fomc
    date: date
    note: str

    @property
    def days_away(self) -> int:
        return (self.date - date.today()).days


def upcoming_events(provider: DataProvider, symbols: list[str],
                    within_days: int = 14,
                    today: date | None = None) -> list[MarketEvent]:
    """Earnings for the given symbols plus macro events, within the window."""
    today = today or date.today()
    horizon = today + timedelta(days=within_days)
    out: list[MarketEvent] = []

    for sym in dict.fromkeys(s.upper() for s in symbols):
        try:
            for d in provider.earnings_dates(sym):
                if today <= d <= horizon:
                    out.append(MarketEvent(sym, "earnings", d,
                                           f"{sym} reports earnings"))
        except Exception:
            continue

    for d in FOMC_DATES:
        if today <= d <= horizon:
            out.append(MarketEvent("", "fomc", d,
                                   "FOMC rate decision (market-wide volatility)"))

    out.sort(key=lambda e: e.date)
    return out
