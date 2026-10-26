import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tradingbot import backtest, history
from tradingbot.bot import Bot, replay_day
from tradingbot.config import NotifySettings
from tradingbot.notify import Notifier
from tradingbot.skills import ReplayData
from tradingbot.store import read_json, write_json

FIX = Path(__file__).resolve().parent / "fixtures" / "replay-2026-10-19"
HIST = FIX / "charts"  # holds TSLA-60d5m.json.gz


class History(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = history.load_chart(HIST / "TSLA-60d5m.json.gz")

    def test_full_sessions(self):
        days = history.full_sessions(self.chart)
        self.assertEqual((len(days), days[0], days[-1]), (59, date(2026, 7, 27), date(2026, 10, 16)))

    def test_build_day_has_no_later_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            have = history.build_day({"TSLA": self.chart}, date(2026, 9, 3), Path(tmp))
            self.assertEqual(have, ["TSLA"])
            day = json.loads((Path(tmp) / "charts" / "TSLA.json").read_text())
            upto = json.loads((Path(tmp) / "charts" / "TSLA-60d5m.json").read_text())
            self.assertEqual(history.full_sessions(day), [date(2026, 9, 3)])
            self.assertEqual(history.full_sessions(upto)[-1], date(2026, 9, 3))
            meta = day["chart"]["result"][0]["meta"]
            self.assertIsNotNone(meta["chartPreviousClose"])  # the day before's close, not the 60-day start
            bundle = json.loads((Path(tmp) / "scan" / "bundle.json").read_text())
            self.assertEqual(list(bundle), ["TSLA"])


class Backtest(unittest.TestCase):
    def test_summarize(self):
        trades = [{"pnl": 46.2, "r": 1.0, "reason": "target"}, {"pnl": -50.89, "r": -1.1, "reason": "stop"}]
        s = backtest.summarize(trades, 5000)
        self.assertEqual((s["trades"], s["wins"], s["pnl"], s["max_drawdown"]), (2, 1, -4.69, 50.89))
        self.assertEqual(s["exits"], {"target": 1, "stop": 1})

    def test_one_day_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "bot.toml"
            cfg.write_text('[plan]\ntarget = "1R"\n')
            res = backtest.run(cfg, HIST, Path(tmp) / "bt", days=1, echo=lambda *_: None)
            self.assertEqual((res["from"], res["days"], res["symbols"]), ("2026-10-16", 1, ["TSLA"]))
            self.assertTrue((Path(tmp) / "bt" / "bot" / "backtest.json").is_file())
            self.assertFalse((Path(tmp) / "bt" / "_days").exists())

    def test_too_little_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                backtest.run(None, Path(tmp), Path(tmp) / "bt", echo=lambda *_: None)


class Live(unittest.TestCase):
    """Live mode: the bot leaves a ticket, the user places it through /trader-broker, the bot follows."""

    def test_follows_the_broker_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "bot.toml"
            cfg.write_text('mode = "live"\n[plan]\ntarget = "1R"\n')
            bot = Bot(cfg, workspace=str(Path(tmp) / "ws"), replay=ReplayData(FIX))
            replay_day(bot, "2026-10-19", "10:15", "10:15", echo=lambda _: None)
            self.assertEqual(bot.store.state()["pending"]["symbol"], "TSLA")

            # what ticket.py writes after the user's yes and the Allow once card
            log_path = bot.ws.file("state/broker.json")
            base = {"date": "2026-10-19", "symbol": "TSLA", "side": "buy"}
            write_json(
                log_path,
                {
                    "orders": [
                        {**base, "at": "2026-10-19T10:17:00-04:00", "status": "approved"},
                        {
                            **base,
                            "at": "2026-10-19T10:17:30-04:00",
                            "status": "placed",
                            "kind": "entry",
                            "qty": "8",
                            "max_loss": 46.88,
                            "broker_order_id": "abc",
                        },
                        {
                            **base,
                            "at": "2026-10-19T10:17:31-04:00",
                            "status": "filled",
                            "qty": "8",
                            "price": 364.3,
                        },
                    ]
                },
            )
            replay_day(bot, "2026-10-19", "10:20", "10:20", echo=lambda _: None)
            pos = bot.store.state()["position"]
            self.assertEqual((pos["mode"], pos["qty"], pos["entry"]), ("live", 8, 364.3))

            replay_day(bot, "2026-10-19", "15:55", "15:55", echo=lambda _: None)
            alerts = [r for r in bot.store.journal() if r["kind"] == "alert"]
            self.assertEqual(len(alerts), 1)
            self.assertIn("flatten", alerts[0]["detail"])
            replay_day(bot, "2026-10-19", "15:56", "15:56", echo=lambda _: None)  # rings once
            self.assertEqual(len([r for r in bot.store.journal() if r["kind"] == "alert"]), 1)

            log = read_json(log_path)
            log["orders"].append(
                {
                    **base,
                    "side": "sell",
                    "at": "2026-10-19T15:57:00-04:00",
                    "status": "filled",
                    "qty": "8",
                    "price": 366.0,
                }
            )
            write_json(log_path, log)
            replay_day(bot, "2026-10-19", "15:58", "15:58", echo=lambda _: None)
            self.assertIsNone(bot.store.state()["position"])
            t = bot.store.trades()[0]
            self.assertEqual((t["mode"], t["exit"], t["reason"], t["pnl"]), ("live", 366.0, "manual", 13.6))

    def test_declined_ticket_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "bot.toml"
            cfg.write_text('mode = "live"\n[plan]\ntarget = "1R"\n')
            bot = Bot(cfg, workspace=str(Path(tmp) / "ws"), replay=ReplayData(FIX))
            replay_day(bot, "2026-10-19", "10:15", "10:15", echo=lambda _: None)
            write_json(
                bot.ws.file("state/broker.json"),
                {
                    "orders": [
                        {
                            "date": "2026-10-19",
                            "symbol": "TSLA",
                            "side": "buy",
                            "at": "2026-10-19T10:16:00-04:00",
                            "status": "declined",
                        }
                    ]
                },
            )
            replay_day(bot, "2026-10-19", "10:20", "10:20", echo=lambda _: None)
            st = bot.store.state()
            self.assertIsNone(st["pending"])
            self.assertIsNone(st["position"])


class Notify(unittest.TestCase):
    def capture(self, n):
        sent = []
        n._post = lambda url, body, ctype: sent.append((url, json.loads(body)))
        return sent

    def test_channels_need_their_secret(self):
        s = NotifySettings(telegram_chat_id="42", ntfy_topic="my-topic")
        self.assertEqual(Notifier(s, env={}).channels(), ["ntfy"])
        self.assertEqual(Notifier(s, env={"TELEGRAM_BOT_TOKEN": "t"}).channels(), ["telegram", "ntfy"])

    def test_payloads(self):
        n = Notifier(
            NotifySettings(telegram_chat_id="42", ntfy_topic="my-topic"), env={"TELEGRAM_BOT_TOKEN": "T"}
        )
        sent = self.capture(n)
        self.assertEqual(n.send("TSLA: paper orderis įvykdytas", "pirkti 8"), [])
        (tg_url, tg), (ntfy_url, nt) = sent
        self.assertEqual(tg_url, "https://api.telegram.org/botT/sendMessage")
        self.assertEqual(tg, {"chat_id": "42", "text": "TSLA: paper orderis įvykdytas\npirkti 8"})
        self.assertEqual(ntfy_url, "https://ntfy.sh")
        self.assertEqual((nt["topic"], nt["title"]), ("my-topic", "TSLA: paper orderis įvykdytas"))

    def test_a_dead_channel_is_reported_not_raised(self):
        n = Notifier(NotifySettings(ntfy_topic="t"), env={})

        def boom(*_):
            raise OSError("no network")

        n._post = boom
        self.assertEqual(n.send("x"), ["ntfy: no network"])

    def test_bot_notifies_live_but_never_in_a_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "bot.toml"
            cfg.write_text('[notify]\nntfy_topic = "t"\nevents = ["entry"]\n')
            bot = Bot(cfg, workspace=str(Path(tmp) / "ws"))
            sent = self.capture(bot.notifier)
            bot.log("2026-10-19T10:15:00-04:00", "entry", "TSLA pirkta", "")
            bot.log("2026-10-19T10:15:00-04:00", "scan", "Patikrinta", "")  # not in events
            self.assertEqual(len(sent), 1)
            bot.replay = ReplayData(FIX)
            bot.log("2026-10-19T10:20:00-04:00", "entry", "replay", "")
            self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
