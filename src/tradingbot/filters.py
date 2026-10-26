"""The bot's own go / no-go rules, read from the skills' JSON.

These only ever narrow what the skills allow. They never loosen a trader-risk
limit and never edit an order to make it pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import FilterSettings


@dataclass
class Check:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def hit_time(at: str, day: datetime) -> datetime | None:
    """Scanner's '10:15 AM' on the day of `day` (aware, New York)."""
    try:
        t = datetime.strptime(at.strip(), "%I:%M %p")
    except (ValueError, AttributeError):
        return None
    return day.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def fresh_hits(watchlist: dict, now: datetime, max_age_minutes: int) -> list[dict]:
    """Scanner hits that broke out within the last `max_age_minutes`, newest first.

    An older breakout is a different trade: the price has moved on from the entry.
    """
    out = []
    for hit in watchlist.get("hits", []):
        when = hit_time(hit.get("at", ""), now)
        if when is None or when > now:
            continue
        if now - when <= timedelta(minutes=max_age_minutes):
            out.append((when, hit))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in out]


def check_news(news: dict, symbol: str, settings: FilterSettings) -> Check:
    sym = symbol.upper()
    blocked = {t.lower() for t in settings.block_news_tags}
    check = Check(ok=True)
    for note in news.get("notes", []):
        if note.upper().startswith("HEADLINES FOR %s:" % sym):
            # Trading blind is not "no news": a halt or a lawsuit would go unseen.
            check.ok = False
            check.reasons.append("could not read the headlines (%s)" % note.split(":", 1)[1].strip())
    earnings = []
    for item in news.get("items", []):
        touches = [t.upper() for t in item.get("touches") or [item.get("ticker", "")]]
        if sym not in touches:
            continue
        tags = {t.lower() for t in item.get("tags", [])}
        title = item.get("title", "").strip()
        hit = sorted(tags & blocked)
        if hit:
            check.ok = False
            check.reasons.append("news tagged %s: %s" % (", ".join(hit), title))
        elif settings.block_important_filings and item.get("important"):
            check.ok = False
            check.reasons.append("important filing: %s" % title)
        elif "earnings" in tags:
            earnings.append(title)
    if earnings:
        check.warnings.append(
            "%d earnings headline(s), latest: %s. A day trade is flat by the close, so an "
            "overnight report cannot gap it." % (len(earnings), earnings[0])
        )
    for move in news.get("moves", []):
        if str(move.get("ticker", "")).upper() == sym and move.get("moving"):
            check.warnings.append("moving %+.2f%% vs yesterday's close" % move.get("pct", 0))
    return check


def check_timemachine(tm: dict, settings: FilterSettings) -> Check:
    verdict = tm.get("verdict") or ""
    check = Check(ok=True)
    low = verdict.lower()
    for phrase in settings.block_verdicts:
        if phrase.lower() in low:
            check.ok = False
            check.reasons.append("Time Machine: %s" % verdict)
            break
    triggered = (tm.get("history") or {}).get("triggered")
    if triggered is not None and triggered < settings.min_history_triggers:
        check.ok = False
        check.reasons.append(
            "the rule fired only %d times in the history (need %d)"
            % (triggered, settings.min_history_triggers)
        )
    check.warnings.extend(tm.get("verdict_notes") or [])
    return check
