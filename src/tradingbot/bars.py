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
from datetime import datetime, timedelta
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


def _fetch(symbol: str, timeout: float = 20) -> dict:
    last = None
    for host in HOSTS:
        url = "%s/v8/finance/chart/%s?range=1d&interval=5m" % (host, symbol.upper().replace(".", "-"))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1)
    raise BarsError("Yahoo chart for %s: %s" % (symbol, last))


def closed_bars(symbol: str, now: datetime, offline_dir: Path | None = None) -> list[Bar]:
    """Today's bars that have closed by `now` (New York)."""
    if offline_dir is not None:
        path = Path(offline_dir) / ("%s.json" % symbol.upper())
        if not path.is_file():
            raise BarsError("no offline chart %s" % path)
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = _fetch(symbol)
    day = now.date()
    return [b for b in parse_chart(data) if b.start.date() == day and b.end <= now]
