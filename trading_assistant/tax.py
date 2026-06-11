"""Tax-aware tooling: loss-harvesting suggestions, wash-sale detection and
realized-gain reporting.

Tax rates are configurable (data/config.json: tax_short_rate / tax_long_rate)
because they depend on your country and bracket. Defaults: 30% short-term,
15% long-term. This is bookkeeping support, not tax advice — confirm rules
for your jurisdiction (e.g. the 30-day wash-sale window is a US rule; some
countries have none, others use different windows).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .advisor.universe import BY_SYMBOL, UNIVERSE
from .data.provider import DataProvider
from .notify import load_config
from .portfolio.manager import PortfolioManager

WASH_SALE_DAYS = 30
DEFAULT_SHORT_RATE = 0.30
DEFAULT_LONG_RATE = 0.15


def tax_rates() -> tuple[float, float]:
    cfg = load_config()
    return (float(cfg.get("tax_short_rate", DEFAULT_SHORT_RATE)),
            float(cfg.get("tax_long_rate", DEFAULT_LONG_RATE)))


@dataclass
class HarvestSuggestion:
    symbol: str
    lot_date: str
    quantity: float
    cost_per_share: float
    current_price: float
    unrealized_loss: float        # negative
    loss_pct: float               # negative
    is_long_term: bool
    days_to_long_term: int
    est_tax_saving: float
    replacements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _replacement_candidates(symbol: str, max_n: int = 2) -> list[str]:
    """Similar-but-not-identical assets to stay invested after harvesting
    (same sector & asset class, different ticker)."""
    asset = BY_SYMBOL.get(symbol)
    if not asset:
        return []
    return [a.symbol for a in UNIVERSE
            if a.symbol != symbol and a.sector == asset.sector
            and a.asset_class == asset.asset_class][:max_n]


def _wash_sale_warnings(manager: PortfolioManager, symbol: str,
                        today: date | None = None) -> list[str]:
    today = today or date.today()
    cutoff = today - timedelta(days=WASH_SALE_DAYS)
    warnings: list[str] = []

    recent_buys = [t for t in manager.portfolio.trades
                   if t.symbol == symbol and t.side == "buy"
                   and date.fromisoformat(t.date) >= cutoff]
    if recent_buys:
        warnings.append(f"You bought {symbol} within the last {WASH_SALE_DAYS} days "
                        f"({len(recent_buys)} purchase(s)) — selling at a loss now "
                        "would likely be a wash sale (loss disallowed).")

    if any(pl.symbol == symbol and pl.active for pl in manager.portfolio.dca_plans):
        warnings.append(f"An active DCA plan keeps buying {symbol} — pause it for "
                        f"{WASH_SALE_DAYS} days around the sale or the loss is washed.")
    return warnings


def harvest_opportunities(manager: PortfolioManager, provider: DataProvider,
                          min_loss_pct: float = 0.05, min_loss_abs: float = 100.0,
                          prices: dict[str, float] | None = None,
                          today: date | None = None) -> list[HarvestSuggestion]:
    """Per-lot unrealized losses worth harvesting, with estimated tax savings,
    wash-sale warnings and replacement candidates."""
    short_rate, long_rate = tax_rates()
    today = today or date.today()
    out: list[HarvestSuggestion] = []

    for pos in manager.portfolio.positions:
        if prices and pos.symbol in prices:
            price = prices[pos.symbol]
        else:
            try:
                price = provider.quote(pos.symbol).price
            except Exception:
                continue
        wash = _wash_sale_warnings(manager, pos.symbol, today)
        for lot in pos.lots:
            loss = (price - lot.cost_per_share) * lot.quantity
            loss_pct = price / lot.cost_per_share - 1.0 if lot.cost_per_share else 0.0
            if loss >= 0:
                continue
            if abs(loss) < min_loss_abs and abs(loss_pct) < min_loss_pct:
                continue
            is_lt = lot.is_long_term(today)
            rate = long_rate if is_lt else short_rate
            out.append(HarvestSuggestion(
                symbol=pos.symbol, lot_date=lot.date, quantity=lot.quantity,
                cost_per_share=lot.cost_per_share, current_price=price,
                unrealized_loss=loss, loss_pct=loss_pct, is_long_term=is_lt,
                days_to_long_term=lot.days_to_long_term(today),
                est_tax_saving=abs(loss) * rate,
                replacements=_replacement_candidates(pos.symbol),
                warnings=list(wash)))
    out.sort(key=lambda s: s.unrealized_loss)   # deepest loss first
    return out


@dataclass
class GainCheck:
    """Pre-sale check: warns when a gain is about to be realized short-term."""
    short_term_gain: float
    long_term_gain: float
    almost_long_term: list[tuple[str, int, float]]  # (lot date, days left, gain)


def preview_sale(manager: PortfolioManager, symbol: str, quantity: float,
                 price: float, today: date | None = None) -> GainCheck:
    """Simulate a FIFO sale without executing it, to surface tax impact."""
    pos = manager.get_position(symbol)
    if not pos:
        raise ValueError(f"No position in {symbol}")
    today = today or date.today()
    remaining = quantity
    st = lt = 0.0
    almost: list[tuple[str, int, float]] = []
    for lot in pos.lots:
        if remaining <= 1e-12:
            break
        take = min(lot.quantity, remaining)
        gain = (price - lot.cost_per_share) * take
        if lot.is_long_term(today):
            lt += gain
        else:
            st += gain
            days_left = lot.days_to_long_term(today)
            if gain > 0 and days_left <= 45:
                almost.append((lot.date, days_left, gain))
        remaining -= take
    return GainCheck(short_term_gain=st, long_term_gain=lt, almost_long_term=almost)


def realized_summary(manager: PortfolioManager, year: int | None = None) -> dict:
    year = year or date.today().year
    st = lt = 0.0
    n = 0
    for t in manager.portfolio.trades:
        if t.side != "sell" or date.fromisoformat(t.date).year != year:
            continue
        st += t.short_term_gain
        lt += t.long_term_gain
        n += 1
    short_rate, long_rate = tax_rates()
    est_tax = max(0.0, st) * short_rate + max(0.0, lt) * long_rate
    return {"year": year, "sales": n, "short_term": st, "long_term": lt,
            "total": st + lt, "est_tax": est_tax,
            "short_rate": short_rate, "long_rate": long_rate}
