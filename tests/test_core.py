"""End-to-end tests for the core package, run against the offline provider."""
from datetime import date, timedelta

import pandas as pd
import pytest

from trading_assistant.advisor import AdvisorEngine, InvestorProfile
from trading_assistant.analysis.indicators import atr, bollinger, macd, rsi, sma
from trading_assistant.analysis.metrics import compute_metrics
from trading_assistant.backtest import backtest_dca, backtest_lump_sum
from trading_assistant.data.offline import OfflineProvider
from trading_assistant.portfolio import PortfolioManager, check_alerts
from trading_assistant.portfolio.models import DCAPlan


@pytest.fixture
def provider():
    return OfflineProvider()


@pytest.fixture
def manager(tmp_path):
    return PortfolioManager(tmp_path / "portfolio.json")


# ---------- data ----------

def test_history_shape_and_determinism(provider):
    df = provider.history("AAPL", period="1y")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 252
    assert (df["High"] >= df["Low"]).all()
    assert (df["Close"] > 0).all()
    df2 = OfflineProvider().history("AAPL", period="1y")
    pd.testing.assert_frame_equal(df, df2)  # deterministic


def test_quote_consistent_with_history(provider):
    q = provider.quote("MSFT")
    df = provider.history("MSFT", period="5d")
    assert q.price == pytest.approx(float(df["Close"].iloc[-1]))
    assert q.previous_close == pytest.approx(float(df["Close"].iloc[-2]))


# ---------- indicators & metrics ----------

def test_indicators(provider):
    df = provider.history("SPY", period="1y")
    close = df["Close"]
    assert sma(close, 20).iloc[-1] == pytest.approx(close.tail(20).mean())
    r = rsi(close)
    assert ((r >= 0) & (r <= 100)).all()
    m = macd(close)
    assert {"macd", "signal", "histogram"} == set(m.columns)
    b = bollinger(close)
    assert (b["upper"].dropna() >= b["lower"].dropna()).all()
    assert (atr(df).dropna() >= 0).all()


def test_metrics(provider):
    m = compute_metrics(provider.history("SPY", period="2y")["Close"])
    assert -1 < m.annual_return < 3
    assert 0 < m.annual_volatility < 2
    assert m.max_drawdown <= 0
    assert m.var_95 < 0
    assert 0 <= m.rsi_14 <= 100


# ---------- advisor ----------

def test_advise_conservative_excludes_high_risk(provider):
    report = AdvisorEngine(provider).advise(
        InvestorProfile(risk_tolerance=2, capital=10_000), top_n=6)
    assert report.picks
    assert all(r.asset.risk_bucket <= 1 for r in report.picks) or \
           all(r.asset.risk_bucket <= 2 for r in report.picks)
    assert all(r.asset.asset_class != "crypto" for r in report.picks)


def test_advise_allocation_sums_to_one(provider):
    profile = InvestorProfile(risk_tolerance=7, capital=50_000,
                              include_crypto=True, max_position_pct=0.25)
    report = AdvisorEngine(provider).advise(profile, top_n=8)
    total_weight = sum(r.weight for r in report.picks)
    assert total_weight == pytest.approx(1.0, abs=1e-6)
    assert all(r.weight <= 0.25 + 1e-6 for r in report.picks)
    assert sum(r.amount for r in report.picks) == pytest.approx(50_000, rel=0.01)
    for r in report.picks:
        assert 0 < r.stop_loss < r.metrics.last_price < r.take_profit


def test_excluded_sectors_respected(provider):
    report = AdvisorEngine(provider).advise(
        InvestorProfile(risk_tolerance=8, excluded_sectors=["technology"]))
    assert all(r.asset.sector != "technology" for r in report.picks)


# ---------- portfolio ----------

def test_buy_sell_avg_cost(manager):
    manager.buy("AAPL", 10, 100)
    manager.buy("AAPL", 10, 200)
    pos = manager.get_position("aapl")
    assert pos.quantity == 20
    assert pos.avg_cost == pytest.approx(150)
    sale = manager.sell("AAPL", 5, 180)
    assert sale.total == pytest.approx(400)  # FIFO: first lot cost 100, not avg 150
    assert manager.get_position("AAPL").quantity == 15
    with pytest.raises(ValueError):
        manager.sell("AAPL", 999, 100)


def test_persistence_roundtrip(manager, tmp_path):
    manager.buy("MSFT", 3, 300)
    manager.set_rules("MSFT", stop_loss=250, take_profit=400, trailing_stop_pct=0.10)
    manager.add_dca("VTI", 500, "monthly")
    reloaded = PortfolioManager(manager.path)
    pos = reloaded.get_position("MSFT")
    assert pos.rules.stop_loss == 250
    assert pos.rules.trailing_stop_pct == 0.10
    assert reloaded.portfolio.dca_plans[0].symbol == "VTI"


def test_alerts_stop_loss_and_take_profit(manager, provider):
    price = provider.quote("AAPL").price
    manager.buy("AAPL", 5, price)
    manager.set_rules("AAPL", stop_loss=price * 1.5)        # instantly breached
    alerts = check_alerts(manager, provider)
    assert any(a.kind == "stop_loss" and a.symbol == "AAPL" for a in alerts)

    manager.set_rules("AAPL", stop_loss=0, take_profit=price * 0.5)
    alerts = check_alerts(manager, provider)
    assert any(a.kind == "take_profit" for a in alerts)
    assert not any(a.kind == "stop_loss" for a in alerts)


def test_trailing_stop_high_water_mark(manager, provider):
    price = provider.quote("SPY").price
    manager.buy("SPY", 1, price)
    manager.set_rules("SPY", trailing_stop_pct=0.10)
    check_alerts(manager, provider)   # arms the high-water mark
    pos = manager.get_position("SPY")
    assert pos.rules.high_water_mark == pytest.approx(price)
    # Simulate a previous much-higher high → trigger
    pos.rules.high_water_mark = price * 2
    manager.save()
    alerts = check_alerts(manager, provider)
    assert any(a.kind == "trailing_stop" for a in alerts)


def test_dca_schedule(manager, provider):
    manager.add_dca("VTI", 250, "weekly")
    plan = manager.portfolio.dca_plans[0]
    assert plan.is_due()
    alerts = check_alerts(manager, provider)
    assert any(a.kind == "dca_due" for a in alerts)
    price = provider.quote("VTI").price
    manager.execute_dca(plan, price)
    assert not plan.is_due()
    assert date.fromisoformat(plan.next_run) > date.today()
    pos = manager.get_position("VTI")
    assert pos.quantity == pytest.approx(250 / price)


def test_dca_plan_advance_skips_to_future():
    plan = DCAPlan("SPY", 100, "monthly",
                   next_run=(date.today() - timedelta(days=95)).isoformat())
    plan.advance()
    assert date.fromisoformat(plan.next_run) > date.today()


# ---------- backtest ----------

def test_backtests(provider):
    dca = backtest_dca(provider, "SPY", 500, "monthly", "2y")
    assert dca.num_purchases == 24
    assert dca.invested == pytest.approx(500 * 24)
    assert len(dca.equity_curve) == 504
    lump = backtest_lump_sum(provider, "SPY", dca.invested, "2y")
    assert lump.invested == dca.invested
    assert lump.final_value > 0
