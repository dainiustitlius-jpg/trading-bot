import unittest

from tradingbot.clock import parse_ny
from tradingbot.config import FilterSettings
from tradingbot.filters import check_news, check_timemachine, fresh_hits

NOW = parse_ny("2026-10-19 10:15")


class FreshHits(unittest.TestCase):
    def test_keeps_recent_newest_first(self):
        wl = {
            "hits": [
                {"symbol": "OLD", "at": "9:50 AM"},
                {"symbol": "A", "at": "10:10 AM"},
                {"symbol": "B", "at": "10:15 AM"},
                {"symbol": "LATER", "at": "10:20 AM"},
                {"symbol": "BAD", "at": "soon"},
            ]
        }
        self.assertEqual([h["symbol"] for h in fresh_hits(wl, NOW, 5)], ["B", "A"])
        self.assertEqual([h["symbol"] for h in fresh_hits(wl, NOW, 0)], ["B"])


def item(tags=(), important=False, ticker="TSLA", title="headline"):
    return {"ticker": ticker, "touches": [ticker], "tags": list(tags), "important": important, "title": title}


class News(unittest.TestCase):
    def setUp(self):
        self.f = FilterSettings()

    def test_clean(self):
        c = check_news({"items": [item(["macro"])], "moves": []}, "TSLA", self.f)
        self.assertTrue(c.ok)
        self.assertEqual(c.warnings, [])

    def test_blocked_tag(self):
        c = check_news({"items": [item(["legal"], title="sued")]}, "tsla", self.f)
        self.assertFalse(c.ok)
        self.assertIn("legal", c.reasons[0])

    def test_important_filing(self):
        self.assertFalse(check_news({"items": [item(important=True)]}, "TSLA", self.f).ok)
        self.f.block_important_filings = False
        self.assertTrue(check_news({"items": [item(important=True)]}, "TSLA", self.f).ok)

    def test_other_ticker_ignored(self):
        self.assertTrue(check_news({"items": [item(["halt"], ticker="AAPL")]}, "TSLA", self.f).ok)

    def test_earnings_is_one_warning(self):
        c = check_news(
            {"items": [item(["earnings"], title="new"), item(["earnings"], title="old")]}, "TSLA", self.f
        )
        self.assertTrue(c.ok)
        self.assertEqual(len(c.warnings), 1)
        self.assertIn("2 earnings headline(s), latest: new", c.warnings[0])

    def test_unreadable_headlines_block(self):
        c = check_news({"items": [], "notes": ["headlines for TSLA: HTTP 503"]}, "TSLA", self.f)
        self.assertFalse(c.ok)
        self.assertIn("HTTP 503", c.reasons[0])

    def test_moving_warns(self):
        c = check_news(
            {"items": [], "moves": [{"ticker": "TSLA", "pct": 3.31, "moving": True}]}, "TSLA", self.f
        )
        self.assertEqual(c.warnings, ["moving +3.31% vs yesterday's close"])


class TimeMachine(unittest.TestCase):
    def test_coin_flip_passes(self):
        tm = {
            "verdict": "Close to a coin flip: target first 29.2 % vs stop first 24.2 %.",
            "history": {"triggered": 33},
            "verdict_notes": ["46.6 % hit neither"],
        }
        c = check_timemachine(tm, FilterSettings())
        self.assertTrue(c.ok)
        self.assertEqual(c.warnings, ["46.6 % hit neither"])

    def test_leans_to_stop_blocks(self):
        tm = {"verdict": "Leans toward the stop: ...", "history": {"triggered": 33}}
        self.assertFalse(check_timemachine(tm, FilterSettings()).ok)

    def test_thin_history_blocks(self):
        tm = {"verdict": "Leans toward the target", "history": {"triggered": 4}}
        c = check_timemachine(tm, FilterSettings())
        self.assertFalse(c.ok)
        self.assertIn("only 4 times", c.reasons[0])


if __name__ == "__main__":
    unittest.main()
