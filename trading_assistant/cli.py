"""Command-line interface for the trading assistant.

Examples:
    python -m trading_assistant.cli quote AAPL MSFT
    python -m trading_assistant.cli advise --risk 7 --growth 12 --capital 25000 --crypto
    python -m trading_assistant.cli buy AAPL 10 182.50
    python -m trading_assistant.cli protect AAPL --stop-loss 165 --take-profit 220
    python -m trading_assistant.cli dca add VTI 500 monthly
    python -m trading_assistant.cli alerts
    python -m trading_assistant.cli backtest SPY --amount 500 --frequency monthly
"""
from __future__ import annotations

import argparse
import sys

from .advisor import AdvisorEngine, InvestorProfile
from .backtest import backtest_dca, backtest_lump_sum
from .data import get_provider
from .notify import get_webhook_url, send_alerts, send_message, set_webhook_url
from .portfolio import PortfolioManager, check_alerts

DISCLAIMER = ("\n⚠ Educational tool — not financial advice. "
              "Past performance does not guarantee future results.\n")


def _provider(args):
    p = get_provider(force=getattr(args, "source", None))
    if not p.is_live:
        print("⚠ Live data unavailable — using OFFLINE SIMULATED data.\n")
    return p


def cmd_quote(args) -> None:
    p = _provider(args)
    for sym in args.symbols:
        try:
            q = p.quote(sym)
            print(f"{q.symbol:>10}  {q.price:>12,.2f} {q.currency}  "
                  f"{q.change:+.2f} ({q.change_pct:+.2f}%)  {q.name}")
        except Exception as e:
            print(f"{sym:>10}  error: {e}")


def cmd_advise(args) -> None:
    p = _provider(args)
    profile = InvestorProfile(
        risk_tolerance=args.risk, target_annual_growth=args.growth / 100.0,
        horizon_years=args.horizon, capital=args.capital,
        include_crypto=args.crypto,
        excluded_sectors=args.exclude or [],
        preferred_sectors=args.prefer or [])
    report = AdvisorEngine(p).advise(profile, top_n=args.top)

    print(f"\nProfile: {profile.label} (risk {profile.risk_tolerance}/10), "
          f"target {profile.target_annual_growth:.0%}/yr, "
          f"{profile.horizon_years:.0f}y horizon, capital {profile.capital:,.0f}")
    print(report.target_comment)
    goal_note = ""
    if report.goal:
        goal_note = (f"; Monte Carlo says {report.goal.probability:.0%} chance of "
                     f"reaching {report.goal.target_value:,.0f} in "
                     f"{profile.horizon_years:.0f}y")
    print(f"\nSuggested portfolio (hist. return ~{report.expected_return:.1%}/yr, "
          f"diversified volatility {report.expected_volatility:.1%}{goal_note}):\n")
    hdr = f"{'SYMBOL':>8} {'WEIGHT':>7} {'AMOUNT':>10} {'SHARES':>10} {'PRICE':>10} {'STOP':>9} {'TARGET':>9}  SCORE"
    print(hdr)
    print("-" * len(hdr))
    for r in report.picks:
        print(f"{r.asset.symbol:>8} {r.weight:>6.1%} {r.amount:>10,.0f} "
              f"{r.shares:>10,.3f} {r.metrics.last_price:>10,.2f} "
              f"{r.stop_loss:>9,.2f} {r.take_profit:>9,.2f}  {r.score:>5.1f}")
        for reason in r.reasons[:2]:
            print(f"{'':>9}· {reason}")
    print(DISCLAIMER)


def cmd_buy(args) -> None:
    mgr = PortfolioManager()
    pos = mgr.buy(args.symbol, args.quantity, args.price)
    print(f"Bought {args.quantity} {pos.symbol} @ {args.price:.2f} "
          f"(now {pos.quantity} shares, avg cost {pos.avg_cost:.2f})")


def cmd_sell(args) -> None:
    mgr = PortfolioManager()
    realized = mgr.sell(args.symbol, args.quantity, args.price)
    print(f"Sold {args.quantity} {args.symbol.upper()} @ {args.price:.2f} "
          f"— realized P&L {realized:+,.2f}")


def cmd_protect(args) -> None:
    mgr = PortfolioManager()
    pos = mgr.set_rules(args.symbol, stop_loss=args.stop_loss,
                        stop_limit=args.stop_limit, take_profit=args.take_profit,
                        trailing_stop_pct=(args.trailing / 100.0) if args.trailing else None)
    r = pos.rules
    print(f"{pos.symbol} protection: stop-loss={r.stop_loss} stop-limit={r.stop_limit} "
          f"take-profit={r.take_profit} trailing={r.trailing_stop_pct}")


def cmd_portfolio(args) -> None:
    mgr = PortfolioManager()
    p = _provider(args)
    v = mgr.valuation(p)
    if not v["rows"]:
        print("Portfolio is empty. Add positions with: buy SYMBOL QTY PRICE")
        return
    hdr = f"{'SYMBOL':>8} {'QTY':>10} {'AVG COST':>10} {'PRICE':>10} {'VALUE':>12} {'P&L':>11} {'P&L %':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in v["rows"]:
        print(f"{r['symbol']:>8} {r['quantity']:>10,.3f} {r['avg_cost']:>10,.2f} "
              f"{r['price']:>10,.2f} {r['value']:>12,.2f} {r['pnl']:>+11,.2f} "
              f"{r['pnl_pct']:>+7.2f}%")
    print("-" * len(hdr))
    print(f"{'TOTAL':>8} {'':>32} {v['total_value']:>12,.2f} {v['total_pnl']:>+11,.2f}")
    for plan in mgr.portfolio.dca_plans:
        print(f"DCA: {plan.amount:,.0f} into {plan.symbol} {plan.frequency}, "
              f"next {plan.next_run}{'' if plan.active else ' (paused)'}")


def cmd_dca(args) -> None:
    mgr = PortfolioManager()
    if args.action == "add":
        plan = mgr.add_dca(args.symbol, args.amount, args.frequency)
        print(f"DCA plan: {plan.amount:,.2f} into {plan.symbol} {plan.frequency}, "
              f"next run {plan.next_run}")
    elif args.action == "remove":
        mgr.remove_dca(args.symbol)
        print(f"Removed DCA plan for {args.symbol.upper()}")
    elif args.action == "run":
        p = _provider(args)
        due = [pl for pl in mgr.portfolio.dca_plans if pl.is_due()]
        if not due:
            print("No DCA purchases due.")
        for plan in due:
            price = p.quote(plan.symbol).price
            pos = mgr.execute_dca(plan, price)
            print(f"Executed DCA: {plan.amount:,.2f} → {plan.amount/price:.4f} "
                  f"{plan.symbol} @ {price:.2f} (next {plan.next_run}); "
                  f"position now {pos.quantity:.4f} shares")


def cmd_alerts(args) -> None:
    mgr = PortfolioManager()
    p = _provider(args)
    alerts = check_alerts(mgr, p)
    if not alerts:
        print("✓ No alerts — all positions within their rules.")
        return
    icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
    for a in alerts:
        print(f"{icons.get(a.level.value, '·')} [{a.kind}] {a.message}")
    if getattr(args, "notify", False):
        sent = send_alerts(alerts)
        if sent:
            print(f"→ {sent} alert(s) sent to Discord.")
        elif get_webhook_url() is None:
            print("→ Discord not configured. Run: config discord <webhook-url>")
        else:
            print("→ Nothing new to send (all alerts within the dedupe cooldown).")


def cmd_watch(args) -> None:
    """Poll alerts on an interval and push new ones to Discord."""
    import time
    if get_webhook_url() is None and args.notify:
        print("Discord not configured. Run: config discord <webhook-url>")
        return
    p = _provider(args)
    print(f"Watching portfolio every {args.interval}s (Ctrl-C to stop)…")
    while True:
        mgr = PortfolioManager()
        alerts = check_alerts(mgr, p)
        if alerts:
            for a in alerts:
                print(f"[{a.level.value}] {a.message}")
            if args.notify:
                sent = send_alerts(alerts)
                if sent:
                    print(f"→ {sent} alert(s) sent to Discord.")
        else:
            print("✓ no alerts")
        if args.once:
            return
        time.sleep(args.interval)


def cmd_config(args) -> None:
    if args.key == "discord":
        set_webhook_url(args.value)
        print("Discord webhook saved to data/config.json")


def cmd_notify(args) -> None:
    if args.action == "test":
        ok = send_message("✅ Trading Assistant is connected to this channel.")
        print("Test message sent." if ok else
              "Failed — set a webhook first: config discord <webhook-url>")


def cmd_sync(args) -> None:
    from .brokers import fetch_ibkr_positions, sync_positions
    print(f"Connecting to IBKR at {args.host}:{args.port} (read-only)…")
    positions = fetch_ibkr_positions(host=args.host, port=args.port,
                                     client_id=args.client_id)
    if not positions:
        print("No positions reported by IBKR.")
        return
    result = sync_positions(PortfolioManager(), positions, replace=not args.merge)
    print(f"Synced {len(positions)} position(s): "
          f"added {result['added'] or '—'}, updated {result['updated'] or '—'}, "
          f"removed {result['removed'] or '—'}")


def cmd_import_csv(args) -> None:
    from .brokers import import_positions_csv
    result = import_positions_csv(PortfolioManager(), args.path,
                                  replace=args.replace)
    print(f"Imported: added {result['added'] or '—'}, "
          f"updated {result['updated'] or '—'}, removed {result['removed'] or '—'}")


def cmd_backtest(args) -> None:
    p = _provider(args)
    dca = backtest_dca(p, args.symbol, args.amount, args.frequency, args.period)
    lump = backtest_lump_sum(p, args.symbol, dca.invested, args.period)
    print(f"\nBacktest {args.symbol.upper()} over {args.period}:")
    for r in (dca, lump):
        print(f"  {r.strategy:<14} invested {r.invested:>10,.0f} → "
              f"{r.final_value:>10,.0f}  ({r.total_return_pct:+.1f}%, "
              f"{r.num_purchases} purchase(s))")
    print(DISCLAIMER)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="trading-assistant",
                                 description="Investment advisor & portfolio tracker")
    ap.add_argument("--source", choices=["yahoo", "offline"], default=None,
                    help="force a data source (default: auto)")
    sub = ap.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quote", help="live quotes")
    q.add_argument("symbols", nargs="+")
    q.set_defaults(func=cmd_quote)

    a = sub.add_parser("advise", help="get investment recommendations")
    a.add_argument("--risk", type=int, default=5, help="risk tolerance 1-10")
    a.add_argument("--growth", type=float, default=8, help="target annual growth %%")
    a.add_argument("--horizon", type=float, default=5, help="years")
    a.add_argument("--capital", type=float, default=10000)
    a.add_argument("--top", type=int, default=8)
    a.add_argument("--crypto", action="store_true", help="allow crypto assets")
    a.add_argument("--exclude", nargs="*", help="sectors to exclude")
    a.add_argument("--prefer", nargs="*", help="sectors to favor")
    a.set_defaults(func=cmd_advise)

    b = sub.add_parser("buy", help="record a purchase")
    b.add_argument("symbol"); b.add_argument("quantity", type=float)
    b.add_argument("price", type=float)
    b.set_defaults(func=cmd_buy)

    s = sub.add_parser("sell", help="record a sale")
    s.add_argument("symbol"); s.add_argument("quantity", type=float)
    s.add_argument("price", type=float)
    s.set_defaults(func=cmd_sell)

    pr = sub.add_parser("protect", help="set stop-loss/take-profit/trailing rules")
    pr.add_argument("symbol")
    pr.add_argument("--stop-loss", type=float)
    pr.add_argument("--stop-limit", type=float)
    pr.add_argument("--take-profit", type=float)
    pr.add_argument("--trailing", type=float, help="trailing stop %% (e.g. 10)")
    pr.set_defaults(func=cmd_protect)

    pf = sub.add_parser("portfolio", help="show holdings & P&L")
    pf.set_defaults(func=cmd_portfolio)

    d = sub.add_parser("dca", help="manage dollar-cost-averaging plans")
    d.add_argument("action", choices=["add", "remove", "run"])
    d.add_argument("symbol", nargs="?", default="")
    d.add_argument("amount", nargs="?", type=float, default=0.0)
    d.add_argument("frequency", nargs="?", default="monthly",
                   choices=["daily", "weekly", "biweekly", "monthly"])
    d.set_defaults(func=cmd_dca)

    al = sub.add_parser("alerts", help="check stop/target/DCA alerts")
    al.add_argument("--notify", action="store_true", help="push alerts to Discord")
    al.set_defaults(func=cmd_alerts)

    w = sub.add_parser("watch", help="poll alerts on an interval, push to Discord")
    w.add_argument("--interval", type=int, default=300, help="seconds between checks")
    w.add_argument("--notify", action="store_true", default=True)
    w.add_argument("--once", action="store_true", help="single check then exit")
    w.set_defaults(func=cmd_watch)

    cf = sub.add_parser("config", help="store settings (e.g. Discord webhook)")
    cf.add_argument("key", choices=["discord"])
    cf.add_argument("value")
    cf.set_defaults(func=cmd_config)

    no = sub.add_parser("notify", help="notification utilities")
    no.add_argument("action", choices=["test"])
    no.set_defaults(func=cmd_notify)

    sy = sub.add_parser("sync", help="pull live positions from IBKR (TWS/Gateway)")
    sy.add_argument("broker", choices=["ibkr"])
    sy.add_argument("--host", default="127.0.0.1")
    sy.add_argument("--port", type=int, default=7497,
                    help="7497 TWS paper, 7496 TWS live, 4002/4001 Gateway")
    sy.add_argument("--client-id", type=int, default=17)
    sy.add_argument("--merge", action="store_true",
                    help="only add/update — don't remove local positions missing at the broker")
    sy.set_defaults(func=cmd_sync)

    ic = sub.add_parser("import-csv", help="import positions from a CSV "
                                           "(Trade Republic & others)")
    ic.add_argument("path")
    ic.add_argument("--replace", action="store_true",
                    help="mirror the file exactly instead of merging")
    ic.set_defaults(func=cmd_import_csv)

    bt = sub.add_parser("backtest", help="DCA vs lump-sum backtest")
    bt.add_argument("symbol")
    bt.add_argument("--amount", type=float, default=500)
    bt.add_argument("--frequency", default="monthly",
                    choices=["daily", "weekly", "biweekly", "monthly"])
    bt.add_argument("--period", default="2y")
    bt.set_defaults(func=cmd_backtest)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
