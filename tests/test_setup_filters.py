"""The setup filters: one rule set, the same answer in the bot and in the fast backtest."""

import tempfile
import unittest
from pathlib import Path

from tradingbot import config, fastbt
from tradingbot.clock import Clock
from tradingbot.config import BotConfig, FilterSettings, PlanSettings
from tradingbot.filters import average, check_context
from tradingbot.pipeline import Pipeline
from tradingbot.skills import ReplayData, SkillRunner
from tradingbot.workspace import Workspace

FIX = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19"


class CheckContext(unittest.TestCase):
    def test_off_by_default(self):
        self.assertTrue(check_context({}, FilterSettings()).ok)

    def test_gap(self):
        f = FilterSettings(max_gap_pct=1)
        self.assertTrue(check_context({"open": 100.5, "prev_close": 100}, f).ok)
        self.assertFalse(check_context({"open": 102, "prev_close": 100}, f).ok)
        self.assertFalse(check_context({"open": 102}, f).ok)  # no data is no evidence

    def test_ma20_and_market(self):
        f = FilterSettings(above_ma20=True, market_up=True)
        self.assertTrue(check_context({"price": 11, "ma20": 10, "market_pct": 0.3}, f).ok)
        c = check_context({"price": 9, "ma20": 10, "market_pct": -0.1}, f)
        self.assertFalse(c.ok)
        self.assertEqual(len(c.reasons), 2)

    def test_average(self):
        self.assertIsNone(average([1.0] * 19))
        self.assertEqual(average([0.0] * 5 + [2.0] * 20), 2.0)


def evaluate(filters: FilterSettings):
    cfg = BotConfig(plan=PlanSettings(target="1R"), filters=filters)
    tmp = tempfile.TemporaryDirectory()
    ws = Workspace(tmp.name)
    ws.prepare(cfg.limits)
    runner = SkillRunner(ws.root, clock=Clock("2026-10-19 10:15"), replay=ReplayData(FIX))
    ev = Pipeline(cfg, runner, ws).evaluate("TSLA")
    tmp.cleanup()
    return ev


class InTheBot(unittest.TestCase):
    def test_context_step_only_when_wanted(self):
        self.assertNotIn("context", [s.name for s in evaluate(FilterSettings()).steps])

    def test_gap_passes_on_the_reel(self):
        # TSLA opened 357.48 over yesterday's 354.72 (+0.78 %)
        ev = evaluate(FilterSettings(max_gap_pct=1))
        ctx = next(s for s in ev.steps if s.name == "context")
        self.assertTrue(ctx.ok, ctx.summary)
        self.assertTrue(ev.passed)

    def test_ma20_blocks_the_reel(self):
        # the 364.18 entry was under TSLA's 20-day average of daily closes (370.36)
        ev = evaluate(FilterSettings(above_ma20=True))
        self.assertEqual(ev.stopped_at.name, "context")
        self.assertIn("20-day average 370.36", ev.stopped_at.summary)

    def test_gap_too_big_stops(self):
        ev = evaluate(FilterSettings(max_gap_pct=0.5))
        self.assertEqual(ev.stopped_at.name, "context")
        self.assertIn("opened +0.78%", ev.stopped_at.summary)

    def test_no_market_data_stops(self):
        ev = evaluate(FilterSettings(market_up=True))  # the fixture has no SPY chart
        self.assertEqual(ev.stopped_at.name, "context")

    def test_thin_breakout_waits_for_volume(self):
        # trader-entry: with 1x the range's volume the 10:10 bar (0.60x) does not count; at 10:15 it waits
        ev = evaluate(FilterSettings(min_volume_x=1))
        self.assertEqual(ev.stopped_at.name, "entry")


class InTheFastBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fastbt.load(FIX / "charts")

    def run_with(self, **kw):
        cfg = config.BotConfig(plan=config.PlanSettings(target="1R"), filters=FilterSettings(**kw))
        return fastbt.run(cfg, self.data)

    def test_filters_only_remove_trades(self):
        base = {(t["date"], t["symbol"]) for t in self.run_with()["list"]}
        for kw in ({"min_volume_x": 1.5}, {"above_ma20": True}, {"max_gap_pct": 0.5}):
            got = {(t["date"], t["symbol"]) for t in self.run_with(**kw)["list"]}
            self.assertLessEqual(len(got), len(base), kw)

    def test_market_filter_without_spy_takes_nothing(self):
        self.assertEqual(self.run_with(market_up=True)["trades"], 0)


if __name__ == "__main__":
    unittest.main()
