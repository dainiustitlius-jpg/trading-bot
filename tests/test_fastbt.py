"""The fast backtest must take the same trades as the full one (the Time Machine filter aside)."""

import tempfile
import unittest
from pathlib import Path

from tradingbot import config, fastbt, sweep

HIST = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19" / "charts"


class SameTradesAsTheFullBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fastbt.load(HIST)

    def test_last_four_days(self):
        # The full backtest (every skill as its own program) on TSLA 2026-10-13..16 with a 1R target took:
        # 10-13 buy 9 at 375.71, stop at 372.91 (-$25.20); 10-15 buy 10 at 360.95, target 362.60 (+$16.50).
        cfg = config.BotConfig(plan=config.PlanSettings(target="1R"))
        res = fastbt.run(cfg, self.data, days=4)
        got = [(t["date"], t["qty"], t["entry"], t["exit"], t["reason"], t["pnl"]) for t in res["list"]]
        self.assertEqual(
            got,
            [
                ("2026-10-13", 9, 375.71, 372.91, "stop", -25.2),
                ("2026-10-15", 10, 360.95, 362.6, "target", 16.5),
            ],
        )
        self.assertEqual((res["trades"], res["pnl"], res["end_equity"]), (2, -8.7, 4991.3))
        self.assertNotIn("warning", res)

    def test_entry_window_and_daily_cap(self):
        cfg = config.BotConfig(limits={"no_entries_after": "09:45"})
        self.assertEqual(fastbt.run(cfg, self.data)["trades"], 0)

    def test_atr_stop_is_refused(self):
        with self.assertRaises(ValueError):
            fastbt.run(config.BotConfig(plan=config.PlanSettings(stop="atr:1.5")), self.data, days=1)


class FastSweep(unittest.TestCase):
    def test_default_grid_runs_and_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            variants = [sweep.parse_variant(t) for t in sweep.DEFAULT_VARIANTS]
            res = sweep.run_fast(None, HIST, Path(tmp) / "sw", variants, echo=lambda *_: None)
            self.assertEqual(len(res["variants"]), 24)
            self.assertEqual(res["variants"][0]["rank"], 1)
            self.assertTrue((Path(tmp) / "sw" / "bot" / "sweep.json").is_file())


if __name__ == "__main__":
    unittest.main()
