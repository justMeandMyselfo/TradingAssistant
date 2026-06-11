"""Trading Assistant — Streamlit web app.

Run with:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading_assistant.advisor import AdvisorEngine, InvestorProfile          # noqa: E402
from trading_assistant.advisor.universe import UNIVERSE                       # noqa: E402
from trading_assistant.analysis.indicators import bollinger, ema, macd, rsi, sma  # noqa: E402
from trading_assistant.backtest import backtest_dca, backtest_lump_sum        # noqa: E402
from trading_assistant.data import get_provider                               # noqa: E402
from trading_assistant import notify                                          # noqa: E402
from trading_assistant.portfolio import PortfolioManager, check_alerts       # noqa: E402

st.set_page_config(page_title="Trading Assistant", page_icon="📈", layout="wide")

PORTFOLIO_PATH = ROOT / "data" / "portfolio.json"
DEFAULT_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "BTC-USD"]


# ---------- shared state ----------

@st.cache_resource
def provider():
    return get_provider()


def manager() -> PortfolioManager:
    # Re-read from disk each rerun so CLI and app stay in sync.
    return PortfolioManager(PORTFOLIO_PATH)


@st.cache_data(ttl=300, show_spinner=False)
def cached_history(symbol: str, period: str) -> pd.DataFrame:
    return provider().history(symbol, period=period)


@st.cache_data(ttl=60, show_spinner=False)
def cached_quotes(symbols: tuple[str, ...]) -> dict:
    return {s: q for s, q in provider().quotes(list(symbols)).items()}


# ---------- header ----------

p = provider()
st.title("📈 Trading Assistant")
if p.is_live:
    st.caption("🟢 Live data — Yahoo Finance (quotes cached ≤60 s, history ≤5 min)")
else:
    st.warning("🔌 Live market data is unreachable from this machine — running on "
               "**deterministic simulated data**. All features work identically; "
               "reconnect to the internet for real prices.", icon="⚠️")

tab_market, tab_advisor, tab_portfolio, tab_backtest = st.tabs(
    ["📊 Market", "🎯 Advisor", "💼 Portfolio", "🧪 Backtest"])


# ---------- price chart helper ----------

def price_figure(symbol: str, df: pd.DataFrame, overlays: list[str]) -> go.Figure:
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03,
                        subplot_titles=(symbol, "RSI (14)", "MACD"))
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"],
                                 low=df["Low"], close=df["Close"], name=symbol,
                                 increasing_line_color="#26a69a",
                                 decreasing_line_color="#ef5350"), row=1, col=1)
    close = df["Close"]
    if "SMA 50" in overlays:
        fig.add_trace(go.Scatter(x=df.index, y=sma(close, 50), name="SMA 50",
                                 line=dict(width=1.2, color="#ffa726")), row=1, col=1)
    if "SMA 200" in overlays:
        fig.add_trace(go.Scatter(x=df.index, y=sma(close, 200), name="SMA 200",
                                 line=dict(width=1.2, color="#ab47bc")), row=1, col=1)
    if "EMA 20" in overlays:
        fig.add_trace(go.Scatter(x=df.index, y=ema(close, 20), name="EMA 20",
                                 line=dict(width=1.2, color="#29b6f6")), row=1, col=1)
    if "Bollinger" in overlays:
        bb = bollinger(close)
        for col, dash in (("upper", "dot"), ("lower", "dot")):
            fig.add_trace(go.Scatter(x=df.index, y=bb[col], name=f"BB {col}",
                                     line=dict(width=1, dash=dash, color="#90a4ae"),
                                     showlegend=(col == "upper")), row=1, col=1)

    r = rsi(close)
    fig.add_trace(go.Scatter(x=df.index, y=r, name="RSI", line=dict(color="#7e57c2")),
                  row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

    m = macd(close)
    colors = ["#26a69a" if v >= 0 else "#ef5350" for v in m["histogram"]]
    fig.add_trace(go.Bar(x=df.index, y=m["histogram"], name="MACD hist",
                         marker_color=colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=m["macd"], name="MACD",
                             line=dict(color="#29b6f6", width=1)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=m["signal"], name="Signal",
                             line=dict(color="#ffa726", width=1)), row=3, col=1)

    fig.update_layout(height=720, xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.06),
                      margin=dict(t=60, b=20))
    return fig


# ====================================================================
# TAB 1 — MARKET
# ====================================================================

with tab_market:
    left, right = st.columns([1, 3])
    with left:
        watchlist_text = st.text_area("Watchlist (one symbol per line)",
                                      "\n".join(DEFAULT_WATCHLIST), height=160)
        watchlist = [s.strip().upper() for s in watchlist_text.splitlines() if s.strip()]
        period = st.select_slider("History", ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
                                  value="1y")
        overlays = st.multiselect("Overlays", ["SMA 50", "SMA 200", "EMA 20", "Bollinger"],
                                  default=["SMA 50", "SMA 200"])
        if st.button("🔄 Refresh data"):
            cached_quotes.clear()
            cached_history.clear()
            st.rerun()

    quotes = cached_quotes(tuple(watchlist))
    with left:
        for s in watchlist:
            q = quotes.get(s)
            if q:
                st.metric(s, f"{q.price:,.2f} {q.currency}",
                          f"{q.change:+,.2f} ({q.change_pct:+.2f}%)")
            else:
                st.metric(s, "n/a")

    with right:
        symbol = st.selectbox("Chart symbol", watchlist or DEFAULT_WATCHLIST)
        try:
            df = cached_history(symbol, period)
            st.plotly_chart(price_figure(symbol, df, overlays), use_container_width=True)
        except Exception as e:
            st.error(f"Could not load {symbol}: {e}")


# ====================================================================
# TAB 2 — ADVISOR
# ====================================================================

with tab_advisor:
    with st.form("profile_form"):
        c1, c2, c3, c4 = st.columns(4)
        risk = c1.slider("Risk tolerance", 1, 10, 5,
                         help="1 = capital preservation, 10 = maximum growth")
        growth = c2.number_input("Target growth %/yr", 0.0, 100.0, 8.0, 0.5)
        horizon = c3.number_input("Horizon (years)", 0.5, 40.0, 5.0, 0.5)
        capital = c4.number_input("Capital to invest", 100.0, 100_000_000.0,
                                  10_000.0, 100.0)
        c5, c6, c7 = st.columns(3)
        max_pos = c5.slider("Max single position %", 5, 100, 25) / 100.0
        include_crypto = c6.checkbox("Include crypto")
        top_n = c7.slider("Number of picks", 3, 12, 8)
        sectors = sorted({a.sector for a in UNIVERSE})
        c8, c9 = st.columns(2)
        prefer = c8.multiselect("Preferred sectors", sectors)
        exclude = c9.multiselect("Excluded sectors", sectors)
        submitted = st.form_submit_button("🎯 Get recommendations", type="primary")

    if submitted:
        profile = InvestorProfile(risk_tolerance=risk, target_annual_growth=growth / 100,
                                  horizon_years=horizon, capital=capital,
                                  max_position_pct=max_pos, include_crypto=include_crypto,
                                  preferred_sectors=prefer, excluded_sectors=exclude)
        with st.spinner("Analyzing the market…"):
            st.session_state["advice"] = AdvisorEngine(provider()).advise(profile, top_n=top_n)

    report = st.session_state.get("advice")
    if report:
        profile = report.profile
        (st.success if report.target_feasible else st.warning)(report.target_comment)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Profile", profile.label)
        m2.metric("Expected return (hist.)", f"{report.expected_return:.1%}/yr")
        m3.metric("Portfolio volatility", f"{report.expected_volatility:.1%}",
                  help="Correlation-aware: computed from the covariance of the "
                       "picks' daily returns, so diversification is credited.")
        if report.goal:
            m4.metric("Chance of hitting target", f"{report.goal.probability:.0%}",
                      help=f"Monte Carlo (5,000 paths) probability that "
                           f"{profile.capital:,.0f} grows to "
                           f"{report.goal.target_value:,.0f} in "
                           f"{profile.horizon_years:.0f}y. Uses a return estimate "
                           f"shrunk toward a 7% market prior "
                           f"({report.goal.expected_return_used:.1%}) to avoid "
                           f"extrapolating a lucky past.")

        rows = [{"Symbol": r.asset.symbol, "Name": r.asset.name,
                 "Class": r.asset.asset_class, "Sector": r.asset.sector,
                 "Score": round(r.score, 1), "Weight": r.weight,
                 "Amount": r.amount, "Shares": r.shares,
                 "Price": r.metrics.last_price, "Stop-loss": r.stop_loss,
                 "Take-profit": r.take_profit,
                 "CAGR": r.metrics.annual_return, "Vol": r.metrics.annual_volatility,
                 "Sharpe": round(r.metrics.sharpe, 2),
                 "Max DD": r.metrics.max_drawdown}
                for r in report.picks]
        rec_df = pd.DataFrame(rows)
        st.dataframe(rec_df, use_container_width=True, hide_index=True,
                     column_config={
                         "Weight": st.column_config.ProgressColumn(format="percent",
                                                                   min_value=0, max_value=1),
                         "CAGR": st.column_config.NumberColumn(format="percent"),
                         "Vol": st.column_config.NumberColumn(format="percent"),
                         "Max DD": st.column_config.NumberColumn(format="percent"),
                         "Amount": st.column_config.NumberColumn(format="dollar"),
                         "Price": st.column_config.NumberColumn(format="dollar"),
                         "Stop-loss": st.column_config.NumberColumn(format="dollar"),
                         "Take-profit": st.column_config.NumberColumn(format="dollar"),
                     })

        v1, v2 = st.columns(2)
        with v1:
            pie = go.Figure(go.Pie(labels=rec_df["Symbol"], values=rec_df["Amount"],
                                   hole=0.45, textinfo="label+percent"))
            pie.update_layout(title="Suggested allocation", height=420,
                              margin=dict(t=50, b=10))
            st.plotly_chart(pie, use_container_width=True)
        with v2:
            scat = go.Figure(go.Scatter(
                x=rec_df["Vol"] * 100, y=rec_df["CAGR"] * 100, mode="markers+text",
                text=rec_df["Symbol"], textposition="top center",
                marker=dict(size=rec_df["Weight"] * 220 + 8,
                            color=rec_df["Score"], colorscale="Viridis",
                            showscale=True, colorbar_title="Score")))
            scat.add_hline(y=profile.target_annual_growth * 100, line_dash="dot",
                           annotation_text="your target")
            scat.update_layout(title="Risk vs return (bubble = weight)",
                               xaxis_title="Annual volatility %",
                               yaxis_title="Historical CAGR %", height=420,
                               margin=dict(t=50, b=10))
            st.plotly_chart(scat, use_container_width=True)

        if report.goal:
            g = report.goal
            months = g.percentile_curves.index
            fan = go.Figure()
            fan.add_trace(go.Scatter(x=months, y=g.percentile_curves["p90"],
                                     name="Optimistic (90th pct)",
                                     line=dict(color="#26a69a", width=1)))
            fan.add_trace(go.Scatter(x=months, y=g.percentile_curves["p10"],
                                     name="Pessimistic (10th pct)", fill="tonexty",
                                     fillcolor="rgba(41,182,246,0.15)",
                                     line=dict(color="#ef5350", width=1)))
            fan.add_trace(go.Scatter(x=months, y=g.percentile_curves["p50"],
                                     name="Median", line=dict(color="#29b6f6", width=2)))
            fan.add_hline(y=g.target_value, line_dash="dot",
                          annotation_text=f"target {g.target_value:,.0f}")
            fan.update_layout(title=f"Monte Carlo outlook — {g.probability:.0%} "
                                    f"chance of reaching your target",
                              xaxis_title="Months", yaxis_title="Portfolio value",
                              height=400, margin=dict(t=50, b=10))
            st.plotly_chart(fan, use_container_width=True)
            st.caption(f"After {profile.horizon_years:.0f} years: pessimistic "
                       f"{g.final_percentiles[10]:,.0f} · median "
                       f"{g.final_percentiles[50]:,.0f} · optimistic "
                       f"{g.final_percentiles[90]:,.0f}")

        with st.expander("Why these picks?"):
            for r in report.picks:
                st.markdown(f"**{r.asset.symbol} — {r.asset.name}** (score {r.score:.0f}): "
                            + ("; ".join(r.reasons) if r.reasons else "balanced risk/return fit")
                            + f". Suggested stop-loss {r.stop_loss:,.2f} / "
                              f"take-profit {r.take_profit:,.2f} (2×ATR risk, 2:1 reward).")

        st.divider()
        a1, a2 = st.columns(2)
        if a1.button("💼 Adopt plan into portfolio (records buys at current prices)"):
            mgr = manager()
            for r in report.picks:
                if r.shares > 0:
                    mgr.buy(r.asset.symbol, r.shares, r.metrics.last_price)
                    mgr.set_rules(r.asset.symbol, stop_loss=r.stop_loss,
                                  take_profit=r.take_profit)
            st.success("Plan adopted — positions and protective rules saved. "
                       "See the Portfolio tab.")
        if a2.button("📅 Create monthly DCA plans instead (capital ÷ 12)"):
            mgr = manager()
            for r in report.picks:
                monthly = round(r.amount / 12, 2)
                if monthly >= 1:
                    mgr.add_dca(r.asset.symbol, monthly, "monthly")
            st.success("Monthly DCA plans created — see the Portfolio tab.")

    st.caption("⚠️ Educational tool — not financial advice. Historical metrics do not "
               "predict future performance.")


# ====================================================================
# TAB 3 — PORTFOLIO
# ====================================================================

with tab_portfolio:
    mgr = manager()
    alerts = check_alerts(mgr, provider())
    if alerts:
        icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        with st.container(border=True):
            st.subheader(f"🚨 Alerts ({len(alerts)})")
            for a in alerts:
                line = f"{icons.get(a.level.value, '·')} **{a.symbol}** — {a.message}"
                if a.level.value == "critical":
                    st.error(line)
                elif a.level.value == "warning":
                    st.warning(line)
                else:
                    st.info(line)

    v = mgr.valuation(provider())
    if v["rows"]:
        t1, t2, t3 = st.columns(3)
        t1.metric("Total value", f"{v['total_value']:,.2f}")
        t2.metric("Total P&L", f"{v['total_pnl']:+,.2f}",
                  f"{(v['total_pnl'] / v['total_cost'] * 100) if v['total_cost'] else 0:+.2f}%")
        t3.metric("Cash (from sales)", f"{v['cash']:,.2f}")

        pos_df = pd.DataFrame(v["rows"])
        st.dataframe(pos_df, use_container_width=True, hide_index=True,
                     column_config={
                         "pnl_pct": st.column_config.NumberColumn("P&L %", format="%.2f%%"),
                         "avg_cost": st.column_config.NumberColumn("Avg cost", format="dollar"),
                         "price": st.column_config.NumberColumn(format="dollar"),
                         "value": st.column_config.NumberColumn(format="dollar"),
                         "pnl": st.column_config.NumberColumn("P&L", format="dollar"),
                     })

        g1, g2 = st.columns(2)
        with g1:
            pie = go.Figure(go.Pie(labels=pos_df["symbol"], values=pos_df["value"],
                                   hole=0.45, textinfo="label+percent"))
            pie.update_layout(title="Portfolio composition", height=380,
                              margin=dict(t=50, b=10))
            st.plotly_chart(pie, use_container_width=True)
        with g2:
            bar = go.Figure(go.Bar(x=pos_df["symbol"], y=pos_df["pnl"],
                                   marker_color=["#26a69a" if x >= 0 else "#ef5350"
                                                 for x in pos_df["pnl"]]))
            bar.update_layout(title="Unrealized P&L by position", height=380,
                              margin=dict(t=50, b=10))
            st.plotly_chart(bar, use_container_width=True)
    else:
        st.info("Portfolio is empty — record a purchase below, or adopt a plan "
                "from the Advisor tab.")

    st.divider()
    c_trade, c_rules, c_dca = st.columns(3)

    with c_trade:
        st.subheader("Record a trade")
        with st.form("trade_form", clear_on_submit=True):
            side = st.radio("Side", ["Buy", "Sell"], horizontal=True)
            t_sym = st.text_input("Symbol", "AAPL").upper().strip()
            t_qty = st.number_input("Quantity", 0.0001, 1e9, 10.0, format="%.4f")
            use_live = st.checkbox("Use current market price", value=True)
            t_price = st.number_input("Price (if not using market)", 0.01, 1e9, 100.0)
            if st.form_submit_button("Save trade"):
                try:
                    px = provider().quote(t_sym).price if use_live else t_price
                    if side == "Buy":
                        pos = manager().buy(t_sym, t_qty, px)
                        st.success(f"Bought {t_qty} {t_sym} @ {px:,.2f} "
                                   f"(avg cost {pos.avg_cost:,.2f})")
                    else:
                        realized = manager().sell(t_sym, t_qty, px)
                        st.success(f"Sold {t_qty} {t_sym} @ {px:,.2f} — "
                                   f"realized {realized:+,.2f}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    with c_rules:
        st.subheader("Protective rules")
        symbols = [p_.symbol for p_ in mgr.portfolio.positions]
        if symbols:
            with st.form("rules_form"):
                r_sym = st.selectbox("Position", symbols)
                cur = mgr.get_position(r_sym).rules
                r_sl = st.number_input("Stop-loss price (0 = none)", 0.0, 1e9,
                                       float(cur.stop_loss or 0.0))
                r_lim = st.number_input("Stop-limit price (0 = none)", 0.0, 1e9,
                                        float(cur.stop_limit or 0.0))
                r_tp = st.number_input("Take-profit price (0 = none)", 0.0, 1e9,
                                       float(cur.take_profit or 0.0))
                r_tr = st.number_input("Trailing stop % (0 = none)", 0.0, 99.0,
                                       float((cur.trailing_stop_pct or 0.0) * 100))
                if st.form_submit_button("Save rules"):
                    manager().set_rules(r_sym, stop_loss=r_sl, stop_limit=r_lim,
                                        take_profit=r_tp, trailing_stop_pct=r_tr / 100)
                    st.success(f"Rules saved for {r_sym}")
                    st.rerun()
        else:
            st.caption("Add a position first.")

    with c_dca:
        st.subheader("DCA plans")
        for plan in mgr.portfolio.dca_plans:
            st.write(f"• **{plan.symbol}** — {plan.amount:,.2f} {plan.frequency}, "
                     f"next {plan.next_run}" + ("" if plan.active else " (paused)"))
        due = [pl for pl in mgr.portfolio.dca_plans if pl.is_due()]
        if due and st.button(f"▶ Execute {len(due)} due DCA purchase(s) at market"):
            mgr2 = manager()
            for plan in [pl for pl in mgr2.portfolio.dca_plans if pl.is_due()]:
                px = provider().quote(plan.symbol).price
                mgr2.execute_dca(plan, px)
            st.success("DCA purchases recorded.")
            st.rerun()
        with st.form("dca_form", clear_on_submit=True):
            d_sym = st.text_input("Symbol", "VTI").upper().strip()
            d_amt = st.number_input("Amount per purchase", 1.0, 1e9, 500.0)
            d_freq = st.selectbox("Frequency", ["weekly", "biweekly", "monthly"], index=2)
            cdl, cdr = st.columns(2)
            if cdl.form_submit_button("Add plan"):
                manager().add_dca(d_sym, d_amt, d_freq)
                st.success(f"DCA plan added for {d_sym}")
                st.rerun()
            if cdr.form_submit_button("Remove plan"):
                manager().remove_dca(d_sym)
                st.success(f"DCA plan removed for {d_sym}")
                st.rerun()

    st.divider()
    st.subheader("🔌 Connect & notifications")
    c_discord, c_import, c_ibkr = st.columns(3)

    with c_discord:
        st.markdown("**Discord alerts**")
        current_url = notify.get_webhook_url(ROOT / "data" / "config.json") or ""
        hook = st.text_input("Webhook URL", current_url, type="password",
                             help="Discord → channel → Edit → Integrations → "
                                  "Webhooks → New Webhook → Copy URL")
        if st.button("Save webhook") and hook.strip():
            notify.set_webhook_url(hook, ROOT / "data" / "config.json")
            st.success("Webhook saved.")
        cb1, cb2 = st.columns(2)
        if cb1.button("Send test ping"):
            ok = notify.send_message("✅ Trading Assistant is connected to "
                                     "this channel.", hook or None)
            st.success("Sent!") if ok else st.error("Failed — check the URL.")
        if cb2.button("Push current alerts"):
            n = notify.send_alerts(alerts, hook or None,
                                   state_path=ROOT / "data" / "alert_state.json")
            st.success(f"Sent {n} alert(s)." if n else
                       "Nothing new to send (dedupe cooldown or no alerts).")
        st.caption("For 24/7 alerts run:  `python -m trading_assistant.cli watch`")

    with c_import:
        st.markdown("**Import positions (CSV)**")
        st.caption("Works for Trade Republic and any broker without an API. "
                   "Columns: symbol, quantity, avg_cost (aliases & `;` accepted).")
        up = st.file_uploader("Positions CSV", type=["csv"])
        replace_csv = st.checkbox("Mirror file exactly (remove others)", value=False)
        if up is not None and st.button("Import CSV"):
            import tempfile
            from trading_assistant.brokers import import_positions_csv
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
                f.write(up.getvalue())
                tmp = f.name
            try:
                result = import_positions_csv(manager(), tmp, replace=replace_csv)
                st.success(f"Added {len(result['added'])}, updated "
                           f"{len(result['updated'])}, removed {len(result['removed'])}.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    with c_ibkr:
        st.markdown("**Interactive Brokers (live sync)**")
        st.caption("Read-only, via the official API. Requires TWS or IB Gateway "
                   "running on this machine with socket clients enabled.")
        ib_host = st.text_input("Host", "127.0.0.1")
        ib_port = st.number_input("Port", 1, 65535, 7497,
                                  help="7497 TWS paper · 7496 TWS live · "
                                       "4002/4001 Gateway")
        ib_merge = st.checkbox("Merge only (don't remove local positions)", value=False)
        if st.button("Sync from IBKR"):
            try:
                from trading_assistant.brokers import (fetch_ibkr_positions,
                                                       sync_positions)
                positions = fetch_ibkr_positions(host=ib_host, port=int(ib_port))
                result = sync_positions(manager(), positions, replace=not ib_merge)
                st.success(f"Synced {len(positions)} position(s): "
                           f"+{len(result['added'])} added, "
                           f"~{len(result['updated'])} updated, "
                           f"-{len(result['removed'])} removed.")
                st.rerun()
            except Exception as e:
                st.error(f"IBKR connection failed: {e}")


# ====================================================================
# TAB 4 — BACKTEST
# ====================================================================

with tab_backtest:
    b1, b2, b3, b4 = st.columns(4)
    bt_sym = b1.text_input("Symbol", "SPY").upper().strip()
    bt_amt = b2.number_input("Amount per DCA purchase", 10.0, 1e7, 500.0)
    bt_freq = b3.selectbox("DCA frequency", ["weekly", "biweekly", "monthly"], index=2)
    bt_period = b4.select_slider("Period", ["6mo", "1y", "2y", "5y"], value="2y")

    if st.button("Run backtest", type="primary"):
        try:
            dca = backtest_dca(provider(), bt_sym, bt_amt, bt_freq, bt_period)
            lump = backtest_lump_sum(provider(), bt_sym, dca.invested, bt_period)
            r1, r2, r3 = st.columns(3)
            r1.metric("Invested", f"{dca.invested:,.0f}")
            r2.metric(f"DCA {bt_freq}", f"{dca.final_value:,.0f}",
                      f"{dca.total_return_pct:+.1f}%")
            r3.metric("Lump sum", f"{lump.final_value:,.0f}",
                      f"{lump.total_return_pct:+.1f}%")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=dca.equity_curve.index, y=dca.equity_curve,
                                     name=f"DCA {bt_freq}", line=dict(color="#29b6f6")))
            fig.add_trace(go.Scatter(x=lump.equity_curve.index, y=lump.equity_curve,
                                     name="Lump sum", line=dict(color="#ffa726")))
            fig.update_layout(title=f"{bt_sym}: DCA vs lump sum over {bt_period}",
                              yaxis_title="Portfolio value", height=480)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("DCA reduces timing risk and entry-point regret; lump sum tends "
                       "to win in steadily rising markets. Past performance ≠ future results.")
        except Exception as e:
            st.error(f"Backtest failed: {e}")
