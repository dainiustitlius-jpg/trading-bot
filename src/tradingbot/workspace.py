"""A workspace is a folder holding the trader/ files the skills read and write.

Paper and live runs use separate workspaces so a paper run never touches the
live broker log or portfolio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .skills import SKILLS_DIR

EXAMPLE_CONFIG = SKILLS_DIR / "trader-budget" / "config.example.json"
PLACEHOLDER_UA = "Your Name you@example.com"


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.trader = self.root / "trader"
        self.config_path = self.trader / "config.json"

    def file(self, name: str) -> Path:
        return self.trader / name

    def read(self, name: str) -> Any:
        path = self.file(name)
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def prepare(self, limits: dict[str, Any], paper: bool = True) -> dict[str, Any]:
        """Create trader/ and write trader/config.json: the skills' example, the bot's limits on top.

        An existing config.json is kept as the base, so values the user set by hand survive.
        """
        (self.trader / "state").mkdir(parents=True, exist_ok=True)
        if self.config_path.is_file():
            cfg = json.loads(self.config_path.read_text())
        else:
            cfg = json.loads(EXAMPLE_CONFIG.read_text())
        cfg.update(limits)
        if paper:
            cfg["paper"] = True
        self.config_path.write_text(json.dumps(cfg, indent=2) + "\n")
        return cfg

    def config(self) -> dict[str, Any]:
        return self.read("config.json") or {}
