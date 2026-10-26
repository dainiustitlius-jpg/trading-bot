"""The whole loop on the saved TSLA day: tick every 5 minutes, paper fill, flat by 15:55, report."""

import json
import tempfile
import unittest
from pathlib import Path

from tradingbot.bot import Bot, replay_day
from tradingbot.server import Dashboard
from tradingbot.skills import ReplayData

DATA = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19"


def make_bot(tmp: str, end: str = "16:05", start: str = "10:10") -> Bot:
    cfg = Path(tmp) / "bot.toml"
    cfg.write_text('[plan]\ntarget = "1R"\n')
    bot = Bot(cfg, workspace=str(Path(tmp) / "ws"), replay=ReplayData(DATA))
    replay_day(bot, "2026-10-19", start, end, echo=lambda _: None)
    return bot


class FullDay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.bot = make_bot(cls.tmp.name)

    def test_one_trade_flat_by_the_close(self):
        trades = self.bot.store.trades()
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t["symbol"], t["qty"], t["entry"], t["reason"]), ("TSLA", 8, 364.36, "flat_by"))
        # The saved bars end at 10:45, so the 15:55 exit uses the 10:45 close (366.68) less 5 bps.
        self.assertEqual(t["exit"], 366.5)
        self.assertEqual(t["pnl"], 17.12)

    def test_portfolio_and_broker_log(self):
        pf = self.bot.ws.read("portfolio.json")
        self.assertEqual(pf["cash"], 5017.12)
        self.assertEqual(pf["positions"], [])
        orders = self.bot.ws.read("state/broker.json")["orders"]
        placed = [o for o in orders if o["status"] == "placed"]
        self.assertEqual(len(placed), 1)
        self.assertEqual((placed[0]["kind"], placed[0]["max_loss"]), ("entry", 46.88))
        self.assertNotIn("approved", {o["status"] for o in orders})  # the bot never records a human yes

    def test_journal_and_report(self):
        kinds = [r["kind"] for r in self.bot.store.journal()]
        self.assertEqual(kinds[-3:], ["entry", "exit", "report"])
        report = json.loads((self.bot.store.reports_dir / "2026-10-19.json").read_text())
        self.assertEqual((report["trades"], report["wins"], report["pnl"]), (1, 1, 17.12))

    def test_dashboard_state(self):
        st = Dashboard(Path(self.tmp.name) / "bot.toml", str(Path(self.tmp.name) / "ws")).state()
        self.assertEqual(st["mode"], "paper")
        self.assertIsNone(st["position"])
        self.assertEqual(st["today"]["entries"], 1)
        self.assertEqual(st["today"]["loss_used"], 46.88)
        self.assertEqual(st["today"]["pnl"], 17.12)
        self.assertEqual(st["account"]["equity"], 5017.12)


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_paused_bot_does_not_enter(self):
        cfg = Path(self.tmp.name) / "bot.toml"
        cfg.write_text('[plan]\ntarget = "1R"\n')
        ws = str(Path(self.tmp.name) / "ws")
        Dashboard(cfg, ws).control({"paused": True})
        bot = Bot(cfg, workspace=ws, replay=ReplayData(DATA))
        replay_day(bot, "2026-10-19", "10:15", "10:15", echo=lambda _: None)
        self.assertIsNone(bot.store.state()["position"])
        self.assertEqual(bot.ws.read("state/broker.json"), None)

    def test_flatten_request_closes_the_position(self):
        bot = make_bot(self.tmp.name, end="10:20", start="10:15")
        self.assertIsNotNone(bot.store.state()["position"])
        bot.store.set_control(flatten=True)
        replay_day(bot, "2026-10-19", "10:25", "10:25", echo=lambda _: None)
        self.assertIsNone(bot.store.state()["position"])
        self.assertEqual(bot.store.trades()[0]["reason"], "manual")
        self.assertFalse(bot.store.control()["flatten"])

    def test_live_mode_leaves_a_ticket(self):
        cfg = Path(self.tmp.name) / "bot.toml"
        cfg.write_text('mode = "live"\n[plan]\ntarget = "1R"\n')
        bot = Bot(cfg, workspace=str(Path(self.tmp.name) / "ws"), replay=ReplayData(DATA))
        replay_day(bot, "2026-10-19", "10:15", "10:20", echo=lambda _: None)
        st = bot.store.state()
        self.assertIsNone(st["position"])
        self.assertEqual(st["pending"]["symbol"], "TSLA")
        self.assertIsNone(bot.ws.read("state/broker.json"))  # nothing placed
        Dashboard(cfg, str(bot.ws.root)).control({"clear_pending": True})
        replay_day(bot, "2026-10-19", "10:25", "10:25", echo=lambda _: None)
        self.assertIsNone(bot.store.state()["pending"])


class Settings(unittest.TestCase):
    def test_save_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config" / "bot.toml"
            dash = Dashboard(cfg, str(Path(tmp) / "ws"))
            dash.save_settings(
                {
                    "mode": "paper",
                    "plan": {"target": "1R"},
                    "limits": {"max_loss_per_trade_usd": 40, "flat_by": "15:50"},
                    "filters": {"block_news_tags": ["halt"]},
                }
            )
            from tradingbot import config

            loaded = config.load(cfg)
            self.assertEqual(loaded.plan.target, "1R")
            self.assertEqual(loaded.limits, {"max_loss_per_trade_usd": 40, "flat_by": "15:50"})
            self.assertEqual(loaded.filters.block_news_tags, ["halt"])

    def test_bad_settings_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash = Dashboard(Path(tmp) / "bot.toml", tmp)
            with self.assertRaises(ValueError):
                dash.save_settings({"mode": "yolo"})
            with self.assertRaises(ValueError):
                dash.save_settings({"limits": {"bad key!": 1}})
            with self.assertRaises(TypeError):
                dash.save_settings({"plan": {"nope": 1}})
            self.assertFalse((Path(tmp) / "bot.toml").exists())


if __name__ == "__main__":
    unittest.main()


class Http(unittest.TestCase):
    def test_endpoints(self):
        import urllib.error
        import urllib.request

        from tradingbot.server import serve

        with tempfile.TemporaryDirectory() as tmp:
            httpd = serve(Dashboard(Path(tmp) / "bot.toml", tmp), port=0, background=True)
            self.addCleanup(httpd.shutdown)
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn(b"Trading bot", r.read())
            with urllib.request.urlopen(base + "/api/state") as r:
                self.assertEqual(json.loads(r.read())["mode"], "paper")
            form = urllib.request.Request(base + "/api/control", data=b"paused=1", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(form)
            self.assertEqual(cm.exception.code, 415)  # a cross-site form post cannot reach the controls
            req = urllib.request.Request(
                base + "/api/control",
                data=b'{"paused": true}',
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as r:
                self.assertEqual(json.loads(r.read()), {"paused": True})
