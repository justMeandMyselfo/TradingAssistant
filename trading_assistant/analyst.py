"""Claude-powered portfolio analyst.

Bundles everything the assistant knows about your portfolio — positions,
P&L, protective rules, alerts, tax-harvest opportunities, upcoming events,
guardrail state and the advisor track record — into one context block, then
asks Claude for a plain-English brief or answers your questions over it.

Setup: set ANTHROPIC_API_KEY in the environment, or store it with
    python -m trading_assistant.cli config anthropic <key>
Keys live in data/config.json (git-ignored) and never leave your machine
except to call the Anthropic API.
"""
from __future__ import annotations

import json
import os
from datetime import date

from .data.provider import DataProvider
from .notify import load_config
from .portfolio.manager import PortfolioManager

DEFAULT_MODEL = "claude-opus-4-8"
MAX_TOKENS = 8000   # adaptive thinking shares this budget with the visible answer

SYSTEM_PROMPT = (
    "You are the analyst desk of a personal trading assistant. You receive a "
    "JSON snapshot of the user's portfolio and market context, and you answer "
    "in clear, plain English for a retail investor.\n"
    "Rules:\n"
    "- Ground every number you state in the snapshot; never invent prices, "
    "positions or performance figures. If something isn't in the snapshot, "
    "say you don't have that data.\n"
    "- Be direct and specific: name symbols, amounts and percentages.\n"
    "- Flag risks proactively (concentration, missing stops, breached rules, "
    "wash-sale traps, event risk like imminent earnings).\n"
    "- You are an educational tool, not a licensed financial advisor — close "
    "every brief with a one-line reminder of that.\n"
    "- Keep answers tight: lead with what matters most."
)


def get_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or load_config().get("anthropic_key")


def gather_context(manager: PortfolioManager, provider: DataProvider) -> dict:
    """One JSON-serializable snapshot of everything the analyst may cite."""
    from . import tax, tracking
    from .events import upcoming_events
    from .portfolio.alerts import check_alerts

    snapshot: dict = {"as_of": date.today().isoformat(),
                      "data_source": provider.name,
                      "live_data": provider.is_live}

    v = manager.valuation(provider)
    snapshot["portfolio"] = {
        "total_value": round(v["total_value"], 2),
        "total_pnl": round(v["total_pnl"], 2),
        "cash": round(v["cash"], 2),
        "positions": [
            {"symbol": r["symbol"], "quantity": r["quantity"],
             "avg_cost": round(r["avg_cost"], 2), "price": round(r["price"], 2),
             "value": round(r["value"], 2), "pnl_pct": round(r["pnl_pct"], 2),
             "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
             "trailing_stop_pct": r["trailing_stop_pct"]}
            for r in v["rows"]],
    }
    snapshot["journal"] = [
        {"symbol": p.symbol, "thesis": p.thesis, "sell_if": p.invalidation}
        for p in manager.portfolio.positions if p.thesis or p.invalidation]
    snapshot["dca_plans"] = [p.to_dict() for p in manager.portfolio.dca_plans]

    try:
        snapshot["alerts"] = [
            {"symbol": a.symbol, "kind": a.kind, "level": a.level.value,
             "message": a.message}
            for a in check_alerts(manager, provider)]
    except Exception:
        snapshot["alerts"] = []

    try:
        snapshot["tax"] = {
            "realized_ytd": tax.realized_summary(manager),
            "harvest_opportunities": [
                {"symbol": s.symbol, "lot_date": s.lot_date,
                 "unrealized_loss": round(s.unrealized_loss, 2),
                 "est_tax_saving": round(s.est_tax_saving, 2),
                 "replacements": s.replacements, "warnings": s.warnings}
                for s in tax.harvest_opportunities(manager, provider)[:5]],
        }
    except Exception:
        snapshot["tax"] = {}

    try:
        snapshot["upcoming_events"] = [
            {"symbol": e.symbol or "MACRO", "kind": e.kind,
             "date": e.date.isoformat(), "note": e.note}
            for e in upcoming_events(provider,
                                     [p.symbol for p in manager.portfolio.positions])]
    except Exception:
        snapshot["upcoming_events"] = []

    try:
        perfs = tracking.performance(provider)
        snapshot["advisor_track_record"] = {
            **tracking.summary(perfs),
            "entries": [{"date": t.date, "return": round(t.portfolio_return, 4),
                         "spy": round(t.benchmark_return, 4),
                         "alpha": round(t.alpha, 4)} for t in perfs[-10:]],
        }
    except Exception:
        snapshot["advisor_track_record"] = {}

    g = manager.portfolio.guardrail
    snapshot["circuit_breaker"] = {"locked": g.is_locked(), "reason": g.reason}
    return snapshot


def _extract_text(response) -> str:
    return "\n".join(b.text for b in response.content
                     if getattr(b, "type", "") == "text").strip()


class PortfolioAnalyst:
    def __init__(self, client=None, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError("Install the SDK first: pip install anthropic") from e
            key = get_api_key()
            if not key:
                raise RuntimeError(
                    "No Anthropic API key found. Set ANTHROPIC_API_KEY or run: "
                    "config anthropic <key> (get one at console.anthropic.com)")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _ask_claude(self, context: dict, request: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (f"Portfolio snapshot:\n```json\n"
                            f"{json.dumps(context, indent=1)}\n```\n\n{request}"),
            }])
        if response.stop_reason == "refusal":
            return ("The analyst declined this request. Try rephrasing your "
                    "question about the portfolio.")
        return _extract_text(response)

    def brief(self, manager: PortfolioManager, provider: DataProvider) -> str:
        """The 'what changed for my portfolio' analyst brief."""
        context = gather_context(manager, provider)
        return self._ask_claude(context, (
            "Write my portfolio brief. Cover: (1) overall health — value, P&L, "
            "concentration; (2) anything urgent — triggered alerts, breached "
            "rules, the circuit breaker; (3) tax moves worth making, including "
            "wash-sale traps; (4) upcoming events I should prepare for; "
            "(5) one or two concrete next actions. Use short sections with "
            "headers."))

    def ask(self, question: str, manager: PortfolioManager,
            provider: DataProvider) -> str:
        """Free-form Q&A grounded in the portfolio snapshot."""
        context = gather_context(manager, provider)
        return self._ask_claude(context, f"Question: {question}")
