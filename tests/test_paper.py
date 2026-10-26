import unittest

from tradingbot.bars import Bar, parse_chart
from tradingbot.clock import parse_ny
from tradingbot.paper import scan_bars

POS = {
    "symbol": "TSLA",
    "qty": 8,
    "entry": 364.36,
    "entry_plan": 364.18,
    "entry_at": "2026-10-19T10:15:00-04:00",
    "stop": 358.32,
    "target": 370.04,
    "flat_by": "15:55",
    "last_price": 364.36,
    "checked_until": None,
}


def bar(hhmm, o, h, lo, c):
    return Bar(parse_ny("2026-10-19 " + hhmm), o, h, lo, c)


class ScanBars(unittest.TestCase):
    def test_holds_between_stop_and_target(self):
        ex, up = scan_bars(POS, [bar("10:15", 364.2, 365.2, 363.0, 365.05)], parse_ny("2026-10-19 10:20"), 5)
        self.assertIsNone(ex)
        self.assertEqual(up["last_price"], 365.05)
        self.assertEqual(up["checked_until"], "2026-10-19T10:20:00-04:00")

    def test_bars_before_entry_are_ignored(self):
        ex, up = scan_bars(POS, [bar("10:10", 361.0, 364.4, 350.0, 364.18)], parse_ny("2026-10-19 10:20"), 5)
        self.assertIsNone(ex)
        self.assertEqual(up, {})

    def test_target_fills_at_the_target(self):
        ex, _ = scan_bars(POS, [bar("10:15", 365.0, 371.0, 364.0, 370.5)], parse_ny("2026-10-19 10:20"), 5)
        self.assertEqual((ex.reason, ex.price, ex.bar), ("target", 370.04, "10:15"))

    def test_stop_first_when_a_bar_touches_both(self):
        ex, _ = scan_bars(POS, [bar("10:15", 364.0, 371.0, 358.0, 365.0)], parse_ny("2026-10-19 10:20"), 0)
        self.assertEqual((ex.reason, ex.price), ("stop", 358.32))

    def test_gap_through_the_stop_fills_at_the_open(self):
        ex, _ = scan_bars(POS, [bar("10:15", 355.0, 356.0, 354.0, 355.5)], parse_ny("2026-10-19 10:20"), 0)
        self.assertEqual(ex.price, 355.0)

    def test_stop_slippage(self):
        ex, _ = scan_bars(POS, [bar("10:15", 364.0, 364.5, 358.0, 359.0)], parse_ny("2026-10-19 10:20"), 10)
        self.assertEqual(ex.price, round(358.32 * 0.999, 2))

    def test_flat_by_sells_at_last_close(self):
        pos = dict(POS, checked_until="2026-10-19T15:55:00-04:00", last_price=366.68)
        ex, _ = scan_bars(pos, [], parse_ny("2026-10-19 15:55"), 5)
        self.assertEqual((ex.reason, ex.price), ("flat_by", round(366.68 * 0.9995, 2)))

    def test_manual_flatten(self):
        ex, _ = scan_bars(POS, [], parse_ny("2026-10-19 11:00"), 0, force="manual")
        self.assertEqual((ex.reason, ex.price), ("manual", 364.36))

    def test_checked_bars_are_not_rechecked(self):
        pos = dict(POS, checked_until="2026-10-19T10:20:00-04:00")
        ex, up = scan_bars(pos, [bar("10:15", 364.0, 380.0, 364.0, 379.0)], parse_ny("2026-10-19 10:25"), 0)
        self.assertIsNone(ex)


class ParseChart(unittest.TestCase):
    def test_drops_live_tick_and_nulls(self):
        base = 1792416600  # 2026-10-19 09:30 New York
        data = {
            "chart": {
                "result": [
                    {
                        "timestamp": [base, base + 300, base + 600, base + 645],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [1, None, 3, 4],
                                    "high": [1, 2, 3, 4],
                                    "low": [1, 2, 3, 4],
                                    "close": [1, 2, 3, 4],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        bars = parse_chart(data)
        self.assertEqual([b.start.strftime("%H:%M") for b in bars], ["09:30", "09:40"])


if __name__ == "__main__":
    unittest.main()
