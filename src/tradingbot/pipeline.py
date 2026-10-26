"""One pass of the day-trade chain, skill by skill.

scanner -> news -> entry -> exit -> timemachine -> budget -> risk

The skills keep one entry.json, plan.json and orders.json at a time, so
candidates are evaluated one after another and a pass stops at the first
order trader-risk passes. Nothing here places an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import filters
from .config import BotConfig
from .skills import SkillResult, SkillRunner
from .workspace import Workspace


@dataclass
class Step:
    name: str
    ok: bool
    summary: str
    warnings: list[str] = field(default_factory=list)
    data: Any = None


@dataclass
class Evaluation:
    symbol: str
    steps: list[Step] = field(default_factory=list)
    order: dict | None = None

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps) and self.order is not None

    @property
    def stopped_at(self) -> Step | None:
        return next((s for s in self.steps if not s.ok), None)


@dataclass
class PassResult:
    now: str
    market: Step
    scan: Step | None = None
    evaluations: list[Evaluation] = field(default_factory=list)

    @property
    def chosen(self) -> Evaluation | None:
        return next((e for e in self.evaluations if e.passed), None)


def _failed(name: str, res: SkillResult) -> Step:
    return Step(name, False, res.error(), data=res.data)


class Pipeline:
    def __init__(self, cfg: BotConfig, runner: SkillRunner, workspace: Workspace):
        self.cfg = cfg
        self.runner = runner
        self.ws = workspace

    # -- market and scan ---------------------------------------------------

    def market(self) -> Step:
        res = self.runner.run("alerts", "clock", offline=False)
        if not isinstance(res.data, dict):
            return _failed("market", res)
        d = res.data
        if not d.get("open"):
            return Step(
                "market",
                False,
                "NYSE closed (%s), next open %s" % (d.get("reason"), d.get("next_open")),
                data=d,
            )
        warn = ["early close at %s" % d["closes"]] if d.get("early_close") else []
        return Step("market", True, "NYSE open, %s" % d.get("reason"), warn, d)

    def scan(self) -> tuple[Step, list[str]]:
        res = self.runner.run("scanner", "orb", "--min-price", str(self.cfg.scan.min_price))
        if not isinstance(res.data, dict):
            # exit 1 with one plain line: before 9:50, after the entry cutoff, or nothing found
            return Step("scan", False, res.error()), []
        hits = filters.fresh_hits(res.data, self.runner.clock.now(), self.cfg.scan.max_trigger_age_minutes)
        symbols = [h["symbol"] for h in hits][: self.cfg.scan.max_candidates]
        summary = "%d breakout(s) in %s usable charts, %d fresh (last %d min)" % (
            len(res.data.get("hits", [])),
            res.data.get("scanned"),
            len(hits),
            self.cfg.scan.max_trigger_age_minutes,
        )
        return Step("scan", True, summary, data=res.data), symbols

    # -- one candidate -----------------------------------------------------

    def evaluate(self, symbol: str) -> Evaluation:
        ev = Evaluation(symbol.upper())
        for step in (self._news, self._entry, self._exit, self._timemachine, self._budget, self._risk):
            result = step(ev)
            ev.steps.append(result)
            if not result.ok:
                break
        return ev

    def _news(self, ev: Evaluation) -> Step:
        f = self.cfg.filters
        res = self.runner.run(
            "news", "--tickers", ev.symbol, "--hours", str(f.news_hours), "--all", "--dry-run"
        )
        if not isinstance(res.data, dict):
            return _failed("news", res)
        check = filters.check_news(res.data, ev.symbol, f)
        n = sum(
            1 for i in res.data.get("items", []) if ev.symbol in [t.upper() for t in i.get("touches", [])]
        )
        summary = (
            "; ".join(check.reasons)
            if check.reasons
            else "%d item(s) in %gh, none blocking" % (n, f.news_hours)
        )
        return Step("news", check.ok, summary, check.warnings + res.data.get("notes", []), res.data)

    def _entry(self, ev: Evaluation) -> Step:
        res = self.runner.run("entry", "check", ev.symbol, "--write")
        d = res.data
        if not isinstance(d, dict):
            return _failed("entry", res)
        if d.get("status") != "triggered":
            return Step("entry", False, "%s: %s" % (d.get("status"), d.get("reason")), data=d)
        age = d.get("minutes_since") or 0
        trig = d.get("trigger") or {}
        if age > self.cfg.scan.max_trigger_age_minutes:
            return Step(
                "entry", False, "triggered %s, %d min ago: too late" % (trig.get("time"), age), data=d
            )
        return Step(
            "entry",
            True,
            "triggered %s at %s (range high %s)" % (trig.get("time"), trig.get("price"), d.get("level")),
            data=d,
        )

    def _exit(self, ev: Evaluation) -> Step:
        p = self.cfg.plan
        res = self.runner.run("exit", "plan", "--stop", p.stop, "--target", p.target, "--write")
        d = res.data
        if not res.ok or not isinstance(d, dict):
            return _failed("exit", res)
        return Step(
            "exit",
            True,
            "entry %s, stop %s (%s), target %s (%s), risk %s a share"
            % (
                d["entry"],
                d["stop"],
                d.get("stop_label"),
                d["target"],
                d.get("target_label"),
                d["risk_per_share"],
            ),
            d.get("warnings") or [],
            d,
        )

    def _timemachine(self, ev: Evaluation) -> Step:
        plan = self.ws.read("plan.json") or {}
        args = ["run", "--write"]
        if plan.get("flat_by"):
            args += ["--until", plan["flat_by"]]
        res = self.runner.run("timemachine", *args)
        d = res.data
        if not res.ok or not isinstance(d, dict):
            return _failed("timemachine", res)
        check = filters.check_timemachine(d, self.cfg.filters)
        mc, hist = d.get("mc", {}), d.get("history", {})
        summary = (
            "%s | target first %.1f%%, stop first %.1f%%, neither %.1f%% (seed %s); history %s/%s fired"
            % (
                d.get("verdict"),
                100 * mc.get("p_target", 0),
                100 * mc.get("p_stop", 0),
                100 * mc.get("p_neither", 0),
                mc.get("seed"),
                hist.get("triggered"),
                hist.get("sessions"),
            )
        )
        if not check.ok:
            summary = "; ".join(check.reasons)
        return Step("timemachine", check.ok, summary, check.warnings, d)

    def _budget(self, ev: Evaluation) -> Step:
        res = self.runner.run("budget", "--write")
        d = res.data
        if not isinstance(d, dict):
            return _failed("budget", res)
        sizing = d.get("sizing") or {}
        if sizing.get("refusal") or not d.get("orders"):
            return Step("budget", False, sizing.get("refusal") or res.error(), data=d)
        return Step(
            "budget",
            True,
            "%s shares, $%.2f, max loss $%.2f at the stop (bound by %s)"
            % (sizing["qty"], sizing["cost"], sizing["max_loss"], sizing.get("bound_by")),
            data=d,
        )

    def _risk(self, ev: Evaluation) -> Step:
        res = self.runner.run("risk")
        d = res.data
        if not isinstance(d, dict) or not d.get("results"):
            return _failed("risk", res)
        r = d["results"][0]
        if r.get("verdict") != "PASS":
            return Step(
                "risk",
                False,
                "BLOCK: " + "; ".join(r.get("reasons") or ["blocked"]),
                r.get("warnings", []),
                d,
            )
        orders = (self.ws.read("orders.json") or {}).get("orders") or []
        ev.order = orders[0] if orders else None
        return Step(
            "risk",
            True,
            "PASS: max loss $%.2f, reward:risk %.2f" % (r["max_loss"], r["reward_risk"]),
            r.get("warnings", []) + d.get("warnings", []),
            d,
        )

    # -- a whole pass ------------------------------------------------------

    def run_once(self, symbols: list[str] | None = None) -> PassResult:
        """One pass: is the market open, what broke out, and the first candidate that passes every gate.

        With `symbols` the scan is skipped and those are evaluated in order.
        """
        result = PassResult(now=self.runner.clock.ny_text(), market=self.market())
        if not result.market.ok:
            return result
        if symbols is None:
            result.scan, symbols = self.scan()
            if not result.scan.ok:
                return result
        for sym in symbols:
            ev = self.evaluate(sym)
            result.evaluations.append(ev)
            if ev.passed:
                break
        return result
