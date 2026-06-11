"""Alpaca execution: turn triggered alerts into real orders.

PAPER TRADING BY DEFAULT. The live endpoint is refused unless you have both
set "alpaca_allow_live": true in data/config.json AND constructed the client
with paper=False — two deliberate steps, no accidents.

Credentials: environment variables APCA_API_KEY_ID / APCA_API_SECRET_KEY,
or data/config.json keys alpaca_key / alpaca_secret (git-ignored).

Order mapping:
  stop_loss / trailing_stop alert → market sell of the whole position
                                    (limit sell at stop_limit if configured)
  take_profit alert               → market sell of the whole position
  dca_due alert                   → notional market buy of the plan amount

Nothing executes implicitly: only the `execute` CLI command, the web app
button, or `watch --execute` call into this module — and each run shows a
dry-run plan first unless explicitly told to send.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..notify import load_config
from ..portfolio.alerts import Alert
from ..portfolio.manager import PortfolioManager

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


def _to_alpaca_symbol(symbol: str) -> str:
    """Yahoo-style BTC-USD → Alpaca-style BTC/USD; equities unchanged."""
    return symbol.replace("-USD", "/USD") if symbol.endswith("-USD") else symbol


class AlpacaClient:
    def __init__(self, key: str, secret: str, paper: bool = True):
        if not key or not secret:
            raise AlpacaError(
                "Missing Alpaca credentials — set APCA_API_KEY_ID / "
                "APCA_API_SECRET_KEY or alpaca_key / alpaca_secret in "
                "data/config.json")
        if not paper and not load_config().get("alpaca_allow_live", False):
            raise AlpacaError(
                "Refusing LIVE trading: set \"alpaca_allow_live\": true in "
                "data/config.json first (and make sure you mean it).")
        self.base = PAPER_URL if paper else LIVE_URL
        self.paper = paper
        self._headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                         "Content-Type": "application/json",
                         "User-Agent": "TradingAssistant/1.0"}

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise AlpacaError(f"Alpaca {e.code} on {method} {path}: {detail}") from e

    # ---------- API ----------

    def account(self) -> dict:
        return self._request("GET", "/v2/account")

    def positions(self) -> list[dict]:
        return self._request("GET", "/v2/positions")  # type: ignore[return-value]

    def submit_order(self, symbol: str, side: str, qty: float | None = None,
                     notional: float | None = None, order_type: str = "market",
                     limit_price: float | None = None,
                     time_in_force: str = "day") -> dict:
        if (qty is None) == (notional is None):
            raise AlpacaError("Provide exactly one of qty or notional")
        body: dict = {"symbol": _to_alpaca_symbol(symbol), "side": side,
                      "type": order_type, "time_in_force": time_in_force}
        if qty is not None:
            body["qty"] = str(qty)
        else:
            body["notional"] = str(round(notional, 2))
        if order_type == "limit":
            if limit_price is None:
                raise AlpacaError("limit orders need limit_price")
            body["limit_price"] = str(limit_price)
        return self._request("POST", "/v2/orders", body)


def from_config(paper: bool = True) -> AlpacaClient:
    cfg = load_config()
    key = os.environ.get("APCA_API_KEY_ID") or cfg.get("alpaca_key", "")
    secret = os.environ.get("APCA_API_SECRET_KEY") or cfg.get("alpaca_secret", "")
    return AlpacaClient(key, secret, paper=paper)


# ---------- alert → order planning ----------

@dataclass
class PlannedOrder:
    symbol: str
    side: str                  # buy | sell
    reason: str                # which alert produced it
    qty: float | None = None
    notional: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    result: dict = field(default_factory=dict)   # filled in after submission

    def describe(self) -> str:
        amount = (f"{self.qty:g} sh" if self.qty is not None
                  else f"{self.notional:,.2f} notional")
        extra = f" @ {self.limit_price}" if self.limit_price else ""
        return f"{self.side.upper()} {self.symbol} {amount} ({self.order_type}{extra}) ← {self.reason}"


_SELL_KINDS = {"stop_loss", "trailing_stop", "take_profit"}


def plan_orders(manager: PortfolioManager, alerts: list[Alert]) -> list[PlannedOrder]:
    """Translate triggered alerts into a deterministic order plan (no I/O)."""
    plans: list[PlannedOrder] = []
    seen: set[tuple[str, str]] = set()
    for a in alerts:
        if a.kind in _SELL_KINDS:
            if (a.symbol, "sell") in seen:
                continue   # one exit per symbol even if multiple rules fired
            pos = manager.get_position(a.symbol)
            if not pos or pos.quantity <= 0:
                continue
            rules = pos.rules
            use_limit = a.kind == "stop_loss" and rules.stop_limit is not None
            plans.append(PlannedOrder(
                symbol=a.symbol, side="sell", reason=a.kind, qty=pos.quantity,
                order_type="limit" if use_limit else "market",
                limit_price=rules.stop_limit if use_limit else None))
            seen.add((a.symbol, "sell"))
        elif a.kind == "dca_due":
            plan = next((p for p in manager.portfolio.dca_plans
                         if p.symbol == a.symbol and p.is_due()), None)
            if plan:
                plans.append(PlannedOrder(symbol=a.symbol, side="buy",
                                          reason="dca_due", notional=plan.amount))
    return plans


def execute_plan(client: AlpacaClient, manager: PortfolioManager,
                 plans: list[PlannedOrder], provider=None) -> list[PlannedOrder]:
    """Submit planned orders and mirror the result into the local portfolio.

    Local bookkeeping uses the current quote as the assumed fill price (good
    enough for market orders; reconcile precisely later with `sync ibkr` or
    Alpaca's position endpoints)."""
    executed: list[PlannedOrder] = []
    for p in plans:
        p.result = client.submit_order(p.symbol, p.side, qty=p.qty,
                                       notional=p.notional,
                                       order_type=p.order_type,
                                       limit_price=p.limit_price)
        executed.append(p)
        price = None
        if provider is not None:
            try:
                price = provider.quote(p.symbol).price
            except Exception:
                price = None
        if p.side == "sell" and p.qty:
            if price:
                manager.sell(p.symbol, p.qty, price, override=True)
        elif p.side == "buy" and p.notional and price:
            dca = next((d for d in manager.portfolio.dca_plans
                        if d.symbol == p.symbol and d.is_due()), None)
            if dca:
                manager.execute_dca(dca, price)
            else:
                manager.buy(p.symbol, p.notional / price, price)
    return executed
