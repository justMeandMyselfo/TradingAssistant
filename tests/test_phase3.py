"""Tests for events, Alpaca execution planning and accountability tracking."""
import json
from datetime import date, timedelta

import pytest

from trading_assistant.advisor import AdvisorEngine, InvestorProfile
from trading_assistant.brokers import alpaca
from trading_assistant.brokers.alpaca import (AlpacaClient, AlpacaError,
                                              PlannedOrder, _to_alpaca_symbol,
                                              plan_orders)
from trading_assistant.data.offline import OfflineProvider
from trading_assistant.events import FOMC_DATES, upcoming_events
from trading_assistant.portfolio import PortfolioManager, check_alerts
from trading_assistant import tracking


@pytest.fixture
def provider():
    return OfflineProvider()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from trading_assistant import notify
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    return PortfolioManager(tmp_path / "portfolio.json")


# ---------- events ----------

def test_offline_earnings_deterministic_quarterly(provider):
    d1 = provider.earnings_dates("AAPL")
    d2 = OfflineProvider().earnings_dates("AAPL")
    assert d1 == d2
    assert len(d1) == 2
    assert all(d > date.today() for d in d1)
    assert (d1[1] - d1[0]).days == 91
    assert provider.earnings_dates("SPY") == []       # ETFs don't report
    assert provider.earnings_dates("BTC-USD") == []


def test_fomc_dates_sane():
    assert FOMC_DATES == sorted(FOMC_DATES)
    assert all(2025 <= d.year <= 2026 for d in FOMC_DATES)


def test_upcoming_events_window(provider):
    fixed_today = date(2026, 6, 10)   # FOMC 2026-06-17 is 7 days out
    evs = upcoming_events(provider, ["AAPL", "SPY"], within_days=14,
                          today=fixed_today)
    kinds = {e.kind for e in evs}
    assert "fomc" in kinds
    assert all(fixed_today <= e.date <= fixed_today + timedelta(days=14)
               for e in evs)
    assert evs == sorted(evs, key=lambda e: e.date)


def test_event_risk_alert_for_stop_near_earnings(manager, provider, monkeypatch):
    price = provider.quote("AAPL").price
    manager.buy("AAPL", 5, price)
    manager.set_rules("AAPL", stop_loss=price * 0.95)
    monkeypatch.setattr(type(provider), "earnings_dates",
                        lambda self, s: [date.today() + timedelta(days=2)])
    alerts = check_alerts(manager, provider)
    ev = [a for a in alerts if a.kind == "event_risk" and a.symbol == "AAPL"]
    assert ev and ev[0].level.value == "warning"
    assert "stop" in ev[0].message


# ---------- Alpaca ----------

def test_symbol_conversion():
    assert _to_alpaca_symbol("BTC-USD") == "BTC/USD"
    assert _to_alpaca_symbol("AAPL") == "AAPL"


def test_live_refused_without_config(monkeypatch, tmp_path):
    from trading_assistant import notify
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    with pytest.raises(AlpacaError, match="LIVE"):
        AlpacaClient("k", "s", paper=False)
    AlpacaClient("k", "s", paper=True)   # paper always fine


def test_missing_keys_rejected():
    with pytest.raises(AlpacaError, match="credentials"):
        AlpacaClient("", "", paper=True)


def test_plan_orders_from_alerts(manager, provider):
    price = provider.quote("AAPL").price
    manager.buy("AAPL", 5, price)
    manager.set_rules("AAPL", stop_loss=price * 1.5,        # already breached
                      stop_limit=price * 1.45)
    manager.add_dca("VTI", 250, "weekly")                   # due today
    alerts = check_alerts(manager, provider)
    plans = plan_orders(manager, alerts)
    by = {(p.symbol, p.side): p for p in plans}

    sell = by[("AAPL", "sell")]
    assert sell.qty == 5
    assert sell.order_type == "limit"                       # stop_limit configured
    assert sell.limit_price == pytest.approx(price * 1.45)

    buy = by[("VTI", "buy")]
    assert buy.notional == 250
    assert buy.order_type == "market"


def test_execute_plan_submits_and_records(manager, provider, monkeypatch):
    submitted = []

    def fake_request(self, method, path, body=None):
        if method == "POST":
            submitted.append(body)
            return {"id": f"order-{len(submitted)}", "status": "accepted"}
        return {}

    monkeypatch.setattr(AlpacaClient, "_request", fake_request)
    client = AlpacaClient("k", "s", paper=True)

    price = provider.quote("VTI").price
    manager.add_dca("VTI", 500, "monthly")
    plans = [PlannedOrder(symbol="VTI", side="buy", reason="dca_due", notional=500)]
    executed = alpaca.execute_plan(client, manager, plans, provider=provider)

    assert submitted[0]["symbol"] == "VTI"
    assert submitted[0]["notional"] == "500"
    assert executed[0].result["id"] == "order-1"
    pos = manager.get_position("VTI")                       # mirrored locally
    assert pos.quantity == pytest.approx(500 / price)
    assert not manager.portfolio.dca_plans[0].is_due()      # schedule advanced


# ---------- tracking ----------

def test_log_and_performance_roundtrip(provider, tmp_path):
    log = tmp_path / "advice_log.json"
    report = AdvisorEngine(provider).advise(
        InvestorProfile(risk_tolerance=6, capital=10_000), top_n=5)
    entry_id = tracking.log_report(report, provider, path=log)
    assert len(entry_id) == 8

    perfs = tracking.performance(provider, path=log)
    assert len(perfs) == 1
    p = perfs[0]
    assert p.entry_id == entry_id
    # Same-day mark-to-market: returns are ~0 with deterministic data.
    assert p.portfolio_return == pytest.approx(0.0, abs=1e-9)
    assert p.alpha == pytest.approx(0.0, abs=1e-9)


def test_performance_math_with_synthetic_entry(provider, tmp_path):
    log = tmp_path / "advice_log.json"
    spy_now = provider.quote("SPY").price
    aapl_now = provider.quote("AAPL").price
    entry = {"id": "test1234", "date": "2026-01-01",
             "profile": {"label": "Balanced", "capital": 1000.0},
             "benchmark_price": spy_now / 1.10,           # SPY rose 10% since
             "picks": [{"symbol": "AAPL", "weight": 1.0,
                        "price": aapl_now / 1.25, "amount": 1000.0}]}  # +25%
    log.write_text(json.dumps([entry]))
    p = tracking.performance(provider, path=log)[0]
    assert p.portfolio_return == pytest.approx(0.25, rel=1e-6)
    assert p.benchmark_return == pytest.approx(0.10, rel=1e-6)
    assert p.alpha == pytest.approx(0.15, rel=1e-6)
    s = tracking.summary([p])
    assert s["count"] == 1 and s["beat_rate"] == 1.0
