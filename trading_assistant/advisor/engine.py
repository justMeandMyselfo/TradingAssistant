"""Recommendation engine.

Scores every asset in the universe against the investor profile using
risk/return metrics computed from price history, then builds a concrete
allocation (with share counts, stop-loss and take-profit levels).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.indicators import atr
from ..analysis.metrics import AssetMetrics, compute_metrics
from ..analysis.portfolio_risk import (GoalSimulation, portfolio_volatility,
                                       shrink_return, simulate_goal)
from ..data.provider import DataProvider
from .profile import InvestorProfile
from .universe import UNIVERSE, Asset


@dataclass
class Recommendation:
    asset: Asset
    metrics: AssetMetrics
    score: float                 # 0..100
    weight: float = 0.0          # allocation weight, 0..1
    amount: float = 0.0          # dollars to invest
    shares: float = 0.0
    stop_loss: float = 0.0       # suggested protective stop price
    take_profit: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class AdviceReport:
    profile: InvestorProfile
    picks: list[Recommendation]
    expected_return: float       # weighted historical CAGR of the picks
    expected_volatility: float   # correlation-aware portfolio volatility
    target_feasible: bool
    target_comment: str
    data_source: str
    is_live_data: bool
    goal: GoalSimulation | None = None   # Monte Carlo probability of hitting the target


class AdvisorEngine:
    def __init__(self, provider: DataProvider):
        self.provider = provider

    # ---------- scoring ----------

    def _score(self, asset: Asset, m: AssetMetrics, p: InvestorProfile) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 50.0

        # 1. Risk fit: penalize distance between asset vol and the profile's band.
        vol_gap = abs(m.annual_volatility - p.target_volatility)
        risk_fit = max(0.0, 1.0 - vol_gap / 0.30)
        score += 20 * (risk_fit - 0.5)
        if vol_gap < 0.08:
            reasons.append(f"volatility {m.annual_volatility:.0%} sits right in your risk band")
        elif m.annual_volatility > p.target_volatility:
            reasons.append(f"volatility {m.annual_volatility:.0%} is above your comfort zone")

        # 2. Return vs target.
        if m.annual_return >= p.target_annual_growth:
            score += 12
            reasons.append(f"historical growth {m.annual_return:.0%}/yr meets your "
                           f"{p.target_annual_growth:.0%} target")
        else:
            score -= 8 * min(1.0, (p.target_annual_growth - m.annual_return) / 0.10)

        # 3. Risk-adjusted quality.
        score += max(-10.0, min(15.0, m.sharpe * 8))
        if m.sharpe > 0.8:
            reasons.append(f"strong risk-adjusted returns (Sharpe {m.sharpe:.2f})")

        # 4. Trend & momentum.
        if m.trend_above_sma200:
            score += 6
            reasons.append("trading above its 200-day average (uptrend)")
        else:
            score -= 6
        score += max(-6.0, min(8.0, m.momentum_6m * 25))

        # 5. Avoid chasing overbought names / catching falling knives.
        if m.rsi_14 > 75:
            score -= 8
            reasons.append(f"short-term overbought (RSI {m.rsi_14:.0f}) — consider DCA entry")
        elif m.rsi_14 < 30:
            score += 4
            reasons.append(f"short-term oversold (RSI {m.rsi_14:.0f}) — possible entry point")

        # 6. Drawdown tolerance: deep historical drawdowns hurt conservative profiles.
        dd_penalty = abs(m.max_drawdown) * (11 - p.risk_tolerance)
        score -= dd_penalty * 10

        # 7. Horizon: short horizons favor defensive assets.
        if p.horizon_years < 3 and asset.risk_bucket >= 4:
            score -= 10
            reasons.append("high-risk asset on a short horizon — sized down")
        if p.horizon_years >= 10 and asset.risk_bucket <= 2:
            score += 4

        # 8. Sector preferences.
        if asset.sector in p.preferred_sectors:
            score += 8
            reasons.append(f"matches your preferred sector ({asset.sector})")

        return max(0.0, min(100.0, score)), reasons

    # ---------- public API ----------

    def advise(self, profile: InvestorProfile, top_n: int = 8) -> AdviceReport:
        candidates: list[Recommendation] = []
        closes: dict[str, "pd.Series"] = {}  # noqa: F821 — for covariance/MC
        for asset in UNIVERSE:
            if asset.risk_bucket > profile.max_risk_bucket:
                continue
            if asset.sector in profile.excluded_sectors:
                continue
            if asset.asset_class == "crypto" and not profile.include_crypto:
                continue
            try:
                df = self.provider.history(asset.symbol, period="2y")
                m = compute_metrics(df["Close"])
            except Exception:
                continue
            score, reasons = self._score(asset, m, profile)
            rec = Recommendation(asset=asset, metrics=m, score=score, reasons=reasons)
            self._protective_levels(rec, df)
            candidates.append(rec)
            closes[asset.symbol] = df["Close"]

        candidates.sort(key=lambda r: r.score, reverse=True)
        picks = self._diversify(candidates, top_n)
        self._allocate(picks, profile)

        exp_ret = sum(r.weight * r.metrics.annual_return for r in picks)
        weights = {r.asset.symbol: r.weight for r in picks}
        try:
            exp_vol = portfolio_volatility(
                {s: closes[s] for s in weights}, weights)
        except Exception:  # fallback: naive weighted vol (upper bound)
            exp_vol = sum(r.weight * r.metrics.annual_volatility for r in picks)

        goal = None
        if picks and profile.capital > 0:
            goal = simulate_goal(
                capital=profile.capital, mu=shrink_return(exp_ret), sigma=exp_vol,
                horizon_years=profile.horizon_years,
                target_growth=profile.target_annual_growth)

        feasible, comment = profile.is_target_realistic()
        return AdviceReport(profile=profile, picks=picks,
                            expected_return=exp_ret, expected_volatility=exp_vol,
                            target_feasible=feasible, target_comment=comment,
                            data_source=self.provider.name,
                            is_live_data=self.provider.is_live, goal=goal)

    def _diversify(self, ranked: list[Recommendation], top_n: int) -> list[Recommendation]:
        """Greedy pick by score with caps per sector/asset-class so the
        portfolio isn't 8 flavors of the same bet."""
        picks: list[Recommendation] = []
        sector_count: dict[str, int] = {}
        class_count: dict[str, int] = {}
        for rec in ranked:
            if len(picks) >= top_n:
                break
            if sector_count.get(rec.asset.sector, 0) >= 2:
                continue
            if class_count.get(rec.asset.asset_class, 0) >= max(3, top_n // 2):
                continue
            picks.append(rec)
            sector_count[rec.asset.sector] = sector_count.get(rec.asset.sector, 0) + 1
            class_count[rec.asset.asset_class] = class_count.get(rec.asset.asset_class, 0) + 1
        return picks

    def _allocate(self, picks: list[Recommendation], profile: InvestorProfile) -> None:
        """Score-weighted allocation, inverse-vol tilted, capped per position."""
        if not picks:
            return
        raw = [r.score / max(r.metrics.annual_volatility, 0.05) for r in picks]
        total = sum(raw)
        weights = [w / total for w in raw]

        # Iteratively enforce the single-position cap.
        cap = profile.max_position_pct
        for _ in range(10):
            over = sum(max(0.0, w - cap) for w in weights)
            if over < 1e-9:
                break
            spare_idx = [i for i, w in enumerate(weights) if w < cap]
            if not spare_idx:
                break
            weights = [min(w, cap) for w in weights]
            room = sum(cap - weights[i] for i in spare_idx)
            for i in spare_idx:
                weights[i] += over * (cap - weights[i]) / room if room > 0 else 0.0

        scale = 1.0 / sum(weights)
        for rec, w in zip(picks, weights):
            rec.weight = w * scale
            rec.amount = round(rec.weight * profile.capital, 2)
            rec.shares = round(rec.amount / rec.metrics.last_price, 4) if rec.metrics.last_price else 0.0

    def _protective_levels(self, rec: Recommendation, df) -> None:
        """ATR-based stop-loss / take-profit suggestions (2x ATR stop, 2:1 reward)."""
        try:
            a = float(atr(df).iloc[-1])
        except Exception:
            a = rec.metrics.last_price * 0.02
        price = rec.metrics.last_price
        rec.stop_loss = round(max(0.01, price - 2.0 * a), 2)
        rec.take_profit = round(price + 4.0 * a, 2)
