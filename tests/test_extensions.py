"""Tests for Discord notifications, portfolio risk/Monte Carlo, broker sync
and CSV import."""
import json
from types import SimpleNamespace

import pytest

from trading_assistant import notify
from trading_assistant.advisor import AdvisorEngine, InvestorProfile
from trading_assistant.analysis.portfolio_risk import (portfolio_volatility,
                                                       shrink_return,
                                                       simulate_goal)
from trading_assistant.brokers.csv_import import import_positions_csv, read_positions_csv
from trading_assistant.brokers.ibkr import BrokerPosition, _map_positions, sync_positions
from trading_assistant.data.offline import OfflineProvider
from trading_assistant.portfolio import PortfolioManager
from trading_assistant.portfolio.alerts import Alert, AlertLevel


@pytest.fixture
def provider():
    return OfflineProvider()


@pytest.fixture
def manager(tmp_path):
    return PortfolioManager(tmp_path / "portfolio.json")


# ---------- portfolio risk / Monte Carlo ----------

def test_portfolio_volatility_credits_diversification(provider):
    symbols = ["SPY", "TLT", "GLD", "NVDA"]
    closes = {s: provider.history(s, "2y")["Close"] for s in symbols}
    weights = {s: 0.25 for s in symbols}
    true_vol = portfolio_volatility(closes, weights)
    from trading_assistant.analysis.metrics import compute_metrics
    naive = sum(0.25 * compute_metrics(closes[s]).annual_volatility for s in symbols)
    assert 0 < true_vol < naive  # diversification must reduce risk


def test_shrink_return_pulls_toward_prior():
    assert shrink_return(0.07) == pytest.approx(0.07)
    assert 0.07 < shrink_return(0.40) < 0.40
    assert -0.10 < shrink_return(-0.10) < 0.07


def test_simulate_goal_properties():
    sim = simulate_goal(10_000, mu=0.08, sigma=0.15, horizon_years=5,
                        target_growth=0.08)
    assert 0.0 <= sim.probability <= 1.0
    assert sim.target_value == pytest.approx(10_000 * 1.08**5)
    assert sim.final_percentiles[10] < sim.final_percentiles[50] < sim.final_percentiles[90]
    assert len(sim.percentile_curves) == 61  # 5y monthly + start
    # Deterministic with the same seed.
    sim2 = simulate_goal(10_000, mu=0.08, sigma=0.15, horizon_years=5,
                         target_growth=0.08)
    assert sim2.probability == sim.probability
    # A wildly ambitious target must be less likely than a modest one.
    hard = simulate_goal(10_000, mu=0.08, sigma=0.15, horizon_years=5,
                         target_growth=0.30)
    assert hard.probability < sim.probability


def test_advice_report_includes_goal(provider):
    report = AdvisorEngine(provider).advise(
        InvestorProfile(risk_tolerance=6, capital=20_000, horizon_years=5))
    assert report.goal is not None
    assert 0.0 <= report.goal.probability <= 1.0
    assert report.expected_volatility > 0


# ---------- Discord notifications ----------

class _FakeResponse:
    status = 204
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_send_alerts_chunks_and_dedupes(tmp_path, monkeypatch):
    sent_payloads = []

    def fake_urlopen(req, timeout=10):
        sent_payloads.append(json.loads(req.data))
        return _FakeResponse()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    state = tmp_path / "alert_state.json"
    alerts = [Alert(f"SYM{i}", "stop_loss", AlertLevel.CRITICAL, f"msg {i}", 10.0)
              for i in range(12)]

    n = notify.send_alerts(alerts, webhook_url="https://discord.test/hook",
                           state_path=state)
    assert n == 12
    assert len(sent_payloads) == 2                      # 10 + 2 embeds
    assert len(sent_payloads[0]["embeds"]) == 10
    assert sent_payloads[0]["embeds"][0]["color"] == 0xE53935

    # Second send within the cooldown is fully de-duplicated.
    n2 = notify.send_alerts(alerts, webhook_url="https://discord.test/hook",
                            state_path=state)
    assert n2 == 0


def test_send_without_webhook_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    assert notify.send_message("hello") is False


def test_webhook_config_roundtrip(tmp_path):
    cfg = tmp_path / "config.json"
    notify.set_webhook_url("https://discord.test/abc", cfg)
    assert notify.get_webhook_url(cfg) == "https://discord.test/abc"


# ---------- IBKR mapping & sync ----------

def _fake_ib_position(symbol, qty, avg_cost, sec_type="STK", multiplier=""):
    contract = SimpleNamespace(symbol=symbol, secType=sec_type,
                               multiplier=multiplier)
    return SimpleNamespace(contract=contract, position=qty, avgCost=avg_cost,
                           account="DU123")


def test_map_positions_handles_multiplier_crypto_and_zero():
    raw = [_fake_ib_position("AAPL", 10, 172.5),
           _fake_ib_position("ES", 2, 50 * 4500.0, sec_type="FUT", multiplier="50"),
           _fake_ib_position("BTC", 0.5, 60_000, sec_type="CRYPTO"),
           _fake_ib_position("DEAD", 0, 1.0)]
    mapped = _map_positions(raw)
    by_sym = {m.symbol: m for m in mapped}
    assert by_sym["AAPL"].avg_cost == pytest.approx(172.5)
    assert by_sym["ES"].avg_cost == pytest.approx(4500.0)   # multiplier stripped
    assert "BTC-USD" in by_sym                              # Yahoo-style crypto
    assert "DEAD" not in by_sym                             # zero qty skipped


def test_sync_replace_preserves_rules(manager):
    manager.buy("AAPL", 5, 150)
    manager.buy("OLD", 1, 10)
    manager.set_rules("AAPL", stop_loss=140)
    result = sync_positions(manager, [
        BrokerPosition("AAPL", 10, 160), BrokerPosition("MSFT", 2, 400)],
        replace=True)
    assert result["updated"] == ["AAPL"]
    assert result["added"] == ["MSFT"]
    assert result["removed"] == ["OLD"]
    pos = manager.get_position("AAPL")
    assert pos.quantity == 10 and pos.avg_cost == 160
    assert pos.rules.stop_loss == 140      # rules survive the sync


# ---------- CSV import ----------

def test_csv_import_with_aliases_and_european_format(manager, tmp_path):
    csv = tmp_path / "tr.csv"
    csv.write_text("Ticker;Shares;Buy_Price\n"
                   "aapl;10;172,50\n"
                   "VWCE.DE;25;1.104,90\n"
                   ";;\n")
    positions = read_positions_csv(csv)
    assert {p.symbol for p in positions} == {"AAPL", "VWCE.DE"}
    assert positions[1].avg_cost == pytest.approx(1104.90)

    result = import_positions_csv(manager, csv)
    assert sorted(result["added"]) == ["AAPL", "VWCE.DE"]
    assert manager.get_position("AAPL").quantity == 10


def test_csv_import_rejects_garbage(tmp_path, manager):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        import_positions_csv(manager, bad)
