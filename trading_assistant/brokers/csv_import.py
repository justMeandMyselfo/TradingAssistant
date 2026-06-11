"""Generic CSV position import — the supported path for Trade Republic and
any broker without an official API.

Expected columns (case-insensitive, extra columns ignored, comma or
semicolon separated, European decimal commas handled):

    symbol , quantity , avg_cost

Accepted aliases: symbol/ticker/instrument, quantity/qty/shares/amount,
avg_cost/cost/price/buy_price/avg_price.

Example file:

    symbol;quantity;avg_cost
    AAPL;10;172,50
    VWCE.DE;25;104,90
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .ibkr import BrokerPosition, sync_positions

_ALIASES = {
    "symbol": {"symbol", "ticker", "instrument", "isin/ticker"},
    "quantity": {"quantity", "qty", "shares", "amount", "units"},
    "avg_cost": {"avg_cost", "cost", "price", "buy_price", "avg_price",
                 "average_cost", "averageprice"},
}


def _find_column(columns: list[str], target: str) -> str:
    wanted = _ALIASES[target]
    for c in columns:
        if c.strip().lower().replace(" ", "_") in wanted:
            return c
    raise ValueError(f"CSV is missing a '{target}' column "
                     f"(accepted names: {sorted(wanted)})")


def _to_float(value) -> float:
    if isinstance(value, str):
        value = value.strip().replace("€", "").replace("$", "").replace(" ", "")
        # European format: 1.234,56 → 1234.56
        if "," in value and (value.rfind(",") > value.rfind(".")):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    return float(value)


def read_positions_csv(path: str | Path) -> list[BrokerPosition]:
    df = pd.read_csv(path, sep=None, engine="python")  # sniffs , or ;
    cols = list(df.columns)
    c_sym = _find_column(cols, "symbol")
    c_qty = _find_column(cols, "quantity")
    c_cost = _find_column(cols, "avg_cost")

    out: list[BrokerPosition] = []
    for _, row in df.iterrows():
        sym = str(row[c_sym]).strip().upper()
        if not sym or sym == "NAN":
            continue
        qty = _to_float(row[c_qty])
        cost = _to_float(row[c_cost])
        if qty <= 0 or cost <= 0:
            continue
        out.append(BrokerPosition(symbol=sym, quantity=qty, avg_cost=cost,
                                  account="csv-import"))
    if not out:
        raise ValueError("No valid positions found in CSV")
    return out


def import_positions_csv(manager, path: str | Path, replace: bool = False) -> dict:
    """Read a CSV and merge it into the portfolio (replace=False adds/updates
    only; replace=True mirrors the file exactly)."""
    return sync_positions(manager, read_positions_csv(path), replace=replace)
