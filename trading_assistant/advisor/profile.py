"""Investor profile: who is investing and what they want."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InvestorProfile:
    risk_tolerance: int = 5          # 1 (very conservative) .. 10 (very aggressive)
    target_annual_growth: float = 0.08   # e.g. 0.08 = 8%/year
    horizon_years: float = 5.0
    capital: float = 10_000.0
    max_position_pct: float = 0.25   # cap any single asset's allocation
    include_crypto: bool = False
    excluded_sectors: list[str] = field(default_factory=list)
    preferred_sectors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.risk_tolerance = int(min(10, max(1, self.risk_tolerance)))
        self.target_annual_growth = max(0.0, float(self.target_annual_growth))
        self.horizon_years = max(0.25, float(self.horizon_years))
        self.capital = max(0.0, float(self.capital))
        self.max_position_pct = min(1.0, max(0.05, float(self.max_position_pct)))

    @property
    def max_risk_bucket(self) -> int:
        """Highest universe risk bucket (1-5) this investor should touch."""
        # tolerance 1-2 → bucket 1 ... tolerance 9-10 → bucket 5
        return (self.risk_tolerance + 1) // 2

    @property
    def target_volatility(self) -> float:
        """Annualized volatility band center the portfolio should aim for."""
        return 0.05 + 0.035 * self.risk_tolerance  # 8.5% .. 40%

    @property
    def label(self) -> str:
        if self.risk_tolerance <= 2:
            return "Very conservative"
        if self.risk_tolerance <= 4:
            return "Conservative"
        if self.risk_tolerance <= 6:
            return "Balanced"
        if self.risk_tolerance <= 8:
            return "Growth"
        return "Aggressive"

    def is_target_realistic(self) -> tuple[bool, str]:
        """Sanity-check growth target vs risk tolerance."""
        # Rough mapping of risk tolerance to plausible long-run return.
        plausible = 0.03 + 0.022 * self.risk_tolerance
        if self.target_annual_growth <= plausible:
            return True, (f"A {self.target_annual_growth:.0%} annual target is consistent "
                          f"with a {self.label.lower()} profile.")
        return False, (f"A {self.target_annual_growth:.0%} annual target is ambitious for a "
                       f"{self.label.lower()} profile (historically ~{plausible:.0%} is more "
                       f"typical). Expect to either accept more risk or a longer horizon.")
