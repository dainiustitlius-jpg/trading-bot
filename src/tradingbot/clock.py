"""New York time for the bot, real or pinned for a replay.

The skills work out NYSE hours and holidays themselves; the bot only needs
"now" in New York time and a way to hand it to each skill's --now flag.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


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
        return self.pinned if self.pinned is not None else datetime.now(NY)

    def ny_text(self) -> str:
        """'2026-10-19 10:15': what most skills take as --now."""
        return self.now().strftime("%Y-%m-%d %H:%M")

    def iso_text(self) -> str:
        """ISO with offset: trader-news reads a bare time as UTC, so it gets this."""
        return self.now().isoformat(timespec="seconds")
