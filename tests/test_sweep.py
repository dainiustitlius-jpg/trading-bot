import tempfile
import unittest
from pathlib import Path

from tradingbot import config, sweep

HIST = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19" / "charts"


class Variants(unittest.TestCase):
    def test_parse_and_name(self):
        v = sweep.parse_variant("stop=or-low, target=1.5R, flat_by=12:00")
        self.assertEqual(v, {"stop": "or-low", "target": "1.5R", "flat_by": "12:00"})
        self.assertEqual(sweep.name_of(v), "stop-or-low_target-1p5R_flat_by-1200")

    def test_bad_variants(self):
        for text in ("", "target", "colour=red", "stop="):
            with self.assertRaises(ValueError, msg=text):
                sweep.parse_variant(text)

    def test_early_flat_by_moves_the_entry_cutoff(self):
        base = config.BotConfig(limits={"no_entries_after": "15:30", "budget": 5000})
        cfg = sweep.variant_config(base, {"target": "1R", "flat_by": "12:00"})
        self.assertEqual(cfg.plan.target, "1R")
        self.assertEqual(cfg.limits, {"no_entries_after": "11:30", "budget": 5000, "flat_by": "12:00"})
        self.assertEqual(base.limits["no_entries_after"], "15:30")  # the base is not changed

    def test_duplicates_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = sweep.parse_variant("target=1R")
            with self.assertRaises(ValueError):
                sweep.run(None, HIST, Path(tmp) / "sw", [v, dict(v)], echo=lambda *_: None)


class Run(unittest.TestCase):
    def test_two_variants_ranked(self):
        with tempfile.TemporaryDirectory() as tmp:
            variants = [sweep.parse_variant("target=1R"), sweep.parse_variant("target=2R")]
            res = sweep.run(None, HIST, Path(tmp) / "sw", variants, days=1, workers=2, echo=lambda *_: None)
            self.assertEqual(res["status"], "done")
            self.assertEqual({r["status"] for r in res["variants"]}, {"done"})
            # only variants that traded get a rank (no R without a trade); the rest stay listed after them
            traded = [r for r in res["variants"] if r["trades"]]
            self.assertEqual([r.get("rank") for r in traded], list(range(1, len(traded) + 1)))
            self.assertEqual(len(res["variants"]), 2)
            self.assertTrue((Path(tmp) / "sw" / "bot" / "sweep.json").is_file())


if __name__ == "__main__":
    unittest.main()
