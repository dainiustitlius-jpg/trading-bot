"""The bot's own files, next to trader/ in the workspace.

bot/state.json     today's state: the open position, the last pass, the market line
bot/control.json   requests from the dashboard: pause new entries, flatten now
bot/journal.jsonl  one event per line, append only
bot/trades.jsonl   one closed trade per line
bot/reports/       one daily report per trading day

Writes are atomic (temp file + replace) because the dashboard reads them while
the bot loop writes.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
            fh.write("\n")
        for attempt in range(10):  # Windows refuses to replace a file another thread is reading
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path, last: int | None = None) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    if last is not None:
        lines = lines[-last:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class BotStore:
    def __init__(self, root: str | Path):
        self.dir = Path(root) / "bot"
        self.state_path = self.dir / "state.json"
        self.control_path = self.dir / "control.json"
        self.journal_path = self.dir / "journal.jsonl"
        self.trades_path = self.dir / "trades.jsonl"
        self.reports_dir = self.dir / "reports"

    # state
    def state(self) -> dict:
        return read_json(self.state_path, {}) or {}

    def save_state(self, state: dict) -> None:
        write_json(self.state_path, state)

    # control (written by the dashboard, read by the loop)
    def control(self) -> dict:
        return read_json(self.control_path, {}) or {}

    def set_control(self, **changes: Any) -> dict:
        ctl = self.control()
        ctl.update(changes)
        write_json(self.control_path, ctl)
        return ctl

    # append-only logs
    def _append(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def log(self, at: str, kind: str, title: str, detail: str = "", **extra: Any) -> dict:
        record = {"at": at, "kind": kind, "title": title, "detail": detail, **extra}
        self._append(self.journal_path, record)
        return record

    def journal(self, last: int = 200) -> list[dict]:
        return read_jsonl(self.journal_path, last)

    def add_trade(self, trade: dict) -> None:
        self._append(self.trades_path, trade)

    def trades(self) -> list[dict]:
        return read_jsonl(self.trades_path)

    def save_report(self, date: str, report: dict) -> Path:
        path = self.reports_dir / ("%s.json" % date)
        write_json(path, report)
        return path
