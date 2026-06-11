"""Tests for tax-lot accounting, loss harvesting, wash sales and guardrails."""
from datetime import date, datetime, timedelta

import pytest

from trading_assistant import guardrails
from trading_assistant.brokers.ibkr import BrokerPosition, sync_positions
from trading_assistant.data.offline import OfflineProvider
from trading_assistant.portfolio import PortfolioManager, check_alerts
from trading_assistant.portfolio.manager import SellLockedError
from trading_assistant.portfolio.models import Portfolio, Position
from trading_assistant.tax import (harvest_opportunities, preview_sale,
                                   realized_summary)


@pytest.fixture
def provider():
    return OfflineProvider()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Isolate config/state files for guardrail + tax settings.
    from trading_assistant import notify
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    return PortfolioManager(tmp_path / "portfolio.json")


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# ---------- tax lots ----------

def test_fifo_and_st_lt_split(manager):
    manager.buy("AAPL", 10, 100, trade_date=_days_ago(400))   # long-term lot
    manager.buy("AAPL", 10, 200, trade_date=_days_ago(30))    # short-term lot
    sale = manager.sell("AAPL", 15, 250)
    # FIFO: 10 LT @100 → +1500 LT; 5 ST @200 → +250 ST
    assert sale.long_term == pytest.approx(1500)
    assert sale.short_term == pytest.approx(250)
    assert sale.lots_consumed == 2
    pos = manager.get_position("AAPL")
    assert pos.quantity == pytest.approx(5)
    assert pos.avg_cost == pytest.approx(200)   # only the newer lot remains


def test_legacy_portfolio_without_lots_loads(tmp_path):
    legacy = {"positions": [{"symbol": "MSFT", "quantity": 4.0, "avg_cost": 300.0,
                             "opened": _days_ago(500), "rules": {}}],
              "dca_plans": [], "cash": 0.0}
    path = tmp_path / "portfolio.json"
    import json
    path.write_text(json.dumps(legacy))
    mgr = PortfolioManager(path)
    pos = mgr.get_position("MSFT")
    assert len(pos.lots) == 1
    assert pos.lots[0].is_long_term()
    sale = mgr.sell("MSFT", 4, 350)
    assert sale.long_term == pytest.approx(200)
    assert sale.short_term == 0


def test_preview_sale_flags_almost_long_term(manager):
    manager.buy("NVDA", 10, 100, trade_date=_days_ago(330))  # LT in 35 days
    check = preview_sale(manager, "NVDA", 10, 150)
    assert check.short_term_gain == pytest.approx(500)
    assert len(check.almost_long_term) == 1
    assert check.almost_long_term[0][1] == 35


def test_realized_summary(manager):
    manager.buy("KO", 10, 100, trade_date=_days_ago(400))
    manager.sell("KO", 10, 90)   # -100 long-term loss
    s = realized_summary(manager)
    assert s["sales"] == 1
    assert s["long_term"] == pytest.approx(-100)
    assert s["est_tax"] == 0.0   # losses don't owe tax


# ---------- harvesting & wash sales ----------

def test_harvest_opportunity_with_replacement(manager, provider):
    price = provider.quote("AAPL").price
    manager.buy("AAPL", 10, price * 1.5, trade_date=_days_ago(100))  # deep loss
    suggestions = harvest_opportunities(manager, provider)
    assert suggestions
    s = suggestions[0]
    assert s.symbol == "AAPL"
    assert s.unrealized_loss < 0
    assert s.est_tax_saving > 0
    assert not s.is_long_term
    assert "AAPL" not in s.replacements
    assert s.replacements   # other technology equities exist in the universe
    # Bought 100 days ago → no wash-sale warning.
    assert not s.warnings


def test_wash_sale_warnings_recent_buy_and_dca(manager, provider):
    price = provider.quote("MSFT").price
    manager.buy("MSFT", 5, price * 2, trade_date=_days_ago(10))  # recent buy
    manager.add_dca("MSFT", 100, "monthly")
    suggestions = harvest_opportunities(manager, provider)
    assert suggestions
    warnings = " ".join(suggestions[0].warnings)
    assert "wash sale" in warnings
    assert "DCA" in warnings


# ---------- guardrails ----------

def test_circuit_breaker_engages_and_blocks_sells(manager):
    manager.buy("SPY", 10, 100, trade_date=_days_ago(50))
    guardrails.record_snapshot(manager, 10_000, today=date.today() - timedelta(days=3))
    guardrails.record_snapshot(manager, 8_500, today=date.today())  # -15% in 3d
    msg = guardrails.check_circuit_breaker(manager)
    assert msg and "ENGAGED" in msg
    assert manager.portfolio.guardrail.is_locked()

    with pytest.raises(SellLockedError):
        manager.sell("SPY", 1, 90)
    sale = manager.sell("SPY", 1, 90, override=True)   # conscious override works
    assert sale.quantity == 1

    guardrails.release_circuit_breaker(manager)
    assert not manager.portfolio.guardrail.is_locked()
    manager.sell("SPY", 1, 90)   # unlocked again


def test_circuit_breaker_quiet_on_normal_moves(manager):
    guardrails.record_snapshot(manager, 10_000, today=date.today() - timedelta(days=3))
    guardrails.record_snapshot(manager, 9_800, today=date.today())  # -2%
    assert guardrails.check_circuit_breaker(manager) is None
    assert not manager.portfolio.guardrail.is_locked()


def test_snapshot_idempotent_per_day(manager):
    guardrails.record_snapshot(manager, 100.0)
    guardrails.record_snapshot(manager, 200.0)
    assert list(manager.portfolio.value_history.values()) == [200.0]


def test_panic_cost_finds_dips(provider):
    close = provider.history("NVDA", "2y")["Close"]
    dips = guardrails.panic_cost(close)
    assert dips
    for d in dips:
        assert d.drawdown <= -0.05
    assert dips == sorted(dips, key=lambda d: d.drawdown)


# ---------- alerts integration ----------

def test_alerts_include_thesis_check_and_harvest(manager, provider):
    price = provider.quote("AAPL").price
    manager.buy("AAPL", 10, price * 1.6, trade_date=_days_ago(100),
                thesis="long-term compounder",
                invalidation="services revenue shrinks two quarters straight")
    alerts = check_alerts(manager, provider)
    kinds = {a.kind for a in alerts}
    assert "thesis_check" in kinds
    assert "tax_harvest" in kinds
    # Snapshot got recorded as a side effect.
    assert len(manager.portfolio.value_history) == 1


# ---------- IBKR lot reconciliation ----------

def test_sync_increase_adds_lot_decrease_consumes_fifo(manager):
    manager.buy("AAPL", 10, 100, trade_date=_days_ago(400))
    sync_positions(manager, [BrokerPosition("AAPL", 15, 120)], replace=True)
    pos = manager.get_position("AAPL")
    assert pos.quantity == pytest.approx(15)
    assert pos.avg_cost == pytest.approx(120)
    assert len(pos.lots) == 2
    assert pos.lots[0].is_long_term()          # original lot date preserved
    implied = (15 * 120 - 10 * 100) / 5
    assert pos.lots[1].cost_per_share == pytest.approx(implied)

    sync_positions(manager, [BrokerPosition("AAPL", 3, 100)], replace=True)
    pos = manager.get_position("AAPL")
    assert pos.quantity == pytest.approx(3)
    assert len(pos.lots) == 1                  # FIFO consumed the old lot fully
    assert not pos.lots[0].is_long_term()      # remaining shares are the new lot
