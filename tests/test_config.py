import tempfile
import unittest
from pathlib import Path

from tradingbot import config
from tradingbot.clock import Clock, parse_ny

ROOT = Path(__file__).resolve().parents[1]


class Config(unittest.TestCase):
    def test_example_loads(self):
        cfg = config.load(ROOT / "config" / "bot.example.toml")
        self.assertEqual(cfg.mode, "paper")
        self.assertEqual(cfg.plan.target, "2R")
        self.assertEqual(cfg.limits["max_loss_per_trade_usd"], 50)

    def test_defaults_without_file(self):
        self.assertEqual(config.load(None).workspace, "runs/paper")

    def write(self, text):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_unknown_setting_is_an_error(self):
        with self.assertRaisesRegex(ValueError, r"\[scan\]: unknown setting\(s\) max_candiates"):
            config.load(self.write("[scan]\nmax_candiates = 3\n"))

    def test_bad_mode(self):
        with self.assertRaisesRegex(ValueError, "mode must be paper or live"):
            config.load(self.write('mode = "yolo"\n'))


class ClockTest(unittest.TestCase):
    def test_pinned_formats(self):
        c = Clock("2026-10-19 10:15")
        self.assertEqual(c.ny_text(), "2026-10-19 10:15")
        self.assertEqual(c.iso_text(), "2026-10-19T10:15:00-04:00")

    def test_utc_input_converts(self):
        self.assertEqual(parse_ny("2026-10-19T14:15:00Z").strftime("%H:%M"), "10:15")


if __name__ == "__main__":
    unittest.main()
