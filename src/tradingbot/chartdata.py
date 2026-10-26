"""Candles, indicators and the bot's own marks for the dashboard's chart.

Times go out as "New York wall clock in seconds": Lightweight Charts draws
timestamps as UTC, so each bar's New York offset is added to keep the axis in
market time whatever the viewer's computer is set to.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from . import bars, indicators
from .clock import NY
from .store import BotStore

SYMBOL = re.compile(r"^[A-Z][A-Z.\-]{0,9}$|^\^[A-Z]{1,8}$")
RANGES = {"5m": ("5d", "5m"), "1d": ("1y", "1d")}
_CACHE: dict = {}
TTL = 60


def wall(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, NY)
    return ts + int(dt.utcoffset().total_seconds())


def _candles_from_chart(data: dict, intraday: bool) -> list[dict]:
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(res.get("timestamp") or []):
        c = q["close"][i]
        if c is None or (intraday and ts % 300):
            continue  # a null bar, or Yahoo's live tick between bars
        o, h, lo, v = q["open"][i], q["high"][i], q["low"][i], q["volume"][i]
        out.append(
            {
                "ts": int(ts),
                "o": float(c if o is None else o),
                "h": float(c if h is None else h),
                "l": float(c if lo is None else lo),
                "c": float(c),
                "v": int(v or 0),
            }
        )
    return out


def _daily_from_intraday(candles: list[dict]) -> list[dict]:
    days: dict = {}
    for b in candles:
        d = datetime.fromtimestamp(b["ts"], NY).date()
        if d not in days:
            days[d] = dict(b)
        else:
            day = days[d]
            day["h"], day["l"], day["c"] = max(day["h"], b["h"]), min(day["l"], b["l"]), b["c"]
            day["v"] += b["v"]
    return [days[d] for d in sorted(days)]


def load_candles(symbol: str, interval: str, offline_dir: Path | None = None) -> list[dict]:
    """Yahoo (cached a minute), or a replay's saved charts: <SYM>.json for 5m, <SYM>-60d5m for 1d."""
    if not SYMBOL.match(symbol):
        raise ValueError("not a ticker: %r" % symbol)
    if interval not in RANGES:
        raise ValueError("interval is 5m or 1d")
    if offline_dir is not None:
        if interval == "5m":
            return _candles_from_chart(bars.day_chart(symbol, offline_dir), True)
        return _daily_from_intraday(
            _candles_from_chart(bars._offline(offline_dir, "%s-60d5m.json" % symbol), True)
        )
    key = (symbol, interval)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    rng, iv = RANGES[interval]
    candles = _candles_from_chart(bars._fetch(symbol, range_=rng, interval=iv), interval == "5m")
    _CACHE[key] = (time.time(), candles)
    return candles


def _series(times: list[int], values: list) -> list[dict]:
    return [{"time": t, "value": round(v, 4)} for t, v in zip(times, values, strict=True) if v is not None]


def build(symbol: str, interval: str, store: BotStore | None = None, offline_dir: Path | None = None) -> dict:
    candles = load_candles(symbol, interval, offline_dir)
    times = [wall(b["ts"]) for b in candles]
    closes = [b["c"] for b in candles]
    up, mid, lo = indicators.bollinger(closes)
    out = {
        "symbol": symbol,
        "interval": interval,
        "candles": [
            {"time": t, "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"]}
            for t, b in zip(times, candles, strict=True)
        ],
        "volume": [
            {"time": t, "value": b["v"], "up": b["c"] >= b["o"]} for t, b in zip(times, candles, strict=True)
        ],
        "lines": {
            "ema9": _series(times, indicators.ema(closes, 9)),
            "ema20": _series(times, indicators.ema(closes, 20)),
            "sma50": _series(times, indicators.sma(closes, 50)),
            "sma200": _series(times, indicators.sma(closes, 200)),
            "bb_upper": _series(times, up),
            "bb_mid": _series(times, mid),
            "bb_lower": _series(times, lo),
        },
        "rsi": _series(times, indicators.rsi(closes, 14)),
        "levels": [],
        "markers": [],
    }
    if interval == "5m" and candles:
        sessions = [datetime.fromtimestamp(b["ts"], NY).date() for b in candles]
        out["lines"]["vwap"] = _series(
            times,
            indicators.vwap(
                [b["h"] for b in candles],
                [b["l"] for b in candles],
                closes,
                [b["v"] for b in candles],
                sessions,
            ),
        )
        last = sessions[-1]
        rng = [b for b, s in zip(candles, sessions, strict=True) if s == last][
            :3
        ]  # 9:30-9:45, as trader-entry
        if rng:
            out["levels"] += [
                {"price": max(b["h"] for b in rng), "title": "OR high", "kind": "or"},
                {"price": min(b["l"] for b in rng), "title": "OR low", "kind": "or"},
            ]
    if store is not None:
        _add_bot_marks(out, symbol, interval, store)
    return out


def _bar_time(iso: str, interval: str) -> int:
    """A trade time snapped to the bar it belongs to on this chart."""
    dt = datetime.fromisoformat(iso).astimezone(NY)
    if interval == "1d":
        dt = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        dt = dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)
    return wall(int(dt.timestamp()))


def _add_bot_marks(out: dict, symbol: str, interval: str, store: BotStore) -> None:
    for t in store.trades():
        if t.get("symbol") != symbol:
            continue
        good = t["pnl"] >= 0
        out["markers"].append(
            {
                "time": _bar_time(t["entry_at"], interval),
                "position": "belowBar",
                "shape": "arrowUp",
                "text": "pirkta %s" % t["entry"],
                "kind": "entry",
            }
        )
        out["markers"].append(
            {
                "time": _bar_time(t["exit_at"], interval),
                "position": "aboveBar",
                "shape": "arrowDown",
                "text": "%s %+.2f" % (t["reason"], t["pnl"]),
                "kind": "win" if good else "loss",
            }
        )
    pos = (store.state() or {}).get("position")
    if pos and pos.get("symbol") == symbol:
        out["levels"] += [
            {"price": pos["entry"], "title": "įėjimas", "kind": "entry"},
            {"price": pos["stop"], "title": "stop", "kind": "stop"},
            {"price": pos["target"], "title": "target", "kind": "target"},
        ]
        out["markers"].append(
            {
                "time": _bar_time(pos["entry_at"], interval),
                "position": "belowBar",
                "shape": "arrowUp",
                "text": "pirkta %s" % pos["entry"],
                "kind": "entry",
            }
        )
    out["markers"].sort(key=lambda m: m["time"])
