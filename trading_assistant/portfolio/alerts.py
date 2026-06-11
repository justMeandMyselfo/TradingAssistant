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

    # Fetch prices once; reused by rules, guardrails and tax checks.
    prices: dict[str, float] = {}
    for pos in pf.positions:
        try:
            prices[pos.symbol] = provider.quote(pos.symbol).price
        except Exception:
            continue

    # Behavioral circuit breaker: snapshot today's value, engage on fast drops.
    from .. import guardrails
    if prices:
        total_value = pf.cash + sum(p.market_value(prices.get(p.symbol, p.avg_cost))
                                    for p in pf.positions)
        guardrails.record_snapshot(manager, total_value)
        cb_msg = guardrails.check_circuit_breaker(manager)
        if cb_msg:
            alerts.append(Alert("PORTFOLIO", "circuit_breaker", AlertLevel.CRITICAL,
                                cb_msg, total_value))

    for pos in pf.positions:
        price = prices.get(pos.symbol)
        if price is None:
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

        # Pre-commitment journal: remind the investor of their own exit rule.
        if loss_pct < -10 and pos.invalidation:
            alerts.append(Alert(pos.symbol, "thesis_check", AlertLevel.WARNING,
                                f"{pos.symbol} is down {loss_pct:.1f}%. Your own "
                                f"sell condition was: “{pos.invalidation}”. Has it "
                                "actually happened, or is this just volatility?",
                                price))

    for plan in pf.dca_plans:
        if plan.is_due():
            alerts.append(Alert(plan.symbol, "dca_due", AlertLevel.INFO,
                                f"DCA due: invest {plan.amount:.2f} in {plan.symbol} "
                                f"({plan.frequency} plan).", 0.0))

    # Tax-loss harvesting opportunities (top 3 to keep the noise down).
    from ..tax import harvest_opportunities
    try:
        for s in harvest_opportunities(manager, provider, prices=prices)[:3]:
            wash = f" ⚠ {s.warnings[0]}" if s.warnings else ""
            alts = f" Stay invested via {', '.join(s.replacements)}." if s.replacements else ""
            alerts.append(Alert(s.symbol, "tax_harvest", AlertLevel.INFO,
                                f"Harvestable loss on {s.symbol} lot {s.lot_date}: "
                                f"{s.unrealized_loss:,.2f} ({s.loss_pct:.1%}) — "
                                f"≈{s.est_tax_saving:,.2f} in tax savings.{alts}{wash}",
                                s.current_price))
    except Exception:
        pass

    if dirty:
        manager.save()
    return alerts
