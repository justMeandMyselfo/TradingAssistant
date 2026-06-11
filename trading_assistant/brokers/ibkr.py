"""Interactive Brokers integration (official API via ib_async).

Requirements on the user's machine:
  1. IBKR account with TWS or the free IB Gateway running locally.
  2. API access enabled: TWS → Global Configuration → API → Settings →
     "Enable ActiveX and Socket Clients". Default ports: 7497 (TWS paper),
     7496 (TWS live), 4002 (Gateway paper), 4001 (Gateway live).

This is read-only: it pulls positions and average cost into the local
portfolio. No orders are ever placed.

Trade Republic note: TR has no official API. Use `import_positions_csv`
(brokers/csv_import.py) with an exported/manual CSV instead — unofficial
reverse-engineered TR clients exist but violate TR's terms of service and
require your real banking credentials, so they are deliberately not bundled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrokerPosition:
    symbol: str
    quantity: float
    avg_cost: float
    account: str = ""
    sec_type: str = "STK"


def _map_positions(raw_positions, contract_multiplier_default: float = 1.0) -> list[BrokerPosition]:
    """Convert ib_async Position objects (or anything shaped like them) into
    BrokerPosition records. Split out for testability without a live gateway."""
    out: list[BrokerPosition] = []
    for p in raw_positions:
        contract = p.contract
        qty = float(p.position)
        if qty == 0:
            continue
        # IBKR's avgCost includes the contract multiplier for derivatives.
        try:
            mult = float(getattr(contract, "multiplier", "") or contract_multiplier_default)
        except (TypeError, ValueError):
            mult = contract_multiplier_default
        avg_cost = float(p.avgCost) / (mult or 1.0)
        symbol = contract.symbol
        sec_type = getattr(contract, "secType", "STK")
        if sec_type == "CRYPTO":
            symbol = f"{symbol}-USD"   # match Yahoo's crypto ticker style
        out.append(BrokerPosition(symbol=symbol.upper(), quantity=qty,
                                  avg_cost=avg_cost,
                                  account=getattr(p, "account", ""),
                                  sec_type=sec_type))
    return out


def fetch_ibkr_positions(host: str = "127.0.0.1", port: int = 7497,
                         client_id: int = 17, timeout: float = 10.0) -> list[BrokerPosition]:
    """Connect to a locally running TWS/IB Gateway and read all positions."""
    try:
        from ib_async import IB
    except ImportError as e:
        raise RuntimeError("ib_async is not installed — pip install ib_async") from e

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        return _map_positions(ib.positions())
    finally:
        if ib.isConnected():
            ib.disconnect()


def sync_positions(manager, positions: list[BrokerPosition],
                   replace: bool = True) -> dict:
    """Write broker positions into the local portfolio.

    replace=True mirrors the broker exactly (protective rules on symbols that
    survive the sync are preserved); replace=False only adds/updates symbols
    present in the broker data.
    """
    from ..portfolio.models import Position

    pf = manager.portfolio
    existing_rules = {p.symbol: p.rules for p in pf.positions}
    incoming = {bp.symbol: bp for bp in positions}

    added, updated, removed = [], [], []
    if replace:
        for p in list(pf.positions):
            if p.symbol not in incoming:
                pf.positions.remove(p)
                removed.append(p.symbol)

    for sym, bp in incoming.items():
        pos = manager.get_position(sym)
        if pos:
            pos.quantity = bp.quantity
            pos.avg_cost = bp.avg_cost
            updated.append(sym)
        else:
            pos = Position(symbol=sym, quantity=bp.quantity, avg_cost=bp.avg_cost)
            if sym in existing_rules:
                pos.rules = existing_rules[sym]
            pf.positions.append(pos)
            added.append(sym)

    manager.save()
    return {"added": added, "updated": updated, "removed": removed}
