# 📈 Trading Assistant

A full-featured investment advisor and portfolio tracker:

- **🎯 Recommendation engine** — tell it your risk tolerance (1–10), target annual
  growth, horizon, capital, sector preferences and whether crypto is allowed; it
  scores a 34-asset universe (ETFs, bonds, mega-caps, gold, REITs, crypto) on
  volatility fit, historical CAGR vs your target, Sharpe/Sortino, trend (200-day SMA),
  momentum, RSI and max drawdown, then builds a diversified, position-capped
  allocation with exact share counts and ATR-based stop-loss / take-profit levels.
- **📊 Market dashboard** — live quotes and interactive candlestick charts with
  SMA/EMA/Bollinger overlays plus RSI and MACD panels.
- **💼 Portfolio tracking** — record buys/sells, live valuation and P&L, with
  **stop-loss, stop-limit, take-profit and trailing-stop** rules and an alert
  engine that tells you the moment a rule triggers (including gap-below-limit
  warnings for stop-limit orders, and unprotected-loser warnings).
- **📅 DCA (dollar-cost averaging)** — recurring purchase plans (weekly/biweekly/
  monthly) with due-date alerts and one-click execution at market price.
- **🧪 Backtester** — DCA vs lump-sum comparison with equity curves.
- **🎲 Goal probability** — correlation-aware portfolio volatility (covariance
  matrix, not naive vol sums) feeding a 5,000-path Monte Carlo simulation:
  "you have an N% chance of hitting your target", with a percentile fan chart.
  Return estimates are shrunk toward a 7% market prior to avoid extrapolating
  a lucky past.
- **🔔 Discord alerts** — stop-loss / take-profit / trailing / DCA alerts pushed
  to a Discord channel as color-coded embeds, de-duplicated with a 6h cooldown.
  `watch` mode polls continuously for 24/7 monitoring.
- **🏦 Broker connectivity** — read-only live position sync from **Interactive
  Brokers** (official API via TWS/IB Gateway), plus a flexible **CSV import**
  (handles Trade Republic-style exports, `;` separators, European decimal
  commas, column aliases). Protective rules and tax lots survive every sync.
- **🧾 Tax-lot accounting** — every buy is a tax lot; sells consume lots FIFO
  and split realized gains into short/long-term. **Tax-loss harvesting**
  suggestions per lot with estimated savings, similar-asset replacements to
  stay invested, **wash-sale detection** (recent buys *and* active DCA plans),
  "this lot turns long-term in N days" warnings before sells, and a realized
  year-to-date summary. Rates are configurable per jurisdiction.
- **🧠 Behavioral guardrails** — a **circuit breaker** soft-locks sells for a
  cooling-off period when the portfolio drops hard in a week (overridable,
  consciously); a **pre-commitment journal** stores your thesis and exit
  condition per position and confronts you with them during drawdowns; a
  **panic-cost simulator** shows what selling at past dips would have cost.
- **📅 Event awareness** — upcoming earnings dates and the FOMC calendar feed
  the alert engine: a stop sitting close to price days before earnings gets a
  gap-risk warning (an earnings gap can fill far below a stop-market order).
- **⚡ Alpaca execution** — triggered alerts become real orders: stop-loss →
  market sell (or limit sell at your stop-limit), take-profit → sell, due DCA
  → notional buy. **Paper trading by default**; the live endpoint requires an
  explicit double opt-in. Every run shows a dry-run plan first.
- **📋 Accountability dashboard** — every advisor recommendation is logged
  with its prices and continuously marked to market against just buying SPY:
  per-recommendation alpha, beat rate, and average alpha. If the engine can't
  beat the boring benchmark, you'll see it.

## Data

Live, near-real-time data comes from **Yahoo Finance** (no API key needed).
If the network is unreachable, the app automatically falls back to a clearly
labeled **deterministic offline simulator** so every feature stays usable
(handy for demos, tests and air-gapped environments).

## Quick start

```bash
pip install -r requirements.txt

# Web app
streamlit run app/streamlit_app.py
```

## CLI

```bash
python -m trading_assistant.cli quote AAPL MSFT BTC-USD
python -m trading_assistant.cli advise --risk 7 --growth 12 --capital 25000 --crypto
python -m trading_assistant.cli buy AAPL 10 182.50
python -m trading_assistant.cli protect AAPL --stop-loss 165 --take-profit 220 --trailing 10
python -m trading_assistant.cli dca add VTI 500 monthly
python -m trading_assistant.cli dca run          # execute due DCA purchases
python -m trading_assistant.cli portfolio        # holdings & P&L
python -m trading_assistant.cli alerts           # check all protective rules
python -m trading_assistant.cli backtest SPY --amount 500 --frequency monthly

# Discord alerts
python -m trading_assistant.cli config discord https://discord.com/api/webhooks/...
python -m trading_assistant.cli notify test
python -m trading_assistant.cli alerts --notify   # one-shot push
python -m trading_assistant.cli watch --interval 300   # 24/7 monitor

# Broker sync
python -m trading_assistant.cli sync ibkr --port 7497   # TWS paper account
python -m trading_assistant.cli import-csv my_positions.csv   # API-less brokers

# Tax & guardrails
python -m trading_assistant.cli buy AAPL 10 182.50 --thesis "moat" --sell-if "services shrink"
python -m trading_assistant.cli lots                  # tax lots + journal per position
python -m trading_assistant.cli tax harvest           # loss-harvesting suggestions
python -m trading_assistant.cli tax summary           # realized ST/LT gains YTD
python -m trading_assistant.cli tax rates --short 30 --long 15
python -m trading_assistant.cli journal set AAPL --thesis "..." --sell-if "..."
python -m trading_assistant.cli breaker status        # circuit breaker state
python -m trading_assistant.cli sell AAPL 5 200 --override   # conscious bypass

# Events, execution, accountability
python -m trading_assistant.cli events --days 14      # earnings + FOMC calendar
python -m trading_assistant.cli config alpaca KEY SECRET
python -m trading_assistant.cli alpaca status         # paper account state
python -m trading_assistant.cli alpaca execute        # dry-run order plan
python -m trading_assistant.cli alpaca execute --send # submit to PAPER
python -m trading_assistant.cli watch --execute       # 24/7: alert → paper order
python -m trading_assistant.cli track                 # advisor picks vs SPY
```

## Broker notes

- **Interactive Brokers**: run TWS or the free IB Gateway, enable
  *Global Configuration → API → Settings → Enable ActiveX and Socket Clients*,
  then `sync ibkr`. The connection is **read-only** — no orders are placed.
- **Trade Republic**: there is **no official TR API**. Unofficial
  reverse-engineered clients exist but violate TR's terms and require your real
  banking credentials, so this project deliberately doesn't bundle one. Export
  or type your positions into a CSV (`symbol;quantity;avg_cost`) and use
  `import-csv` instead.

The portfolio is stored in `data/portfolio.json` (git-ignored) and is shared
between the CLI and the web app.

## Project layout

```
trading_assistant/
  data/        # provider abstraction: Yahoo Finance (live) + offline simulator
  analysis/    # indicators, risk metrics, covariance vol + Monte Carlo goals
  advisor/     # investor profile, asset universe, scoring/allocation engine
  portfolio/   # positions, protective rules, DCA plans, persistence, alerts
  brokers/     # IBKR live sync, Alpaca execution, generic CSV import
  notify.py    # Discord webhook alerts with de-duplication
  tax.py       # tax lots, loss harvesting, wash sales, realized gains
  guardrails.py # circuit breaker, panic-cost simulator
  events.py    # earnings dates + FOMC calendar for event-risk alerts
  tracking.py  # accountability log: advisor picks vs SPY
  backtest.py  # DCA vs lump-sum backtests
  cli.py       # command-line interface
app/
  streamlit_app.py   # web app (Market / Advisor / Portfolio / Backtest)
tests/         # pytest suite incl. Streamlit AppTest smoke tests
```

## Tests

```bash
python -m pytest tests/ -v
```

## ⚠️ Disclaimer

This software is for **educational and informational purposes only**. It is
**not financial advice**, and it does not place real orders with any broker —
stop-loss / take-profit / DCA rules generate *alerts* for you to act on.
Markets involve risk of loss; past performance does not guarantee future
results. Always do your own research.
