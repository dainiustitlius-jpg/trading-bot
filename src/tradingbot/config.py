"""The bot's own settings (config/bot.toml). Trading limits go to trader/config.json for the skills."""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass, field
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
    # Setup filters (0 / false = off). The same rules run in the bot and in the backtests.
    min_volume_x: float = 0.0  # the breakout bar's volume vs the opening range's average (trader-entry)
    market_up: bool = False  # only when market_symbol is above yesterday's close at the entry
    market_symbol: str = "SPY"
    above_ma20: bool = False  # only when the price is above its 20-day average of daily closes
    max_gap_pct: float = 0.0  # skip a stock that opened more than this % above yesterday's close


@dataclass
class PaperSettings:
    slippage_bps: float = 5.0  # added to the entry, taken off stop and market exits


@dataclass
class NotifySettings:
    events: list[str] = field(default_factory=lambda: ["entry", "exit", "ticket", "alert", "error", "report"])
    telegram_chat_id: str = ""  # the token comes from TELEGRAM_BOT_TOKEN
    ntfy_topic: str = ""  # anyone who knows the topic can read it: pick a long random one
    ntfy_server: str = "https://ntfy.sh"
    email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""  # the password comes from SMTP_PASSWORD


@dataclass
class BotConfig:
    mode: str = "paper"  # paper | live
    workspace: str = ""  # empty: runs/<mode>, so paper and live never share trader/ files
    scan: ScanSettings = field(default_factory=ScanSettings)
    plan: PlanSettings = field(default_factory=PlanSettings)
    filters: FilterSettings = field(default_factory=FilterSettings)
    paper: PaperSettings = field(default_factory=PaperSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)
    limits: dict[str, Any] = field(default_factory=dict)  # merged over trader-budget's config.example.json

    def __post_init__(self):
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be paper or live, not %r" % self.mode)

    def workspace_dir(self) -> str:
        return self.workspace or "runs/%s" % self.mode


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
        paper=_section(PaperSettings, raw, "paper"),
        notify=_section(NotifySettings, raw, "notify"),
        limits=dict(raw.get("limits", {}) or {}),
    )


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[%s]" % ", ".join(_toml_value(x) for x in v)
    if v is None:
        raise ValueError("TOML has no null")
    return json.dumps(str(v), ensure_ascii=False)  # a JSON string is a valid TOML basic string


def dump(cfg: BotConfig) -> str:
    """BotConfig -> TOML text that load() reads back to the same config."""
    lines = ["# Written by tradingbot (dashboard settings).", ""]
    lines += ["mode = %s" % _toml_value(cfg.mode)]
    if cfg.workspace:
        lines += ["workspace = %s" % _toml_value(cfg.workspace)]
    sections = {
        "scan": cfg.scan,
        "plan": cfg.plan,
        "filters": cfg.filters,
        "paper": cfg.paper,
        "notify": cfg.notify,
    }
    for name, obj in sections.items():
        lines += ["", "[%s]" % name]
        lines += ["%s = %s" % (k, _toml_value(v)) for k, v in asdict(obj).items()]
    lines += ["", "[limits]"]
    lines += ["%s = %s" % (k, _toml_value(v)) for k, v in cfg.limits.items() if v is not None]
    return "\n".join(lines) + "\n"
