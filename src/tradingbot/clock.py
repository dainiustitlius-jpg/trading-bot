"""New York time for the bot, real or pinned for a replay.

The skills work out NYSE hours and holidays themselves; the bot only needs
"now" in New York time and a way to hand it to each skill's --now flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class _USEastern(tzinfo):
    """New York time from the US rule (since 2007), for systems without a time zone database.

    Windows has no IANA database unless the tzdata package is installed. Daylight time runs
    from the second Sunday in March, 2:00, to the first Sunday in November, 2:00 (local).
    """

    @staticmethod
    def _sunday(year: int, month: int, nth: int) -> datetime:
        first = datetime(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1), hours=2)

    def _is_dst(self, local: datetime) -> bool:
        naive = local.replace(tzinfo=None, fold=0)
        end = self._sunday(naive.year, 11, 1)
        if end - timedelta(hours=1) <= naive < end and local.fold:
            return False  # 1:00-2:00 happens twice; fold=1 is the second time, in standard time
        return self._sunday(naive.year, 3, 2) <= naive < end

    def utcoffset(self, dt):
        return timedelta(hours=-4 if self._is_dst(dt) else -5)

    def dst(self, dt):
        return timedelta(hours=1 if self._is_dst(dt) else 0)

    def tzname(self, dt):
        return "EDT" if self._is_dst(dt) else "EST"

    def fromutc(self, dt):
        standard = dt.replace(tzinfo=None) - timedelta(hours=5)
        # DST starts at 2:00 EST (7:00 UTC) and ends at 2:00 EDT, i.e. 1:00 EST (6:00 UTC).
        start, end = (
            self._sunday(standard.year, 3, 2),
            self._sunday(standard.year, 11, 1) - timedelta(hours=1),
        )
        if start <= standard < end:
            return (standard + timedelta(hours=1)).replace(tzinfo=self)
        repeated = end <= standard < end + timedelta(hours=1)
        return standard.replace(tzinfo=self, fold=1 if repeated else 0)


try:
    NY: tzinfo = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    NY = _USEastern()


def parse_ny(text: str) -> datetime:
    """'2026-10-19 10:15' (New York) or an ISO time with an offset -> aware datetime in New York."""
    text = text.strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


class Clock:
    """The bot's clock. Pinned (replay) when `pinned` is given, otherwise the real time."""

    def __init__(self, pinned: str | datetime | None = None):
        if isinstance(pinned, str):
            pinned = parse_ny(pinned)
        self.pinned = pinned

    @property
    def is_pinned(self) -> bool:
        return self.pinned is not None

    def now(self) -> datetime:
        return self.pinned if self.pinned is not None else datetime.now(UTC).astimezone(NY)

    def ny_text(self) -> str:
        """'2026-10-19 10:15': what most skills take as --now."""
        return self.now().strftime("%Y-%m-%d %H:%M")

    def iso_text(self) -> str:
        """ISO with offset: trader-news reads a bare time as UTC, so it gets this."""
        return self.now().isoformat(timespec="seconds")
