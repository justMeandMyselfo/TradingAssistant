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
```

The portfolio is stored in `data/portfolio.json` (git-ignored) and is shared
between the CLI and the web app.

## Project layout

```
trading_assistant/
  data/        # provider abstraction: Yahoo Finance (live) + offline simulator
  analysis/    # indicators (SMA, EMA, RSI, MACD, Bollinger, ATR) and risk metrics
  advisor/     # investor profile, asset universe, scoring/allocation engine
  portfolio/   # positions, protective rules, DCA plans, persistence, alerts
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
