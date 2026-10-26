"""The bot's own settings (config/bot.toml). Trading limits go to trader/config.json for the skills."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScanSettings:
    min_price: float = 5.0
    max_candidates: int = 10  # scanner hits evaluated per pass, most recent first
    max_trigger_age_minutes: int = 5  # a breakout older than this is a different trade


@dataclass
class PlanSettings:
    stop: str = "or-mid"  # trader-exit --stop
    target: str = "2R"  # trader-exit --target


@dataclass
class FilterSettings:
    news_hours: float = 12
    block_news_tags: list[str] = field(default_factory=lambda: ["halt", "legal", "regulatory", "risk"])
    block_important_filings: bool = True
    block_verdicts: list[str] = field(default_factory=lambda: ["toward the stop"])
    min_history_triggers: int = 10


@dataclass
class BotConfig:
    mode: str = "paper"  # paper | live
    workspace: str = "runs/paper"
    scan: ScanSettings = field(default_factory=ScanSettings)
    plan: PlanSettings = field(default_factory=PlanSettings)
    filters: FilterSettings = field(default_factory=FilterSettings)
    limits: dict[str, Any] = field(default_factory=dict)  # merged over trader-budget's config.example.json

    def __post_init__(self):
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be paper or live, not %r" % self.mode)


def _section(cls, raw: dict, name: str):
    data = raw.get(name, {}) or {}
    known = set(cls.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError("[%s]: unknown setting(s) %s" % (name, ", ".join(sorted(unknown))))
    return cls(**data)


def load(path: str | Path | None) -> BotConfig:
    if path is None:
        return BotConfig()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    top = {k: raw[k] for k in ("mode", "workspace") if k in raw}
    return BotConfig(
        **top,
        scan=_section(ScanSettings, raw, "scan"),
        plan=_section(PlanSettings, raw, "plan"),
        filters=_section(FilterSettings, raw, "filters"),
        limits=dict(raw.get("limits", {}) or {}),
    )
