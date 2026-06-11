"""Portfolio persistence and operations (buy/sell, rules, DCA, valuation).

Sells consume tax lots FIFO and split realized gains into short-term vs
long-term. A behavioral circuit breaker (see guardrails.py) can soft-lock
sells for a cooling-off period; pass override=True to bypass it explicitly.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..data.provider import DataProvider
from .models import (DCAPlan, Portfolio, Position, ProtectionRules,
                     RealizedSale, TaxLot, TradeRecord)

DEFAULT_PATH = Path("data") / "portfolio.json"


class SellLockedError(RuntimeError):
    """Raised when the behavioral circuit breaker is blocking sells."""


class PortfolioManager:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.portfolio = self._load()

    # ---------- persistence ----------

    def _load(self) -> Portfolio:
        if self.path.exists():
            return Portfolio.from_dict(json.loads(self.path.read_text()))
        return Portfolio()

    def save(self) -> None:
        self.portfolio.updated = datetime.utcnow().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.portfolio.to_dict(), indent=2))

    # ---------- positions ----------

    def get_position(self, symbol: str) -> Optional[Position]:
        symbol = symbol.upper()
        return next((p for p in self.portfolio.positions if p.symbol == symbol), None)

    def buy(self, symbol: str, quantity: float, price: float,
            thesis: str = "", invalidation: str = "",
            trade_date: Optional[str] = None) -> Position:
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        symbol = symbol.upper()
        trade_date = trade_date or date.today().isoformat()
        pos = self.get_position(symbol)
        if pos:
            pos.lots.append(TaxLot(date=trade_date, quantity=quantity,
                                   cost_per_share=price))
            pos.recompute_from_lots()
        else:
            pos = Position(symbol=symbol, quantity=quantity, avg_cost=price,
                           opened=trade_date,
                           lots=[TaxLot(date=trade_date, quantity=quantity,
                                        cost_per_share=price)])
            self.portfolio.positions.append(pos)
        if thesis:
            pos.thesis = thesis
        if invalidation:
            pos.invalidation = invalidation
        self.portfolio.trades.append(TradeRecord(symbol=symbol, side="buy",
                                                 quantity=quantity, price=price,
                                                 date=trade_date))
        self.save()
        return pos

    def sell(self, symbol: str, quantity: float, price: float,
             override: bool = False, trade_date: Optional[str] = None) -> RealizedSale:
        """FIFO sell. Returns realized P&L split into short/long-term.
        Raises SellLockedError while the circuit breaker is engaged
        (override=True bypasses it deliberately)."""
        g = self.portfolio.guardrail
        if g.is_locked() and not override:
            raise SellLockedError(
                f"Circuit breaker engaged until {g.locked_until} UTC — {g.reason} "
                "Re-read your investment theses; pass override=True / --override "
                "if you still want to sell.")
        pos = self.get_position(symbol)
        if not pos:
            raise ValueError(f"No position in {symbol}")
        if quantity > pos.quantity + 1e-9:
            raise ValueError(f"Cannot sell {quantity}, only hold {pos.quantity}")

        trade_date = trade_date or date.today().isoformat()
        remaining = quantity
        st_gain = lt_gain = 0.0
        consumed = 0
        for lot in list(pos.lots):                       # FIFO
            if remaining <= 1e-12:
                break
            take = min(lot.quantity, remaining)
            gain = (price - lot.cost_per_share) * take
            if lot.is_long_term(date.fromisoformat(trade_date)):
                lt_gain += gain
            else:
                st_gain += gain
            lot.quantity -= take
            remaining -= take
            consumed += 1
            if lot.quantity <= 1e-12:
                pos.lots.remove(lot)
        pos.recompute_from_lots()

        self.portfolio.cash += quantity * price
        self.portfolio.trades.append(TradeRecord(
            symbol=pos.symbol, side="sell", quantity=quantity, price=price,
            date=trade_date, short_term_gain=st_gain, long_term_gain=lt_gain))
        if pos.quantity <= 1e-9:
            self.portfolio.positions.remove(pos)
        self.save()
        return RealizedSale(symbol=symbol.upper(), quantity=quantity, price=price,
                            total=st_gain + lt_gain, short_term=st_gain,
                            long_term=lt_gain, lots_consumed=consumed)

    def set_rules(self, symbol: str, *, stop_loss: float | None = None,
                  stop_limit: float | None = None, take_profit: float | None = None,
                  trailing_stop_pct: float | None = None) -> Position:
        pos = self.get_position(symbol)
        if not pos:
            raise ValueError(f"No position in {symbol}")
        r = pos.rules
        if stop_loss is not None:
            r.stop_loss = stop_loss or None  # 0 clears the rule
        if stop_limit is not None:
            r.stop_limit = stop_limit or None
        if take_profit is not None:
            r.take_profit = take_profit or None
        if trailing_stop_pct is not None:
            r.trailing_stop_pct = trailing_stop_pct or None
            r.high_water_mark = None  # reset; will re-arm at current price
        self.save()
        return pos

    def set_thesis(self, symbol: str, thesis: str | None = None,
                   invalidation: str | None = None) -> Position:
        pos = self.get_position(symbol)
        if not pos:
            raise ValueError(f"No position in {symbol}")
        if thesis is not None:
            pos.thesis = thesis
        if invalidation is not None:
            pos.invalidation = invalidation
        self.save()
        return pos

    # ---------- DCA ----------

    def add_dca(self, symbol: str, amount: float, frequency: str = "monthly") -> DCAPlan:
        if amount <= 0:
            raise ValueError("DCA amount must be positive")
        plan = DCAPlan(symbol=symbol.upper(), amount=amount, frequency=frequency)
        self.portfolio.dca_plans = [p for p in self.portfolio.dca_plans
                                    if p.symbol != plan.symbol]
        self.portfolio.dca_plans.append(plan)
        self.save()
        return plan

    def remove_dca(self, symbol: str) -> None:
        self.portfolio.dca_plans = [p for p in self.portfolio.dca_plans
                                    if p.symbol != symbol.upper()]
        self.save()

    def execute_dca(self, plan: DCAPlan, price: float) -> Position:
        """Record a due DCA purchase at the given price and advance the schedule."""
        qty = plan.amount / price
        pos = self.buy(plan.symbol, qty, price)
        plan.advance()
        self.save()
        return pos

    # ---------- valuation ----------

    def valuation(self, provider: DataProvider) -> dict:
        rows = []
        total_value = self.portfolio.cash
        total_cost = 0.0
        for pos in self.portfolio.positions:
            try:
                q = provider.quote(pos.symbol)
                price = q.price
            except Exception:
                price = pos.avg_cost
            rows.append({
                "symbol": pos.symbol, "quantity": pos.quantity,
                "avg_cost": pos.avg_cost, "price": price,
                "value": pos.market_value(price), "pnl": pos.pnl(price),
                "pnl_pct": pos.pnl_pct(price),
                "stop_loss": pos.rules.stop_loss,
                "take_profit": pos.rules.take_profit,
                "trailing_stop_pct": pos.rules.trailing_stop_pct,
            })
            total_value += pos.market_value(price)
            total_cost += pos.cost_basis
        return {"rows": rows, "cash": self.portfolio.cash,
                "total_value": total_value, "total_cost": total_cost,
                "total_pnl": total_value - self.portfolio.cash - total_cost}
