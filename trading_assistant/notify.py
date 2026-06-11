"""Discord notifications for portfolio alerts.

Configure once with the CLI (`config discord <webhook-url>`), via the web app,
or with the DISCORD_WEBHOOK_URL environment variable. Alerts are sent as rich
embeds, color-coded by severity, and de-duplicated so a stop-loss that stays
breached doesn't ping you every minute.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from .portfolio.alerts import Alert

CONFIG_PATH = Path("data") / "config.json"
STATE_PATH = Path("data") / "alert_state.json"
DEDUPE_COOLDOWN_HOURS = 6.0

_LEVEL_COLORS = {"critical": 0xE53935, "warning": 0xFB8C00, "info": 0x1E88E5}
_LEVEL_ICONS = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


# ---------- config ----------

def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_config(cfg: dict, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))


def get_webhook_url(path: Path | None = None) -> str | None:
    return os.environ.get("DISCORD_WEBHOOK_URL") or load_config(path).get("discord_webhook_url")


def set_webhook_url(url: str, path: Path | None = None) -> None:
    cfg = load_config(path)
    cfg["discord_webhook_url"] = url.strip()
    save_config(cfg, path)


# ---------- sending ----------

def _post(webhook_url: str, payload: dict) -> bool:
    req = urllib.request.Request(
        webhook_url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "TradingAssistant/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return 200 <= resp.status < 300


def send_message(text: str, webhook_url: str | None = None) -> bool:
    """Send a plain text message (e.g. the test ping)."""
    url = webhook_url or get_webhook_url()
    if not url:
        return False
    return _post(url, {"content": text[:2000]})


def send_alerts(alerts: list[Alert], webhook_url: str | None = None,
                dedupe: bool = True, state_path: Path = STATE_PATH) -> int:
    """Send alerts as Discord embeds. Returns how many were actually sent
    (after de-duplication)."""
    url = webhook_url or get_webhook_url()
    if not url or not alerts:
        return 0

    to_send = _filter_deduped(alerts, state_path) if dedupe else list(alerts)
    if not to_send:
        return 0

    embeds = []
    for a in to_send:
        embeds.append({
            "title": f"{_LEVEL_ICONS.get(a.level.value, '·')} {a.symbol} — "
                     f"{a.kind.replace('_', ' ')}",
            "description": a.message[:4000],
            "color": _LEVEL_COLORS.get(a.level.value, 0x9E9E9E),
            "timestamp": datetime.utcnow().isoformat(),
        })

    sent = 0
    # Discord allows max 10 embeds per message.
    for i in range(0, len(embeds), 10):
        chunk = embeds[i:i + 10]
        if _post(url, {"content": "📈 **Trading Assistant alerts**" if i == 0 else "",
                       "embeds": chunk}):
            sent += len(chunk)
    if dedupe and sent:
        _mark_sent(to_send, state_path)
    return sent


# ---------- de-duplication ----------

def _alert_key(a: Alert) -> str:
    return f"{a.symbol}:{a.kind}"


def _filter_deduped(alerts: list[Alert], state_path: Path) -> list[Alert]:
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {}
    cutoff = datetime.utcnow() - timedelta(hours=DEDUPE_COOLDOWN_HOURS)
    out = []
    for a in alerts:
        last = state.get(_alert_key(a))
        if last is None or datetime.fromisoformat(last) < cutoff:
            out.append(a)
    return out


def _mark_sent(alerts: list[Alert], state_path: Path) -> None:
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {}
    now = datetime.utcnow().isoformat()
    for a in alerts:
        state[_alert_key(a)] = now
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
