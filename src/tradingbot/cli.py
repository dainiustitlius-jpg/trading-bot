"""tradingbot: doctor, init, evaluate, pass, run, replay, dashboard, fetch, backtest, sweep, notify-test

Replays: --now "2026-10-19 10:15" pins the New York clock and --data DIR reads
saved data (see skills.ReplayData) instead of the network.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import backtest, history, sweep
from . import config as config_mod
from .bot import Bot, money, replay_day
from .clock import Clock
from .notify import Notifier
from .pipeline import Evaluation, PassResult, Pipeline, Step
from .server import Dashboard, serve
from .skills import SKILLS_DIR, ReplayData, SkillRunner
from .workspace import PLACEHOLDER_UA, Workspace

DEFAULT_CONFIG = Path("config/bot.toml")


def _build(args) -> tuple[config_mod.BotConfig, Workspace, Pipeline]:
    path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else None)
    cfg = config_mod.load(path)
    ws = Workspace(args.workspace or cfg.workspace_dir())
    ws.prepare(cfg.limits, paper=cfg.mode == "paper")
    replay = ReplayData(Path(args.data).resolve()) if args.data else None
    runner = SkillRunner(ws.root.resolve(), clock=Clock(args.now), replay=replay)
    return cfg, ws, Pipeline(cfg, runner, ws)


def _print_step(step: Step) -> None:
    print("  %-4s %-12s %s" % ("ok" if step.ok else "STOP", step.name, step.summary))
    for w in step.warnings:
        print("       %-12s ! %s" % ("", w))


def _print_eval(ev: Evaluation) -> None:
    print("%s: %s" % (ev.symbol, "READY (trader-risk PASS)" if ev.passed else "skipped"))
    for step in ev.steps:
        _print_step(step)


def _print_pass(res: PassResult) -> None:
    print("Pass at %s New York" % res.now)
    _print_step(res.market)
    if res.scan:
        _print_step(res.scan)
    for ev in res.evaluations:
        print()
        _print_eval(ev)
    print()
    chosen = res.chosen
    if chosen:
        o = chosen.order
        print(
            "Order ready: %s %s %s, stop-loss %s, take-profit %s. Nothing was placed."
            % (
                o["side"],
                o["qty"],
                o["symbol"],
                o.get("stop_loss_stop_price"),
                o.get("take_profit_limit_price"),
            )
        )
    elif res.market.ok and (res.scan is None or res.scan.ok):
        print("No candidate passed every gate.")


def _jsonable(obj):
    data = asdict(obj)
    if isinstance(obj, PassResult):
        data["chosen"] = obj.chosen.symbol if obj.chosen else None
    return data


def cmd_doctor(args) -> int:
    problems = 0

    def line(ok: bool, text: str, fatal: bool = True) -> None:
        nonlocal problems
        print("%s %s" % ("ok  " if ok else ("FAIL" if fatal else "warn"), text))
        if not ok and fatal:
            problems += 1

    line(sys.version_info >= (3, 11), "Python %s (need 3.11+)" % sys.version.split()[0])
    cfg, ws, pipe = _build(args)
    missing = pipe.runner.missing()
    line(not missing, "skills in %s%s" % (SKILLS_DIR, (": missing " + ", ".join(missing)) if missing else ""))
    line(cfg.mode == "paper", "mode %s" % cfg.mode, fatal=False)
    line(ws.config_path.is_file(), "workspace config %s" % ws.config_path)
    ua = (os.environ.get("SEC_USER_AGENT") or ws.config().get("sec_user_agent") or "").strip()
    line(
        bool(ua) and ua != PLACEHOLDER_UA,
        "SEC user agent (SEC_USER_AGENT or sec_user_agent): needed for 8-K filings in trader-news",
        fatal=False,
    )
    line(bool(shutil.which("pdftotext")), "pdftotext (optional, trader-pelosi)", fatal=False)
    clock = pipe.market()
    line(clock.data is not None, "NYSE clock: %s" % clock.summary, fatal=False)
    return 1 if problems else 0


def cmd_init(args) -> int:
    cfg, ws, _ = _build(args)
    print("Workspace ready: %s (mode %s)" % (ws.root, cfg.mode))
    print(json.dumps(ws.config(), indent=2))
    return 0


def cmd_evaluate(args) -> int:
    _, _, pipe = _build(args)
    ev = pipe.evaluate(args.symbol)
    if args.json:
        print(json.dumps(_jsonable(ev), indent=2, default=str))
    else:
        _print_eval(ev)
    return 0 if ev.passed else 1


def cmd_pass(args) -> int:
    _, _, pipe = _build(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    res = pipe.run_once(symbols)
    if args.json:
        print(json.dumps(_jsonable(res), indent=2, default=str))
    else:
        _print_pass(res)
    return 0 if res.chosen else 1


def _config_path(args) -> Path:
    return Path(args.config) if args.config else DEFAULT_CONFIG


def _replay(args) -> ReplayData | None:
    return ReplayData(Path(args.data).resolve()) if args.data else None


def _dashboard(args, background: bool):
    dash = Dashboard(_config_path(args), args.workspace)
    httpd = serve(dash, args.host, args.port, background=True)
    print("Pultas: http://%s:%d  (Ctrl+C sustabdo)" % (args.host, args.port), flush=True)
    if not background:
        while True:
            time.sleep(3600)
    return httpd


def cmd_run(args) -> int:
    bot = Bot(_config_path(args), args.workspace)
    if not args.no_dashboard:
        _dashboard(args, background=True)
    print("Botas veikia (%s), aplankas %s. Patikra kas 5 min." % (bot.cfg.mode, bot.ws.root))
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        print("Sustabdyta.")
    return 0


def cmd_replay(args) -> int:
    if not args.workspace:
        raise ValueError("replay needs --workspace (a folder of its own, e.g. runs/replay)")
    bot = Bot(_config_path(args), args.workspace, replay=_replay(args))
    if args.dashboard:  # up first, so the page fills in while the day replays
        _dashboard(args, background=True)
    try:
        replay_day(bot, args.date, args.start, args.end)
        print("Žurnalas: %s" % bot.store.journal_path)
        if args.dashboard:
            print("Pakartojimas baigtas. Pultas veikia, kol paspausite Ctrl+C.")
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("Sustabdyta.")
    return 0


def cmd_dashboard(args) -> int:
    try:
        _dashboard(args, background=False)
    except KeyboardInterrupt:
        pass
    return 0


DEFAULT_SYMBOLS = (
    "AAPL MSFT NVDA AMZN GOOGL META TSLA AMD AVGO NFLX JPM BAC XOM CVX WMT COST KO PEP UNH LLY "
    "JNJ PFE MRK V MA DIS INTC CSCO ORCL CRM ADBE QCOM MU PLTR UBER BA CAT GE F GM SPY"
).split()  # SPY: the market gauge for the market_up filter, never traded


def _symbols(text: str | None) -> list[str] | None:
    return [s.strip().upper() for s in text.split(",") if s.strip()] if text else None


def cmd_fetch(args) -> int:
    symbols = _symbols(args.symbols) or DEFAULT_SYMBOLS
    print("Siunčiama %d akc. 60 dienų 5 min. barų į %s" % (len(symbols), args.history))
    failed = history.fetch(symbols, Path(args.history))
    print(
        "Baigta: %d gerai, %d nepavyko%s"
        % (len(symbols) - len(failed), len(failed), (": " + ", ".join(failed)) if failed else "")
    )
    return 1 if failed and len(failed) == len(symbols) else 0


def cmd_backtest(args) -> int:
    ws = Path(args.workspace or "runs/backtest")
    if args.fast:
        return _backtest_fast(args, ws)
    if args.dashboard:  # up first: the Sandoriai tab follows the backtest day by day
        args.workspace = str(ws)
        _dashboard(args, background=True)
    res = backtest.run(
        _config_path(args) if _config_path(args).is_file() else None,
        Path(args.history),
        ws,
        symbols=_symbols(args.symbols),
        days=args.days,
    )
    print()
    print(
        "Rezultatas %s – %s, %d dienų, %d akc. (naujienų filtras praleistas)"
        % (res["from"], res["to"], res["days"], len(res["symbols"]))
    )
    print(
        "  sandoriai %d · pelningi %s · rezultatas %s · vidutinis R %s · max kritimas %s"
        % (
            res["trades"],
            "%d%%" % round(100 * res["win_rate"]) if res["win_rate"] is not None else "–",
            money(res["pnl"]),
            res["r_avg"] if res["r_avg"] is not None else "–",
            money(res["max_drawdown"]),
        )
    )
    print("  išėjimai: %s" % (", ".join("%s %d" % kv for kv in res["exits"].items()) or "–"))
    print("Failas: %s" % (ws / "bot" / "backtest.json"))
    if args.dashboard:
        print("Backtest baigtas. Pultas veikia, kol paspausite Ctrl+C.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


def _print_result(res: dict, ws: Path) -> None:
    print()
    print(
        "Rezultatas %s – %s, %d dienų, %d akc." % (res["from"], res["to"], res["days"], len(res["symbols"]))
    )
    win = "%d%%" % round(100 * res["win_rate"]) if res["win_rate"] is not None else "–"
    r_avg = res["r_avg"] if res["r_avg"] is not None else "–"
    print(
        "  sandoriai %d · pelningi %s · rezultatas %s · vidutinis R %s · max kritimas %s"
        % (res["trades"], win, money(res["pnl"]), r_avg, money(res["max_drawdown"]))
    )
    print("  išėjimai: %s" % (", ".join("%s %d" % kv for kv in res["exits"].items()) or "–"))
    print("Failas: %s" % (ws / "bot" / "backtest.json"))


def _backtest_fast(args, ws: Path) -> int:
    from . import fastbt
    from .store import BotStore, write_json

    cfg = config_mod.load(_config_path(args) if _config_path(args).is_file() else None)
    print("Greitas režimas: be Time Machine ir naujienų filtrų.")
    data = fastbt.load(Path(args.history), _symbols(args.symbols))
    if not data:
        raise ValueError("no history in %s: run `tradingbot fetch` first" % args.history)
    res = fastbt.run(cfg, data, days=args.days)
    if ws.exists():
        shutil.rmtree(ws)
    store = BotStore(ws)
    for t in res.pop("list"):
        store.add_trade(t)
    write_json(store.dir / "backtest.json", {**res, "status": "done", "done": res["days"]})
    _print_result(res, ws)
    if args.dashboard:
        args.workspace = str(ws)
        print("Pultas veikia, kol paspausite Ctrl+C.")
        _dashboard(args, background=False)
    return 0


def cmd_sweep(args) -> int:
    root = Path(args.workspace or "runs/sweep")
    default = sweep.FULL_VARIANTS if args.full else sweep.DEFAULT_VARIANTS
    texts = [t for t in (args.variants or "").split(";") if t.strip()] or default
    variants = [sweep.parse_variant(t) for t in texts]
    if args.dashboard:  # up first: the Sandoriai tab shows the comparison as it fills in
        args.workspace = str(root)
        _dashboard(args, background=True)
    if args.full:
        res = sweep.run(
            _config_path(args),
            Path(args.history),
            root,
            variants,
            symbols=_symbols(args.symbols),
            days=args.days,
            workers=args.workers,
        )
    else:
        print("Greitas režimas: be Time Machine ir naujienų filtrų. Tikslesniam: --full (lėta).")
        res = sweep.run_fast(
            _config_path(args),
            Path(args.history),
            root,
            variants,
            symbols=_symbols(args.symbols),
            days=args.days,
        )
    print()
    row = "%-4s %-62s %7s %7s %11s %7s %6s %10s"
    print(row % ("#", "variantas", "sandor.", "pelng.", "rezultatas", "vid. R", "t", "max krit."))
    shown_few = False
    for r in res["variants"]:
        if r["status"] != "done":
            print("     %-62s %s" % (r["label"], r.get("error") or r["status"]))
            continue
        if r.get("few") and not shown_few:
            print("--- mažiau nei %d sandorių: per mažai išvadoms ---" % sweep.MIN_TRADES)
            shown_few = True
        win = "%d%%" % round(100 * r["win_rate"]) if r["win_rate"] is not None else "–"
        r_avg = r["r_avg"] if r["r_avg"] is not None else "–"
        t = r.get("r_t") if r.get("r_t") is not None else "–"
        print(
            row
            % (
                r.get("rank", ""),
                r["label"],
                r["trades"],
                win,
                money(r["pnl"]),
                r_avg,
                t,
                money(r["max_drawdown"]),
            )
        )
    print("t: vidutinis R / jo paklaida. Kai |t| < 2, rezultatas gali būti atsitiktinis.")
    print("Failas: %s" % (root / "bot" / "sweep.json"))
    if args.dashboard:
        print("Palyginimas baigtas. Pultas veikia, kol paspausite Ctrl+C.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_notify_test(args) -> int:
    cfg = config_mod.load(_config_path(args) if _config_path(args).is_file() else None)
    n = Notifier(cfg.notify)
    if not n.channels():
        print("Nė vienas kanalas nesukonfigūruotas: žr. [notify] config/bot.toml")
        return 1
    errors = n.send("Bandomasis pranešimas", "Trading bot pranešimai veikia.")
    for ch in n.channels():
        bad = [e for e in errors if e.startswith(ch + ":")]
        print("%-8s %s" % (ch, bad[0] if bad else "išsiųsta"))
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tradingbot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="bot settings (default config/bot.toml if it exists)")
    common.add_argument("--workspace", help="folder holding trader/ (default from the config)")
    common.add_argument("--now", help='pin the New York clock, e.g. "2026-10-19 10:15"')
    common.add_argument("--data", help="replay data folder (scan/, charts/, news/) instead of the network")
    common.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", parents=[common], help="check the setup").set_defaults(func=cmd_doctor)
    sub.add_parser("init", parents=[common], help="create the workspace and trader/config.json").set_defaults(
        func=cmd_init
    )
    p = sub.add_parser("evaluate", parents=[common], help="run one symbol through the chain")
    p.add_argument("symbol")
    p.set_defaults(func=cmd_evaluate)
    p = sub.add_parser("pass", parents=[common], help="one pass: clock, scan, candidates until one passes")
    p.add_argument("--symbols", help="skip the scan and evaluate these, comma separated")
    p.set_defaults(func=cmd_pass)

    web = argparse.ArgumentParser(add_help=False)
    web.add_argument("--host", default="127.0.0.1", help="dashboard address (default: this computer only)")
    web.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("run", parents=[common, web], help="the bot loop, every 5 minutes, with the dashboard")
    p.add_argument("--no-dashboard", action="store_true")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("replay", parents=[common, web], help="tick through one saved day on a pinned clock")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--start", default="09:30")
    p.add_argument("--end", default="16:05")
    p.add_argument("--dashboard", action="store_true", help="serve the dashboard over the result afterwards")
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("dashboard", parents=[common, web], help="only the dashboard")
    p.set_defaults(func=cmd_dashboard)
    p = sub.add_parser("fetch", parents=[common], help="download 60 days of 5-minute bars for a backtest")
    p.add_argument("--symbols", help="comma separated (default: 40 liquid US stocks)")
    p.add_argument("--history", default="data/history")
    p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("backtest", parents=[common, web], help="replay the bot over the saved 60 days")
    p.add_argument("--history", default="data/history")
    p.add_argument("--symbols", help="comma separated (default: every saved symbol)")
    p.add_argument("--days", type=int, help="only the last N tradable days")
    p.add_argument("--dashboard", action="store_true", help="serve the dashboard over the result afterwards")
    p.add_argument(
        "--fast", action="store_true", help="the skills' rules in one process, no Time Machine (seconds)"
    )
    p.set_defaults(func=cmd_backtest)
    p = sub.add_parser("sweep", parents=[common, web], help="the backtest once per settings variant, ranked")
    p.add_argument("--variants", help='e.g. "stop=or-mid,target=1R;stop=or-low,target=2R,flat_by=12:00"')
    p.add_argument("--history", default="data/history")
    p.add_argument("--symbols", help="comma separated (default: every saved symbol)")
    p.add_argument("--days", type=int, help="only the last N tradable days")
    p.add_argument("--workers", type=int, help="variants run at once (default: CPU cores - 1)")
    p.add_argument(
        "--full", action="store_true", help="every skill as its own program, with the Time Machine (slow)"
    )
    p.add_argument("--dashboard", action="store_true", help="serve the dashboard while it runs")
    p.set_defaults(func=cmd_sweep)
    p = sub.add_parser(
        "notify-test", parents=[common], help="send a test message to every configured channel"
    )
    p.set_defaults(func=cmd_notify_test)
    args = ap.parse_args(argv)
    # A Windows console may not encode every character a skill prints; never crash on that.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print("tradingbot: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
