"""Today's 5-minute bars for an open position: Yahoo's free chart JSON, or a saved file in a replay.

The skills fetch their own data; this is only for the paper broker, which has
to see each bar's high and low to know whether the stop or the target filled.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .clock import NY

UA = "Mozilla/5.0 (compatible; tradingbot/0.1; paper broker)"
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
BAR = timedelta(minutes=5)


@dataclass(frozen=True)
class Bar:
    start: datetime  # New York
    open: float
    high: float
    low: float
    close: float

    @property
    def end(self) -> datetime:
        return self.start + BAR


class BarsError(RuntimeError):
    pass


def parse_chart(data: dict) -> list[Bar]:
    """Yahoo v8 chart JSON -> complete 5-minute bars on the grid (the live tick is dropped)."""
    try:
        res = data["chart"]["result"][0]
        stamps = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise BarsError("not a Yahoo chart") from exc
    bars = []
    for i, ts in enumerate(stamps):
        start = datetime.fromtimestamp(ts, NY)
        if start.minute % 5 or start.second:
            continue
        o, h, lo, c = (q[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, lo, c):
            continue
        bars.append(Bar(start, float(o), float(h), float(lo), float(c)))
    return bars


def _fetch(symbol: str, timeout: float = 20, range_: str = "1d", interval: str = "5m") -> dict:
    last = None
    for host in HOSTS:
        url = "%s/v8/finance/chart/%s?range=%s&interval=%s" % (
            host,
            symbol.upper().replace(".", "-"),
            range_,
            interval,
        )
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1)
    raise BarsError("Yahoo chart for %s: %s" % (symbol, last))


def _offline(offline_dir: Path, name: str) -> dict:
    for candidate in (name, name + ".gz"):
        path = Path(offline_dir) / candidate
        if path.is_file():
            raw = path.read_bytes()
            if candidate.endswith(".gz"):
                import gzip

                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    raise BarsError("no offline chart %s" % (Path(offline_dir) / name))


def day_chart(symbol: str, offline_dir: Path | None = None) -> dict:
    return _offline(offline_dir, "%s.json" % symbol.upper()) if offline_dir is not None else _fetch(symbol)


def closed_bars(symbol: str, now: datetime, offline_dir: Path | None = None) -> list[Bar]:
    """Today's bars that have closed by `now` (New York)."""
    day = now.date()
    return [b for b in parse_chart(day_chart(symbol, offline_dir)) if b.start.date() == day and b.end <= now]


def prev_close(chart: dict) -> float | None:
    meta = (chart.get("chart", {}).get("result") or [{}])[0].get("meta", {})
    v = meta.get("previousClose") or meta.get("chartPreviousClose")
    return float(v) if v else None


def daily_closes(symbol: str, before: date, offline_dir: Path | None = None) -> list[float]:
    """Daily closes before `before`, oldest first: Yahoo's daily chart, or the last 5-minute bar of
    each saved day in a replay (<SYM>-60d5m.json)."""
    if offline_dir is not None:
        data = _offline(offline_dir, "%s-60d5m.json" % symbol.upper())
        last: dict = {}
        for b in parse_chart(data):
            last[b.start.date()] = b.close
        return [last[d] for d in sorted(last) if d < before]
    data = _fetch(symbol, range_="3mo", interval="1d")
    res = data["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for ts, c in zip(res.get("timestamp") or [], closes, strict=False):
        if c is not None and datetime.fromtimestamp(ts, NY).date() < before:
            out.append(float(c))
    return out
