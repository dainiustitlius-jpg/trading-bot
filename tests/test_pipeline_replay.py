"""End to end, offline: the README's TSLA trade on Monday 2026-10-19, through every skill.

The fixtures in tests/fixtures/replay-2026-10-19 come from the day-trader-skill
test suite (real Yahoo data): the scanner's 234-chart wall, TSLA's 5-minute
bars for the day and for the 60 days before, and that morning's headlines.
"""

import tempfile
import unittest
from pathlib import Path

from tradingbot.clock import Clock
from tradingbot.config import BotConfig, PlanSettings
from tradingbot.pipeline import Pipeline
from tradingbot.skills import ReplayData, SkillRunner
from tradingbot.workspace import Workspace

DATA = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19"


def pipeline(now: str, cleanup) -> tuple[Pipeline, Workspace]:
    cfg = BotConfig(plan=PlanSettings(target="1R"))  # the README trade uses a 1R target
    tmp = tempfile.TemporaryDirectory()
    cleanup(tmp.cleanup)
    ws = Workspace(tmp.name)
    ws.prepare(cfg.limits)
    runner = SkillRunner(ws.root, clock=Clock(now), replay=ReplayData(DATA))
    return Pipeline(cfg, runner, ws), ws


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipe, cls.ws = pipeline("2026-10-19 10:15", cls.addClassCleanup)
        cls.ev = cls.pipe.evaluate("TSLA")

    def step(self, name):
        return next(s for s in self.ev.steps if s.name == name)

    def test_every_gate_passes(self):
        self.assertTrue(self.ev.passed, [(s.name, s.summary) for s in self.ev.steps])
        self.assertEqual(
            [s.name for s in self.ev.steps], ["news", "entry", "exit", "timemachine", "budget", "risk"]
        )

    def test_entry_and_plan(self):
        self.assertEqual(self.step("entry").data["trigger"]["price"], 364.18)
        plan = self.ws.read("plan.json")
        self.assertEqual((plan["entry"], plan["stop"], plan["target"]), (364.18, 358.32, 370.04))

    def test_timemachine_is_a_coin_flip(self):
        tm = self.step("timemachine").data
        self.assertTrue(tm["verdict"].startswith("Close to a coin flip"))
        self.assertEqual(tm["mc"]["seed"], 19)
        self.assertEqual(tm["history"]["triggered"], 33)

    def test_order_is_eight_shares_bracket(self):
        o = self.ev.order
        self.assertEqual((o["symbol"], o["side"], o["qty"]), ("TSLA", "buy", "8"))
        self.assertEqual((o["stop_loss_stop_price"], o["take_profit_limit_price"]), ("358.32", "370.04"))
        self.assertEqual(self.step("budget").data["sizing"]["max_loss"], 46.88)

    def test_risk_recorded_a_pass(self):
        verdicts = self.ws.read("state/risk.json")
        self.assertIn("PASS", str(verdicts))

    def test_earnings_news_warns_but_does_not_block(self):
        news = self.step("news")
        self.assertTrue(news.ok)
        self.assertTrue(any("earnings headline" in w for w in news.warnings))


class Pass(unittest.TestCase):
    def test_scan_picks_tsla(self):
        # 11 fresh breakouts at 10:15; only TSLA has saved data, the rest stop because the bot
        # will not trade a stock whose headlines it could not read.
        pipe, _ = pipeline("2026-10-19 10:15", self.addCleanup)
        res = pipe.run_once()
        self.assertTrue(res.scan.ok)
        self.assertEqual(res.chosen.symbol, "TSLA")
        self.assertEqual(res.evaluations[-1].symbol, "TSLA")
        for ev in res.evaluations[:-1]:
            self.assertEqual(ev.stopped_at.name, "news")
            self.assertIn("could not read the headlines", ev.stopped_at.summary)

    def test_bar_still_forming_waits(self):
        pipe, _ = pipeline("2026-10-19 10:12", self.addCleanup)
        ev = pipe.evaluate("TSLA")
        self.assertFalse(ev.passed)
        self.assertEqual(ev.stopped_at.name, "entry")
        self.assertIn("waiting", ev.stopped_at.summary)

    def test_stale_trigger_is_skipped(self):
        pipe, _ = pipeline("2026-10-19 10:45", self.addCleanup)
        ev = pipe.evaluate("TSLA")
        self.assertEqual(ev.stopped_at.name, "entry")
        self.assertIn("too late", ev.stopped_at.summary)

    def test_weekend_is_closed(self):
        pipe, _ = pipeline("2026-10-18 10:15", self.addCleanup)
        res = pipe.run_once()
        self.assertFalse(res.market.ok)
        self.assertIsNone(res.scan)
        self.assertIn("closed", res.market.summary)

    def test_symbols_skip_the_scan(self):
        pipe, _ = pipeline("2026-10-19 10:15", self.addCleanup)
        res = pipe.run_once(["TSLA"])
        self.assertIsNone(res.scan)
        self.assertEqual(res.chosen.symbol, "TSLA")


if __name__ == "__main__":
    unittest.main()
