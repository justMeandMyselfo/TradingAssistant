"""Portfolio domain models with JSON (de)serialization."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class ProtectionRules:
    """Risk-management rules attached to a position.

    stop_loss        — sell alert when price falls to this level.
    stop_limit       — optional limit price paired with the stop (stop-limit order).
    take_profit      — sell alert when price reaches this level.
    trailing_stop_pct — e.g. 0.10 = alert when price drops 10% from its high-water mark.
    high_water_mark  — highest price seen since the rule was set (auto-updated).
    """
    stop_loss: Optional[float] = None
    stop_limit: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    high_water_mark: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProtectionRules":
        return cls(**{k: d.get(k) for k in
                      ("stop_loss", "stop_limit", "take_profit",
                       "trailing_stop_pct", "high_water_mark")})


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    opened: str = field(default_factory=lambda: date.today().isoformat())
    rules: ProtectionRules = field(default_factory=ProtectionRules)

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_cost

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def pnl(self, price: float) -> float:
        return (price - self.avg_cost) * self.quantity

    def pnl_pct(self, price: float) -> float:
        return (price / self.avg_cost - 1.0) * 100.0 if self.avg_cost else 0.0

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "quantity": self.quantity,
                "avg_cost": self.avg_cost, "opened": self.opened,
                "rules": self.rules.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(symbol=d["symbol"], quantity=float(d["quantity"]),
                   avg_cost=float(d["avg_cost"]),
                   opened=d.get("opened", date.today().isoformat()),
                   rules=ProtectionRules.from_dict(d.get("rules", {})))


_FREQ_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30}


@dataclass
class DCAPlan:
    """Dollar-cost-averaging schedule for one symbol."""
    symbol: str
    amount: float                  # currency per purchase
    frequency: str = "monthly"     # daily | weekly | biweekly | monthly
    next_run: str = field(default_factory=lambda: date.today().isoformat())
    active: bool = True

    def is_due(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        return self.active and date.fromisoformat(self.next_run) <= today

    def advance(self, today: Optional[date] = None) -> None:
        today = today or date.today()
        step = timedelta(days=_FREQ_DAYS.get(self.frequency, 30))
        nxt = date.fromisoformat(self.next_run)
        while nxt <= today:
            nxt += step
        self.next_run = nxt.isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DCAPlan":
        return cls(symbol=d["symbol"], amount=float(d["amount"]),
                   frequency=d.get("frequency", "monthly"),
                   next_run=d.get("next_run", date.today().isoformat()),
                   active=bool(d.get("active", True)))


@dataclass
class Portfolio:
    positions: list[Position] = field(default_factory=list)
    dca_plans: list[DCAPlan] = field(default_factory=list)
    cash: float = 0.0
    updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {"positions": [p.to_dict() for p in self.positions],
                "dca_plans": [d.to_dict() for d in self.dca_plans],
                "cash": self.cash, "updated": self.updated}

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        return cls(positions=[Position.from_dict(p) for p in d.get("positions", [])],
                   dca_plans=[DCAPlan.from_dict(p) for p in d.get("dca_plans", [])],
                   cash=float(d.get("cash", 0.0)),
                   updated=d.get("updated", datetime.utcnow().isoformat()))
