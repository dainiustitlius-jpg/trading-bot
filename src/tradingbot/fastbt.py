"""Fast backtest: the same rules, in one process, in seconds instead of hours.

The full backtest drives every skill as a separate program every 5 minutes,
which is faithful but slow. This one loads the skills' own pure functions once
and calls them directly:

  trader-entry   evaluate()     the opening-range breakout trigger
  trader-exit    compute()      the stop and the target, in exact cents
  trader-budget  size_trade()   the shares, and every refusal
  trader-risk    check()        PASS / BLOCK with the day's limits

and walks the bars with the same paper-broker rules (paper.scan_bars). So the
decisions are the skills' decisions; what it leaves out is the Time Machine
filter (10,000 replays per candidate is the slow part) and the news gate.
When several stocks break out on the same bar, the most traded so far goes
first, as the scanner lists them.
"""

from __future__ import annotations

import importlib.util
import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from . import config as config_mod
from . import filters
from .backtest import WARMUP, summarize
from .bars import Bar
from .clock import NY
from .history import full_sessions, history_file, load_chart
from .paper import cents, scan_bars, trade_record
from .skills import SKILLS_DIR
from .workspace import EXAMPLE_CONFIG

_MODS: dict = {}


def skill(folder: str, name: str):
    """A skill script loaded as a module (its folder name has a hyphen)."""
    key = folder + "/" + name
    if key not in _MODS:
        spec = importlib.util.spec_from_file_location("skill_" + name, SKILLS_DIR / folder / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODS[key] = mod
    return _MODS[key]


def _utc(local: datetime) -> datetime:
    """Aware New York -> naive UTC, the skills' clock."""
    return local.astimezone(UTC).replace(tzinfo=None)


class DayData:
    """One symbol, one session: raw bars for trader-entry and Bar objects for the paper broker."""

    def __init__(self, symbol: str, raw: list[dict], prev_close, closes_before: list[float] | None = None):
        self.symbol = symbol
        self.raw = raw
        self.prev_close = prev_close
        self.closes_before = closes_before or []  # the daily closes before this day, oldest first
        self.cache: dict = {}
        self.bars = [
            Bar(datetime.fromtimestamp(b["t"], NY), b["o"], b["h"], b["l"], b["c"])
            for b in raw
            if b["t"] % 300 == 0
        ]

    def chart(self) -> dict:
        return {
            "symbol": self.symbol,
            "meta": {},
            "bars": self.raw,
            "prev_close": self.prev_close,
            "data_time": None,
        }

    def dollar_volume(self, until: datetime) -> float:
        ts = until.timestamp()
        return sum(b["c"] * b["v"] for b in self.raw if b["t"] + 300 <= ts)


def load(history_dir: Path, symbols: list[str] | None = None) -> dict[date, dict[str, DayData]]:
    """{day: {symbol: DayData}} for every full session in the saved history."""
    history_dir = Path(history_dir)
    if symbols is None:
        symbols = sorted(p.name.split("-60d5m")[0] for p in history_dir.glob("*-60d5m.json*"))
    out: dict[date, dict[str, DayData]] = {}
    for sym in symbols:
        path = history_file(history_dir, sym.upper())
        if path is None:
            continue
        chart = load_chart(path)
        res = chart["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        full = set(full_sessions(chart))
        by_day: dict[date, list[dict]] = {}
        for i, t in enumerate(res.get("timestamp") or []):
            c = q["close"][i]
            if c is None:
                continue
            d = datetime.fromtimestamp(t, NY).date()
            if d not in full:
                continue
            o, h, lo, v = q["open"][i], q["high"][i], q["low"][i], q["volume"][i]
            by_day.setdefault(d, []).append(
                {
                    "t": int(t),
                    "o": float(c if o is None else o),
                    "h": float(c if h is None else h),
                    "l": float(c if lo is None else lo),
                    "c": float(c),
                    "v": int(v or 0),
                }
            )
        closes: list[float] = []
        for d in sorted(by_day):
            out.setdefault(d, {})[sym.upper()] = DayData(
                sym.upper(), by_day[d], closes[-1] if closes else None, closes[-40:]
            )
            closes.append(by_day[d][-1]["c"])
    return out


def _limits(cfg: config_mod.BotConfig) -> dict:
    import json

    limits = json.loads(EXAMPLE_CONFIG.read_text())
    limits.update(cfg.limits)
    limits["paper"] = True
    return limits


def _parse_stop(text: str):
    rule, param = skill("trader-exit", "exit").parse_stop(text)
    if rule == "atr":
        raise ValueError("the fast backtest has no ATR stop yet; use the full backtest for atr:K")
    return rule, param


def simulate_day(
    day: date,
    data: dict[str, DayData],
    cfg: config_mod.BotConfig,
    limits: dict,
    cash: float,
    slippage_bps: float,
) -> tuple[list[dict], float]:
    """One trading day. Returns (closed trades, cash at the end)."""
    entry_mod = skill("trader-entry", "entry")
    exit_mod = skill("trader-exit", "exit")
    size_mod = skill("trader-budget", "size")
    risk_mod = skill("trader-risk", "check")
    stop_rule, stop_param = _parse_stop(cfg.plan.stop)
    target_rule, target_param = exit_mod.parse_target(cfg.plan.target)
    flat_by = datetime.combine(day, time.fromisoformat(limits.get("flat_by") or "15:55"), NY)
    cutoff = datetime.combine(day, time.fromisoformat(limits.get("no_entries_after") or "15:30"), NY)
    max_age = timedelta(minutes=cfg.scan.max_trigger_age_minutes)
    min_price = cfg.scan.min_price
    entry_cfg = {
        "no_entries_before": limits.get("no_entries_before", "09:45"),
        "no_entries_after": limits.get("no_entries_after", "15:30"),
    }

    # Every symbol's first close above its opening range: it does not depend on the hour asked.
    triggers = []
    ask = _utc(cutoff + timedelta(minutes=5))
    f = cfg.filters
    market = data.get(f.market_symbol.upper())
    for sym, dd in data.items():
        if sym == f.market_symbol.upper():
            continue  # the market gauge (an ETF) is not traded
        key = (ask, f.min_volume_x, tuple(sorted(entry_cfg.items())))
        res = dd.cache.get(key)
        if res is None:  # the same day is asked again by every variant of a sweep
            res = dd.cache[key] = entry_mod.evaluate(
                dd.chart(), ask, cfg=entry_cfg, min_volume_x=f.min_volume_x or None
            )
        if res.get("status") != "triggered":
            continue
        tr = res["trigger"]
        end = datetime.combine(day, time.fromisoformat(tr["close_time"]), NY)
        if tr["price"] < min_price:
            continue
        ctx = {
            "price": tr["price"],
            "open": dd.raw[0]["o"],
            "prev_close": dd.prev_close,
            "ma20": filters.average(dd.closes_before),
        }
        triggers.append(
            {"symbol": sym, "end": end, "price": tr["price"], "or": res["opening_range"], "ctx": ctx}
        )

    state = {"orders": []}
    trades, pos, traded = [], None, set()
    t = datetime.combine(day, time(9, 50), NY)
    close = datetime.combine(day, time(16, 0), NY)
    while t <= close:
        if pos is not None:
            dd = data[pos["symbol"]]
            ex, update = scan_bars(pos, [b for b in dd.bars if b.end <= t], t, slippage_bps)
            pos.update(update)
            if ex is not None:
                trade = trade_record(pos, ex, "paper")
                cash = cents(cash + pos["qty"] * ex.price)
                state["orders"].append(
                    {
                        "at": t.isoformat(),
                        "date": day.isoformat(),
                        "status": "filled",
                        "symbol": pos["symbol"],
                        "side": "sell",
                        "qty": str(pos["qty"]),
                    }
                )
                trades.append(trade)
                pos = None
        elif t <= cutoff + max_age:
            fresh = [tr for tr in triggers if tr["symbol"] not in traded and t - max_age <= tr["end"] <= t]
            fresh.sort(key=lambda tr: (tr["end"], data[tr["symbol"]].dollar_volume(t)), reverse=True)
            for tr in fresh:
                if filters.context_wanted(f):  # measured at the tick, as the bot does
                    ctx = {**tr["ctx"], "market_pct": _market_pct(market, t)}
                    if not filters.check_context(ctx, f).ok:
                        continue
                pos = _try_entry(
                    tr,
                    t,
                    day,
                    cfg,
                    limits,
                    cash,
                    state,
                    slippage_bps,
                    flat_by,
                    (stop_rule, stop_param, target_rule, target_param),
                    exit_mod,
                    size_mod,
                    risk_mod,
                )
                if pos is not None:
                    traded.add(tr["symbol"])
                    cash = cents(cash - pos["qty"] * pos["entry"])
                    break
        t += timedelta(minutes=5)
    return trades, cash


def _market_pct(market: DayData | None, at: datetime) -> float | None:
    """The market gauge's move from yesterday's close to its last bar closed by `at`, in %."""
    if market is None or not market.prev_close:
        return None
    done = [b for b in market.bars if b.end <= at]
    return 100.0 * (done[-1].close / market.prev_close - 1) if done else None


def _try_entry(
    tr, t, day, cfg, limits, cash, state, slippage_bps, flat_by, rules, exit_mod, size_mod, risk_mod
):
    stop_rule, stop_param, target_rule, target_param = rules
    if tr["end"] >= flat_by:
        return None  # trader-exit refuses an entry at or after flat_by
    try:
        plan = exit_mod.compute(
            tr["price"],
            stop_rule,
            stop_param,
            target_rule,
            target_param,
            or_high=tr["or"]["high"],
            or_low=tr["or"]["low"],
        )
    except exit_mod.PlanError:
        return None
    p = {
        "symbol": tr["symbol"],
        "side": "buy",
        "date": day.isoformat(),
        "setup": "orb",
        "entry": float(plan["entry"]),
        "stop": float(plan["stop"]),
        "target": float(plan["target"]),
        "flat_by": flat_by.strftime("%H:%M"),
    }
    s = size_mod.size_trade(p, limits, cash=cash, today=day.isoformat())
    order = size_mod.bracket_order(s)
    if order is None:
        return None
    verdict = risk_mod.check(
        [order],
        limits,
        portfolio={"cash": cash, "positions": []},
        state=state,
        today=day.isoformat(),
        now=_utc(t),
    )["results"][0]
    if verdict["verdict"] != "PASS":
        return None
    qty = int(order["qty"])
    fill = cents(p["entry"] * (1 + slippage_bps / 10_000))
    state["orders"].append(
        {
            "at": t.isoformat(),
            "date": day.isoformat(),
            "status": "placed",
            "kind": "entry",
            "symbol": tr["symbol"],
            "side": "buy",
            "qty": order["qty"],
            "max_loss": verdict["max_loss"],
        }
    )
    return {
        "symbol": tr["symbol"],
        "qty": qty,
        "entry": fill,
        "entry_plan": p["entry"],
        "entry_at": t.isoformat(timespec="seconds"),
        "stop": p["stop"],
        "target": p["target"],
        "max_loss": verdict["max_loss"],
        "risk_per_share": s["risk_per_share"],
        "flat_by": p["flat_by"],
        "order_id": "fast-%s-%s" % (day.isoformat(), tr["symbol"]),
        "last_price": fill,
        "last_price_at": t.isoformat(timespec="seconds"),
        "checked_until": None,
    }


def run(
    cfg: config_mod.BotConfig,
    days_data: dict[date, dict[str, DayData]],
    days: int | None = None,
    warmup: int = WARMUP,
) -> dict:
    """The whole period with one paper account. Same day range as the full backtest by default."""
    limits = _limits(cfg)
    all_days = sorted(days_data)
    tradable = all_days[warmup:]
    if days:
        tradable = tradable[-days:]
    if not tradable:
        raise ValueError("need more than %d full sessions of history (have %d)" % (warmup, len(all_days)))
    budget = float(limits.get("budget") or 0)
    cash, trades = budget, []
    for d in tradable:
        new, cash = simulate_day(d, days_data[d], cfg, limits, cash, cfg.paper.slippage_bps)
        trades.extend(new)
    res = {
        "mode": "fast",
        "symbols": sorted({s for d in tradable for s in days_data[d]}),
        "from": tradable[0].isoformat(),
        "to": tradable[-1].isoformat(),
        "days": len(tradable),
        "news_gate": "skipped",
        "timemachine_gate": "skipped (fast mode)",
        **summarize(trades, budget),
        "list": trades,
    }
    if not math.isclose(res["end_equity"], cash, abs_tol=0.02):
        res["warning"] = "cash %.2f does not match the equity %.2f" % (cash, res["end_equity"])
    return res
