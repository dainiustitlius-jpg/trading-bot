"""Chart indicators, standard library only (no pandas or TA-Lib to install on Windows).

Each function takes plain lists, oldest first, and returns a list of the same
length with None where the indicator is not defined yet.
"""

from __future__ import annotations

import math


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    """Seeded with the simple average of the first n values."""
    out: list[float | None] = []
    k = 2.0 / (n + 1)
    prev = None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
            continue
        prev = sum(values[:n]) / n if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def bollinger(values: list[float], n: int = 20, width: float = 2.0):
    """(upper, middle, lower), population standard deviation as Bollinger defined it."""
    mid = sma(values, n)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - n + 1 : i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        upper.append(m + width * sd)
        lower.append(m - width * sd)
    return upper, mid, lower


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI, as trader-scanner computes it."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n

    def value(g: float, lo: float) -> float:
        return 100.0 if lo == 0 else 100.0 - 100.0 / (1 + g / lo)

    out[n] = value(avg_g, avg_l)
    for i in range(n + 1, len(values)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = value(avg_g, avg_l)
    return out


def vwap(highs, lows, closes, volumes, sessions) -> list[float | None]:
    """Volume-weighted average of the typical price, restarting at each new session (day)."""
    out: list[float | None] = []
    pv = vol = 0.0
    last = None
    for h, lo, c, v, s in zip(highs, lows, closes, volumes, sessions, strict=True):
        if s != last:
            pv = vol = 0.0
            last = s
        pv += (h + lo + c) / 3 * v
        vol += v
        out.append(pv / vol if vol else None)
    return out
