"""Curated investable universe with asset-class metadata.

risk_bucket: 1 (defensive) → 5 (speculative). Used by the advisor to match
assets to the investor's risk tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    asset_class: str   # equity | etf | bond | commodity | reit | crypto
    sector: str
    risk_bucket: int   # 1..5


UNIVERSE: list[Asset] = [
    # Broad market / core ETFs
    Asset("SPY", "S&P 500 ETF", "etf", "broad-market", 2),
    Asset("VTI", "Total US Market ETF", "etf", "broad-market", 2),
    Asset("QQQ", "Nasdaq-100 ETF", "etf", "technology", 3),
    Asset("VEA", "Developed Markets ex-US ETF", "etf", "international", 2),
    Asset("VWO", "Emerging Markets ETF", "etf", "international", 3),
    Asset("SCHD", "US Dividend Equity ETF", "etf", "dividend", 2),
    # Bonds / defensive
    Asset("AGG", "US Aggregate Bond ETF", "bond", "fixed-income", 1),
    Asset("BND", "Total Bond Market ETF", "bond", "fixed-income", 1),
    Asset("TLT", "20+ Year Treasury ETF", "bond", "fixed-income", 2),
    # Commodities / real assets
    Asset("GLD", "Gold ETF", "commodity", "precious-metals", 2),
    Asset("VNQ", "US Real Estate ETF", "reit", "real-estate", 3),
    # Mega-cap equities
    Asset("AAPL", "Apple", "equity", "technology", 3),
    Asset("MSFT", "Microsoft", "equity", "technology", 3),
    Asset("GOOGL", "Alphabet", "equity", "technology", 3),
    Asset("AMZN", "Amazon", "equity", "consumer", 3),
    Asset("NVDA", "NVIDIA", "equity", "technology", 4),
    Asset("META", "Meta Platforms", "equity", "technology", 4),
    Asset("TSLA", "Tesla", "equity", "automotive", 5),
    Asset("BRK-B", "Berkshire Hathaway", "equity", "financials", 2),
    Asset("JPM", "JPMorgan Chase", "equity", "financials", 3),
    Asset("V", "Visa", "equity", "financials", 2),
    Asset("JNJ", "Johnson & Johnson", "equity", "healthcare", 2),
    Asset("UNH", "UnitedHealth", "equity", "healthcare", 3),
    Asset("PG", "Procter & Gamble", "equity", "consumer-staples", 1),
    Asset("KO", "Coca-Cola", "equity", "consumer-staples", 1),
    Asset("WMT", "Walmart", "equity", "consumer-staples", 2),
    Asset("COST", "Costco", "equity", "consumer-staples", 2),
    Asset("XOM", "Exxon Mobil", "equity", "energy", 3),
    Asset("AMD", "AMD", "equity", "technology", 5),
    Asset("NFLX", "Netflix", "equity", "communication", 4),
    Asset("DIS", "Disney", "equity", "communication", 3),
    # Crypto (speculative sleeve)
    Asset("BTC-USD", "Bitcoin", "crypto", "crypto", 5),
    Asset("ETH-USD", "Ethereum", "crypto", "crypto", 5),
    Asset("SOL-USD", "Solana", "crypto", "crypto", 5),
]

BY_SYMBOL: dict[str, Asset] = {a.symbol: a for a in UNIVERSE}
