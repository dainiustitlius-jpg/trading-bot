"""60 days of 5-minute bars per symbol, and the per-day replay folders a backtest needs.

Yahoo keeps about 60 days of 5-minute bars. `fetch` saves one chart per symbol
(<SYM>-60d5m.json, the same shape trader-timemachine reads). `build_day` cuts
those into what one replayed day needs, without any bar from a later day:

  <day>/scan/bundle.json         {SYM: that day's chart} for trader-scanner's orb scan
  <day>/charts/<SYM>.json        that day's bars, for trader-entry, trader-exit and the paper broker
  <day>/charts/<SYM>-60d5m.json  every bar up to the end of that day, for trader-timemachine
  <day>/news/                    empty: old headlines are not available, so a backtest skips the news gate
"""

from __future__ import annotations

import copy
import gzip
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from .bars import HOSTS, UA
from .clock import NY

FULL_SESSION = 78  # 9:30-16:00 in 5-minute bars


def load_chart(path: Path) -> dict:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def history_file(folder: Path, symbol: str) -> Path | None:
    for name in ("%s-60d5m.json" % symbol, "%s-60d5m.json.gz" % symbol):
        if (folder / name).is_file():
            return folder / name
    return None


def fetch(symbols: list[str], out: Path, pause: float = 0.5, echo=print) -> list[str]:
    """Download 60 days of 5-minute bars per symbol. Returns the symbols that failed."""
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for i, sym in enumerate(symbols, 1):
        sym = sym.upper()
        data, err = None, None
        for host in HOSTS:
            url = "%s/v8/finance/chart/%s?range=60d&interval=5m" % (host, sym.replace(".", "-"))
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                err = exc
                time.sleep(2)
        if data and (data.get("chart") or {}).get("result"):
            (out / ("%s-60d5m.json" % sym)).write_text(json.dumps(data), encoding="utf-8")
            echo("%3d/%d %s ok" % (i, len(symbols), sym))
        else:
            failed.append(sym)
            echo("%3d/%d %s nepavyko: %s" % (i, len(symbols), sym, err or "tuščias atsakymas"))
        time.sleep(pause)
    return failed


def _day_of(ts: int) -> date:
    return datetime.fromtimestamp(ts, NY).date()


def full_sessions(chart: dict) -> list[date]:
    stamps = chart["chart"]["result"][0].get("timestamp") or []
    counts = Counter(_day_of(t) for t in stamps)
    return sorted(d for d, n in counts.items() if n >= FULL_SESSION)


def slice_chart(chart: dict, keep) -> dict:
    """A copy of a v8 chart with only the bars whose New York date passes `keep(date)`."""
    out = copy.deepcopy(chart)
    res = out["chart"]["result"][0]
    stamps = res.get("timestamp") or []
    idx = [i for i, t in enumerate(stamps) if keep(_day_of(t))]
    first_kept = idx[0] if idx else len(stamps)
    res["timestamp"] = [stamps[i] for i in idx]
    quote = res["indicators"]["quote"][0]
    closes = quote.get("close") or []
    for key, values in list(quote.items()):
        if isinstance(values, list):
            quote[key] = [values[i] for i in idx]
    meta = res.setdefault("meta", {})
    prev = next((closes[i] for i in range(first_kept - 1, -1, -1) if closes[i] is not None), None)
    last = next(
        (quote["close"][i] for i in range(len(idx) - 1, -1, -1) if quote["close"][i] is not None), None
    )
    if prev is not None:
        meta["chartPreviousClose"] = meta["previousClose"] = prev
    if last is not None:
        meta["regularMarketPrice"] = last
    if res["timestamp"]:
        meta["regularMarketTime"] = res["timestamp"][-1]
    for k in ("tradingPeriods", "currentTradingPeriod"):
        meta.pop(k, None)
    return out


def build_day(history: dict[str, dict], day: date, out: Path) -> list[str]:
    """Write one day's replay folder. Returns the symbols that traded a full session that day."""
    (out / "charts").mkdir(parents=True, exist_ok=True)
    (out / "news").mkdir(parents=True, exist_ok=True)
    (out / "scan").mkdir(parents=True, exist_ok=True)
    bundle, have = {}, []
    for sym, chart in history.items():
        if day not in full_sessions(chart):
            continue
        today = slice_chart(chart, lambda d: d == day)
        upto = slice_chart(chart, lambda d: d <= day)
        bundle[sym] = today
        (out / "charts" / ("%s.json" % sym)).write_text(json.dumps(today), encoding="utf-8")
        (out / "charts" / ("%s-60d5m.json" % sym)).write_text(json.dumps(upto), encoding="utf-8")
        have.append(sym)
    (out / "scan" / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    return have
