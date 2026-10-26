"""Backtest: replay the whole bot, day by day, over the saved 60 days.

Each day gets its own replay folder (history.build_day) holding no bar from a
later day, and the same Bot ticks through it every 5 minutes on a pinned clock:
scanner, entry, exit, Time Machine, budget, risk, the paper broker. One
workspace carries the paper portfolio from day to day.

Two honest limits: the news gate is skipped (old headlines are not available),
and the first 20 sessions only feed the Time Machine's pool (it needs 20 full
sessions before a day), so they are not traded.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .bot import Bot, replay_day
from .history import build_day, full_sessions, history_file, load_chart
from .skills import ReplayData
from .store import write_json

WARMUP = 20


def t_stat(rs: list[float]) -> float | None:
    """Average R over its standard error. Under about 2 the average could well be luck."""
    n = len(rs)
    if n < 3:
        return None
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / (n - 1)
    return round(mean / (var**0.5 / n**0.5), 2) if var > 0 else None


def summarize(trades: list[dict], budget: float) -> dict:
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    pnl = round(sum(t["pnl"] for t in trades), 2)
    rs = [t["r"] for t in trades if t.get("r") is not None]
    equity, peak, max_dd = budget, budget, 0.0
    for t in trades:
        equity += t["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "trades": n,
        "wins": len(wins),
        "win_rate": round(len(wins) / n, 3) if n else None,
        "pnl": pnl,
        "r_total": round(sum(rs), 2),
        "r_avg": round(sum(rs) / len(rs), 3) if rs else None,
        "r_t": t_stat(rs),
        "max_drawdown": round(max_dd, 2),
        "end_equity": round(budget + pnl, 2),
        "exits": reasons,
    }


def run(
    config_path: Path | None,
    history_dir: Path,
    workspace: Path,
    symbols: list[str] | None = None,
    days: int | None = None,
    echo=print,
) -> dict:
    history_dir = Path(history_dir)
    if symbols is None:
        symbols = sorted(p.name.split("-60d5m")[0] for p in history_dir.glob("*-60d5m.json*"))
    charts = {}
    for sym in symbols:
        path = history_file(history_dir, sym.upper())
        if path is None:
            echo("%s: nėra %s-60d5m.json, praleidžiama" % (sym, sym))
            continue
        charts[sym.upper()] = load_chart(path)
    if not charts:
        raise ValueError("no history in %s: run `tradingbot fetch` first" % history_dir)

    all_days = sorted({d for c in charts.values() for d in full_sessions(c)})
    tradable = all_days[WARMUP:]
    if days:
        tradable = tradable[-days:]
    if not tradable:
        raise ValueError("need more than %d full sessions of history (have %d)" % (WARMUP, len(all_days)))

    workspace = Path(workspace)
    if workspace.exists():
        shutil.rmtree(workspace)  # a backtest always starts from a fresh paper account
    days_dir = workspace / "_days"
    bot = None
    echo("Backtest: %d akc., %d dienų (%s – %s)" % (len(charts), len(tradable), tradable[0], tradable[-1]))
    for i, day in enumerate(tradable, 1):
        folder = days_dir / day.isoformat()
        build_day(charts, day, folder)
        if bot is None:
            bot = Bot(config_path, str(workspace), replay=ReplayData(folder.resolve()), skip_news=True)
        bot.replay = ReplayData(folder.resolve())
        before = len(bot.store.trades())
        replay_day(bot, day.isoformat(), "09:50", "16:05", echo=lambda _: None)
        new = bot.store.trades()[before:]
        line = ", ".join("%s %+.2f (%s)" % (t["symbol"], t["pnl"], t["reason"]) for t in new) or "be sandorių"
        echo("%2d/%d %s %s" % (i, len(tradable), day, line))
        shutil.rmtree(folder, ignore_errors=True)
        write_json(bot.store.dir / "backtest.json", _result(charts, tradable, config_path, bot, i))
    shutil.rmtree(days_dir, ignore_errors=True)
    result = _result(charts, tradable, config_path, bot, len(tradable))
    write_json(bot.store.dir / "backtest.json", result)
    return result


def _result(charts: dict, tradable: list, config_path, bot: Bot, done: int) -> dict:
    """The summary so far; the dashboard reads it while the backtest runs."""

    trades = bot.store.trades()
    budget = float(bot.ws.config().get("budget") or 0)
    return {
        "status": "done" if done >= len(tradable) else "running",
        "done": done,
        "symbols": sorted(charts),
        "from": tradable[0].isoformat(),
        "to": tradable[-1].isoformat(),
        "days": len(tradable),
        "config": str(config_path) if config_path else None,
        "news_gate": "skipped (no historical headlines)",
        **summarize(trades, budget),
        "by_day": _by_day(trades),
    }


def _by_day(trades: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        out[t["date"]] = round(out.get(t["date"], 0) + t["pnl"], 2)
    return out
