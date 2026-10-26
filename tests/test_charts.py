import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tradingbot import chartdata, indicators
from tradingbot.server import VENDOR, Dashboard, serve

CHARTS = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19" / "charts"


class Indicators(unittest.TestCase):
    def test_sma_ema(self):
        self.assertEqual(indicators.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        e = indicators.ema([1, 2, 3, 4], 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2.0)
        self.assertAlmostEqual(e[3], 3.0)  # 4 * 0.5 + 2 * 0.5

    def test_bollinger_flat_series_has_no_width(self):
        up, mid, lo = indicators.bollinger([5.0] * 25)
        self.assertEqual((up[-1], mid[-1], lo[-1]), (5.0, 5.0, 5.0))
        self.assertIsNone(mid[18])

    def test_rsi_extremes(self):
        self.assertEqual(indicators.rsi(list(range(20)), 14)[-1], 100.0)
        self.assertAlmostEqual(indicators.rsi(list(range(20, 0, -1)), 14)[-1], 0.0)
        self.assertEqual(indicators.rsi([1, 2, 3], 14), [None, None, None])

    def test_vwap_restarts_each_session(self):
        v = indicators.vwap([2, 4, 10], [2, 4, 10], [2, 4, 10], [1, 3, 1], ["a", "a", "b"])
        self.assertEqual(v, [2.0, 3.5, 10.0])


class ChartData(unittest.TestCase):
    def test_reel_day(self):
        d = chartdata.build("TSLA", "5m", None, CHARTS)
        self.assertEqual(len(d["candles"]), 16)  # 9:30-10:45; the 10:49 live tick is dropped
        first = d["candles"][0]
        self.assertEqual(first["time"] % 86400, (9 * 60 + 30) * 60)  # 9:30 New York on the axis
        self.assertEqual([lv["title"] for lv in d["levels"]], ["OR high", "OR low"])
        self.assertAlmostEqual(d["levels"][0]["price"], 362.18, places=2)
        self.assertIn("vwap", d["lines"])

    def test_daily_from_saved_bars(self):
        d = chartdata.build("TSLA", "1d", None, CHARTS)
        self.assertEqual(len(d["candles"]), 60)
        self.assertNotIn("vwap", d["lines"])

    def test_bad_input(self):
        for sym, iv in (("TSLA;rm", "5m"), ("TSLA", "1m"), ("", "5m")):
            with self.assertRaises(ValueError):
                chartdata.build(sym, iv, None, CHARTS)


class Http(unittest.TestCase):
    def test_chart_and_vendor(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd = serve(Dashboard(Path(tmp) / "bot.toml", tmp, charts_dir=CHARTS), port=0, background=True)
            self.addCleanup(httpd.shutdown)
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            with urllib.request.urlopen(base + "/api/chart?symbol=tsla&interval=5m") as r:
                self.assertEqual(json.loads(r.read())["symbol"], "TSLA")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/chart?symbol=NOPE&interval=5m")
            self.assertEqual(cm.exception.code, 502)
            with urllib.request.urlopen(base + "/vendor/lightweight-charts.js") as r:
                self.assertIn(b"TradingView Lightweight Charts", r.read(400))
            self.assertTrue(all(p.is_file() for p in VENDOR.values()))


if __name__ == "__main__":
    unittest.main()
