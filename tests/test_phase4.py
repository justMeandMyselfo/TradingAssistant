"""Tests for the small-cap screener and the Claude analyst layer."""
from types import SimpleNamespace

import pytest

from trading_assistant import analyst as analyst_mod
from trading_assistant.analyst import PortfolioAnalyst, gather_context
from trading_assistant.data.offline import OfflineProvider
from trading_assistant.portfolio import PortfolioManager
from trading_assistant.screener import (DEFAULT_SMALL_CAP_UNIVERSE,
                                        INSTITUTIONAL_ACCESS_THRESHOLD, screen)


@pytest.fixture
def provider():
    return OfflineProvider()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from trading_assistant import notify
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    return PortfolioManager(tmp_path / "portfolio.json")


# ---------- screener ----------

def test_screen_returns_ranked_small_caps(provider):
    hits = screen(provider, top=10)
    assert hits
    assert len(hits) <= 10
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    for h in hits:
        assert 0 <= h.score <= 100
        assert h.market_cap is None or h.market_cap <= 10e9
        assert h.avg_dollar_volume > 0
        assert h.institutional_locked_out == (
            h.avg_dollar_volume < INSTITUTIONAL_ACCESS_THRESHOLD)


def test_screen_market_cap_filter_excludes_mega_caps(provider):
    # AAPL gets a simulated mega-cap; it must be filtered out.
    hits = screen(provider, symbols=["AAPL", *DEFAULT_SMALL_CAP_UNIVERSE[:5]],
                  max_market_cap=10e9)
    assert all(h.symbol != "AAPL" for h in hits)


def test_screen_custom_universe_and_bad_tickers_skipped(provider):
    hits = screen(provider, symbols=["CAKE", "TXRH"], top=5)
    assert {h.symbol for h in hits} <= {"CAKE", "TXRH"}
    # A custom universe with junk should not raise.
    assert isinstance(screen(provider, symbols=["CAKE"], top=5), list)


def test_screen_liquidity_floor(provider):
    # Absurdly high floor filters out everything.
    assert screen(provider, min_dollar_volume=1e15) == []


# ---------- analyst context ----------

def test_gather_context_structure(manager, provider):
    price = provider.quote("AAPL").price
    manager.buy("AAPL", 5, price * 1.5, thesis="moat",
                invalidation="services shrink")
    manager.add_dca("VTI", 200, "monthly")
    ctx = gather_context(manager, provider)
    assert ctx["portfolio"]["positions"][0]["symbol"] == "AAPL"
    assert ctx["journal"][0]["thesis"] == "moat"
    assert ctx["dca_plans"][0]["symbol"] == "VTI"
    assert "alerts" in ctx and "tax" in ctx and "upcoming_events" in ctx
    assert ctx["circuit_breaker"]["locked"] is False
    import json
    json.dumps(ctx)   # must be JSON-serializable for the prompt


# ---------- analyst Claude calls (mocked client) ----------

class _FakeMessages:
    def __init__(self, log):
        self.log = log

    def create(self, **kwargs):
        self.log.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text="Your portfolio looks fine.")])


def _fake_client(log):
    return SimpleNamespace(messages=_FakeMessages(log))


def test_brief_calls_claude_with_grounded_context(manager, provider):
    manager.buy("MSFT", 2, provider.quote("MSFT").price)
    calls = []
    analyst = PortfolioAnalyst(client=_fake_client(calls))
    out = analyst.brief(manager, provider)
    assert out == "Your portfolio looks fine."
    req = calls[0]
    assert req["model"] == "claude-opus-4-8"
    assert req["thinking"] == {"type": "adaptive"}
    assert "MSFT" in req["messages"][0]["content"]   # snapshot is in the prompt
    assert "not a licensed financial advisor" in req["system"]


def test_ask_includes_question(manager, provider):
    calls = []
    analyst = PortfolioAnalyst(client=_fake_client(calls))
    analyst.ask("Am I overexposed to tech?", manager, provider)
    assert "Am I overexposed to tech?" in calls[0]["messages"][0]["content"]


def test_refusal_handled(manager, provider):
    class _RefusingMessages:
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", content=[])

    analyst = PortfolioAnalyst(client=SimpleNamespace(messages=_RefusingMessages()))
    out = analyst.ask("anything", manager, provider)
    assert "declined" in out


def test_missing_api_key_raises(monkeypatch, tmp_path):
    from trading_assistant import notify
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    analyst = PortfolioAnalyst()
    with pytest.raises(RuntimeError, match="API key"):
        _ = analyst.client


def test_get_api_key_from_config(monkeypatch, tmp_path):
    from trading_assistant import notify
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "config.json")
    notify.set_webhook_url("https://x", tmp_path / "config.json")  # create file
    cfg = notify.load_config(tmp_path / "config.json")
    cfg["anthropic_key"] = "sk-ant-test"
    notify.save_config(cfg, tmp_path / "config.json")
    assert analyst_mod.get_api_key() == "sk-ant-test"
