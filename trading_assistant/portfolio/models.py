"""Portfolio domain models with JSON (de)serialization.

Positions are tracked at the tax-lot level (each purchase = one lot), which
enables FIFO cost accounting, short/long-term capital-gain splits and
tax-loss harvesting. Older portfolio files without lots load fine: a single
synthetic lot is created from the stored average cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional

LONG_TERM_DAYS = 365


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
class TaxLot:
    """A single purchase: the unit of tax accounting."""
    date: str                  # ISO purchase date
    quantity: float
    cost_per_share: float

    def age_days(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        return (today - date.fromisoformat(self.date)).days

    def is_long_term(self, today: Optional[date] = None) -> bool:
        return self.age_days(today) >= LONG_TERM_DAYS

    def days_to_long_term(self, today: Optional[date] = None) -> int:
        return max(0, LONG_TERM_DAYS - self.age_days(today))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaxLot":
        return cls(date=d["date"], quantity=float(d["quantity"]),
                   cost_per_share=float(d["cost_per_share"]))


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    opened: str = field(default_factory=lambda: date.today().isoformat())
    rules: ProtectionRules = field(default_factory=ProtectionRules)
    lots: list[TaxLot] = field(default_factory=list)
    thesis: str = ""           # why you bought (pre-commitment journal)
    invalidation: str = ""     # the condition under which you promised to sell

    def __post_init__(self) -> None:
        if not self.lots and self.quantity > 0:
            # Back-compat: synthesize one lot from the stored average cost.
            self.lots = [TaxLot(date=self.opened, quantity=self.quantity,
                                cost_per_share=self.avg_cost)]

    def recompute_from_lots(self) -> None:
        self.quantity = sum(l.quantity for l in self.lots)
        self.avg_cost = (sum(l.quantity * l.cost_per_share for l in self.lots)
                         / self.quantity) if self.quantity > 0 else 0.0

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
                "rules": self.rules.to_dict(),
                "lots": [l.to_dict() for l in self.lots],
                "thesis": self.thesis, "invalidation": self.invalidation}

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(symbol=d["symbol"], quantity=float(d["quantity"]),
                   avg_cost=float(d["avg_cost"]),
                   opened=d.get("opened", date.today().isoformat()),
                   rules=ProtectionRules.from_dict(d.get("rules", {})),
                   lots=[TaxLot.from_dict(l) for l in d.get("lots", [])],
                   thesis=d.get("thesis", ""),
                   invalidation=d.get("invalidation", ""))


@dataclass
class TradeRecord:
    """Executed trade, kept for wash-sale detection and tax reporting."""
    symbol: str
    side: str                  # buy | sell
    quantity: float
    price: float
    date: str = field(default_factory=lambda: date.today().isoformat())
    short_term_gain: float = 0.0   # realized, sells only
    long_term_gain: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        return cls(symbol=d["symbol"], side=d["side"],
                   quantity=float(d["quantity"]), price=float(d["price"]),
                   date=d.get("date", date.today().isoformat()),
                   short_term_gain=float(d.get("short_term_gain", 0.0)),
                   long_term_gain=float(d.get("long_term_gain", 0.0)))


@dataclass
class RealizedSale:
    """Outcome of a sell, split by holding period."""
    symbol: str
    quantity: float
    price: float
    total: float               # total realized P&L
    short_term: float
    long_term: float
    lots_consumed: int


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
class GuardrailState:
    """Behavioral circuit breaker: when the portfolio drops hard and fast,
    sells are soft-locked for a cooling-off period."""
    locked_until: Optional[str] = None   # ISO datetime
    reason: str = ""

    def is_locked(self, now: Optional[datetime] = None) -> bool:
        if not self.locked_until:
            return False
        now = now or datetime.utcnow()
        return datetime.fromisoformat(self.locked_until) > now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GuardrailState":
        return cls(locked_until=d.get("locked_until"), reason=d.get("reason", ""))


@dataclass
class Portfolio:
    positions: list[Position] = field(default_factory=list)
    dca_plans: list[DCAPlan] = field(default_factory=list)
    cash: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)
    value_history: dict[str, float] = field(default_factory=dict)  # ISO date → value
    guardrail: GuardrailState = field(default_factory=GuardrailState)
    updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {"positions": [p.to_dict() for p in self.positions],
                "dca_plans": [d.to_dict() for d in self.dca_plans],
                "cash": self.cash,
                "trades": [t.to_dict() for t in self.trades],
                "value_history": self.value_history,
                "guardrail": self.guardrail.to_dict(),
                "updated": self.updated}

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        return cls(positions=[Position.from_dict(p) for p in d.get("positions", [])],
                   dca_plans=[DCAPlan.from_dict(p) for p in d.get("dca_plans", [])],
                   cash=float(d.get("cash", 0.0)),
                   trades=[TradeRecord.from_dict(t) for t in d.get("trades", [])],
                   value_history=dict(d.get("value_history", {})),
                   guardrail=GuardrailState.from_dict(d.get("guardrail", {})),
                   updated=d.get("updated", datetime.utcnow().isoformat()))
