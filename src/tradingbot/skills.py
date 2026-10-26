"""Run the trader-* skill scripts as subprocesses and read their --json output.

Each skill is a stand-alone stdlib Python script with a stable CLI: --json for
machine output, --now to pin the New York clock, --offline to read saved data
instead of the network, and --write to leave a file in trader/. The bot never
imports their internals, so the skills can be updated from upstream as-is.
Every script runs with cwd = the workspace, so their relative trader/ paths
land in that workspace.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .clock import Clock

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


@dataclass(frozen=True)
class Skill:
    script: str  # relative to the skills folder
    now: str | None = None  # "ny" ("2026-10-19 10:15") or "iso" (with offset); None: no --now flag
    offline: str | None = None  # which ReplayData folder its --offline flag reads


SKILLS: dict[str, Skill] = {
    "scanner": Skill("trader-scanner/scan.py", now="ny", offline="scan"),
    "news": Skill("trader-news/news.py", now="iso", offline="news"),
    "entry": Skill("trader-entry/entry.py", now="ny", offline="charts"),
    "exit": Skill("trader-exit/exit.py", now="ny", offline="charts"),
    "timemachine": Skill("trader-timemachine/timemachine.py", now="ny", offline="charts"),
    "budget": Skill("trader-budget/size.py", now="ny"),
    "risk": Skill("trader-risk/check.py", now="ny"),
    "alerts": Skill("trader-alerts/alerts.py", now="ny"),
    "broker": Skill("trader-broker/ticket.py", now="ny"),
    "analyst": Skill("trader-analyst/facts.py"),
    "pelosi": Skill("trader-pelosi/ptr.py"),
}


@dataclass(frozen=True)
class ReplayData:
    """Saved data for a replay, one folder per kind of --offline input.

    scan/    a Yahoo chart bundle for trader-scanner (e.g. wall5m-2026-10-19.json.gz)
    charts/  <SYM>.json (the day's 5-minute bars) and <SYM>-60d5m.json.gz (60 days, for the Time Machine)
    news/    yahoo-<SYM>.xml, chart-<SYM>.json, company_tickers.json for trader-news
    """

    root: Path

    def path(self, kind: str) -> Path:
        return self.root / kind


@dataclass
class SkillResult:
    skill: str
    args: list[str]
    code: int
    stdout: str
    stderr: str
    data: dict | list | None = field(default=None)

    @property
    def ok(self) -> bool:
        return self.code == 0

    def error(self) -> str:
        """The skill's own one-line reason, from stderr or stdout."""
        text = (self.stderr or self.stdout or "").strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return lines[-1] if lines else "exit code %d" % self.code


class SkillError(RuntimeError):
    pass


class SkillRunner:
    def __init__(
        self,
        workspace: Path,
        clock: Clock | None = None,
        replay: ReplayData | None = None,
        skills_dir: Path = SKILLS_DIR,
        python: str = sys.executable,
        timeout: float = 900,
    ):
        self.workspace = Path(workspace)
        self.clock = clock or Clock()
        self.replay = replay
        self.skills_dir = Path(skills_dir)
        self.python = python
        self.timeout = timeout

    def script(self, name: str) -> Path:
        return self.skills_dir / SKILLS[name].script

    def missing(self) -> list[str]:
        return [name for name in SKILLS if not self.script(name).is_file()]

    def run(
        self, name: str, *args: str, json_out: bool = True, now: bool = True, offline: bool = True
    ) -> SkillResult:
        skill = SKILLS[name]
        argv = [str(a) for a in args]
        if json_out:
            argv.append("--json")
        if now and skill.now and self.clock.is_pinned:
            argv += ["--now", self.clock.iso_text() if skill.now == "iso" else self.clock.ny_text()]
        if offline and skill.offline and self.replay is not None:
            argv += ["--offline", str(self.replay.path(skill.offline))]
        # -X utf8: the skills print and write UTF-8 on Windows too, where the default is cp1252.
        cmd = [self.python, "-X", "utf8", "-I", str(self.script(name))] + argv
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SkillError("%s timed out after %ss" % (name, self.timeout)) from exc
        data = None
        if json_out and proc.stdout.strip():
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = None
        return SkillResult(name, argv, proc.returncode, proc.stdout, proc.stderr, data)
