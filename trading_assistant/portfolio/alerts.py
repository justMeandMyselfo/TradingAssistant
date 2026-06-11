"""Alert engine: evaluates protective rules and DCA schedules against
current prices and returns actionable alerts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..data.provider import DataProvider
from .manager import PortfolioManager


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    symbol: str
    kind: str         # stop_loss | stop_limit | take_profit | trailing_stop | dca_due | drawdown
    level: AlertLevel
    message: str
    price: float = 0.0


def check_alerts(manager: PortfolioManager, provider: DataProvider) -> list[Alert]:
    alerts: list[Alert] = []
    pf = manager.portfolio
    dirty = False

    for pos in pf.positions:
        try:
            price = provider.quote(pos.symbol).price
        except Exception:
            continue
        r = pos.rules

        if r.stop_loss is not None and price <= r.stop_loss:
            limit_note = ""
            if r.stop_limit is not None:
                limit_note = (f" Stop-limit: place a limit sell at {r.stop_limit:.2f}."
                              if price >= r.stop_limit else
                              f" Price has gapped below your {r.stop_limit:.2f} limit — "
                              "a stop-limit order would NOT fill; review manually.")
            alerts.append(Alert(pos.symbol, "stop_loss", AlertLevel.CRITICAL,
                                f"{pos.symbol} at {price:.2f} hit your stop-loss "
                                f"{r.stop_loss:.2f}.{limit_note}", price))

        if r.take_profit is not None and price >= r.take_profit:
            alerts.append(Alert(pos.symbol, "take_profit", AlertLevel.INFO,
                                f"{pos.symbol} at {price:.2f} reached your take-profit "
                                f"{r.take_profit:.2f} — consider locking in gains "
                                f"({pos.pnl_pct(price):+.1f}%).", price))

        if r.trailing_stop_pct is not None:
            if r.high_water_mark is None or price > r.high_water_mark:
                r.high_water_mark = price
                dirty = True
            trigger = r.high_water_mark * (1.0 - r.trailing_stop_pct)
            if price <= trigger:
                alerts.append(Alert(pos.symbol, "trailing_stop", AlertLevel.CRITICAL,
                                    f"{pos.symbol} at {price:.2f} fell "
                                    f"{r.trailing_stop_pct:.0%} from its high of "
                                    f"{r.high_water_mark:.2f} — trailing stop triggered.",
                                    price))

        # Unprotected large losers get a heads-up even without explicit rules.
        loss_pct = pos.pnl_pct(price)
        if loss_pct < -15 and r.stop_loss is None and r.trailing_stop_pct is None:
            alerts.append(Alert(pos.symbol, "drawdown", AlertLevel.WARNING,
                                f"{pos.symbol} is down {loss_pct:.1f}% with no stop "
                                "in place — consider adding one.", price))

    for plan in pf.dca_plans:
        if plan.is_due():
            alerts.append(Alert(plan.symbol, "dca_due", AlertLevel.INFO,
                                f"DCA due: invest {plan.amount:.2f} in {plan.symbol} "
                                f"({plan.frequency} plan).", 0.0))

    if dirty:
        manager.save()
    return alerts
