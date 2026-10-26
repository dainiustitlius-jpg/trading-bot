"""The bot loop: one tick every 5 minutes, the same code live and in a replay.

A tick:
1. reloads config/bot.toml (the dashboard may have changed it) and the workspace;
2. asks the NYSE clock;
3. with a paper position open: walks its new 5-minute bars (stop, target, flat_by);
4. otherwise, unless paused or a live ticket waits for the user: one pipeline pass;
   a PASS is filled by the paper broker (paper) or left as a ticket for /trader-broker (live);
5. after the close: the day's report, once.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from . import config as config_mod
from .bars import BarsError, closed_bars
from .clock import Clock
from .paper import PaperBroker, scan_bars
from .pipeline import Pipeline, pass_dict
from .skills import ReplayData, SkillRunner
from .store import BotStore
from .workspace import Workspace

TICK = timedelta(minutes=5)
SETTLE = timedelta(seconds=30)  # wait for Yahoo to publish the bar that just closed


def money(x: float) -> str:
    return "%s$%s" % ("-" if x < 0 else "", format(abs(x), ",.2f"))


class Bot:
    def __init__(
        self,
        config_path: str | Path | None,
        workspace: str | None = None,
        clock: Clock | None = None,
        replay: ReplayData | None = None,
    ):
        self.config_path = Path(config_path) if config_path else None
        self.workspace_override = workspace
        self.clock = clock or Clock()
        self.replay = replay
        self.reload()

    def reload(self) -> None:
        path = self.config_path if self.config_path and self.config_path.is_file() else None
        self.cfg = config_mod.load(path)
        self.ws = Workspace(self.workspace_override or self.cfg.workspace_dir())
        self.ws.prepare(self.cfg.limits, paper=self.cfg.mode == "paper")
        self.store = BotStore(self.ws.root)
        self.runner = SkillRunner(self.ws.root.resolve(), clock=self.clock, replay=self.replay)
        self.pipeline = Pipeline(self.cfg, self.runner, self.ws)
        self.broker = PaperBroker(self.ws, self.runner, self.cfg.paper.slippage_bps)

    # -- one tick ------------------------------------------------------------

    def tick(self) -> str:
        self.reload()
        now = self.clock.now()
        at = now.isoformat(timespec="seconds")
        today = now.date().isoformat()
        st = self.store.state()
        if st.get("date") != today:
            if st.get("position"):
                self.store.log(
                    at,
                    "error",
                    "Pozicija liko atidaryta per naktį",
                    "%s: patikrinkite rankiniu būdu" % st["position"]["symbol"],
                )
            st = {"date": today, "position": None, "pending": None, "last_pass": None, "reported": False}
        st["mode"] = self.cfg.mode
        st["updated"] = at
        ctl = self.store.control()
        if ctl.get("clear_pending"):
            if st.get("pending"):
                self.store.log(
                    at, "ticket", "%s: bilietas atmestas" % st["pending"]["symbol"], "atmetė vartotojas"
                )
            st["pending"] = None
            self.store.set_control(clear_pending=False)
        market = self.pipeline.market()
        st["market"] = {"ok": market.ok, "summary": market.summary, "data": market.data}

        if st.get("position"):
            line = self._manage(st, now, ctl)
        elif not market.ok:
            line = market.summary
        elif ctl.get("paused"):
            line = "pristabdyta: nauji įėjimai neieškomi"
        elif st.get("pending"):
            line = "laukia jūsų patvirtinimo: %s" % st["pending"]["symbol"]
        else:
            line = self._look_for_entry(st, now)

        reason = (market.data or {}).get("reason")
        if reason == "after the close" and not st.get("reported"):
            self._report(st, now)
            st["reported"] = True
        if ctl.get("flatten"):
            self.store.set_control(flatten=False)
        self.store.save_state(st)
        return "%s %s" % (now.strftime("%H:%M"), line)

    def _look_for_entry(self, st: dict, now: datetime) -> str:
        at = now.isoformat(timespec="seconds")
        res = self.pipeline.run_once()
        st["last_pass"] = pass_dict(res)
        if res.scan is None or not res.scan.ok:
            return res.scan.summary if res.scan else res.market.summary
        chosen = res.chosen
        skipped = len(res.evaluations) - (1 if chosen else 0)
        if res.evaluations:  # each candidate's reasons stay in state.json's last_pass for the dashboard
            stops = ", ".join(
                "%s (%s)" % (ev.symbol, ev.stopped_at.name)
                for ev in res.evaluations
                if not ev.passed and ev.stopped_at
            )
            self.store.log(
                at,
                "scan",
                "Patikrinta %d kandidat., praleista %d" % (len(res.evaluations), skipped),
                stops or res.scan.summary,
            )
        if not chosen:
            return "nė vienas kandidatas nepraėjo"
        risk = next(s for s in chosen.steps if s.name == "risk").data["results"][0]
        o = chosen.order
        detail = "pirkti %s · stop-loss %s · take-profit %s · max nuostolis %s" % (
            o["qty"],
            o["stop_loss_stop_price"],
            o["take_profit_limit_price"],
            money(risk["max_loss"]),
        )
        if self.cfg.mode == "live":
            st["pending"] = {"symbol": chosen.symbol, "order": o, "at": at, "max_loss": risk["max_loss"]}
            self.store.log(
                at,
                "ticket",
                "%s: bilietas paruoštas, laukia jūsų" % chosen.symbol,
                detail + ". Patvirtinkite per /trader-broker.",
            )
            return "%s bilietas laukia /trader-broker" % chosen.symbol
        plan = self.ws.read("plan.json") or {}
        pos = self.broker.open(o, risk, plan, now)
        st["position"] = pos
        self.store.log(
            at,
            "entry",
            "%s: paper orderis įvykdytas" % chosen.symbol,
            "%s · įėjimas %.2f" % (detail, pos["entry"]),
        )
        return "%s pirkta %s po %.2f" % (chosen.symbol, pos["qty"], pos["entry"])

    def _manage(self, st: dict, now: datetime, ctl: dict) -> str:
        pos = st["position"]
        at = now.isoformat(timespec="seconds")
        offline = self.replay.path("charts") if self.replay else None
        try:
            bars = closed_bars(pos["symbol"], now, offline)
        except BarsError as exc:
            self.store.log(at, "error", "%s: nepavyko gauti barų" % pos["symbol"], str(exc))
            bars = []
        ex, update = scan_bars(
            pos, bars, now, self.cfg.paper.slippage_bps, force="manual" if ctl.get("flatten") else None
        )
        pos.update(update)
        if ex is None:
            return "%s laikoma, kaina %.2f" % (pos["symbol"], pos["last_price"])
        trade = self.broker.close(pos, ex)
        self.store.add_trade(trade)
        names = {
            "stop": "stop-loss",
            "target": "take-profit",
            "flat_by": "uždaryta iki flat_by",
            "manual": "uždaryta rankiniu būdu",
        }
        self.store.log(
            at,
            "exit",
            "%s: %s" % (pos["symbol"], names[ex.reason]),
            "parduota %d po %.2f · %s · %+.2fR"
            % (trade["qty"], trade["exit"], money(trade["pnl"]), trade["r"] or 0),
        )
        st["position"] = None
        return "%s uždaryta (%s) %s" % (pos["symbol"], ex.reason, money(trade["pnl"]))

    def _report(self, st: dict, now: datetime) -> None:
        today = now.date().isoformat()
        trades = [t for t in self.store.trades() if t.get("date") == today]
        pnl = round(sum(t["pnl"] for t in trades), 2)
        rs = [t["r"] for t in trades if t.get("r") is not None]
        wins = sum(1 for t in trades if t["pnl"] > 0)
        report = {
            "date": today,
            "mode": self.cfg.mode,
            "trades": len(trades),
            "wins": wins,
            "pnl": pnl,
            "r_total": round(sum(rs), 2),
            "r_avg": round(sum(rs) / len(rs), 2) if rs else None,
            "list": trades,
        }
        self.store.save_report(today, report)
        self.store.log(
            now.isoformat(timespec="seconds"),
            "report",
            "Dienos ataskaita",
            "%d sandor. · laimėta %d · %s · %+.2fR" % (len(trades), wins, money(pnl), report["r_total"]),
        )

    # -- loops ---------------------------------------------------------------

    def run_forever(self, echo=print) -> None:
        """Real time: a tick just after every 5-minute bar closes."""
        while True:
            try:
                echo(self.tick())
            except Exception as exc:  # keep the loop alive; the journal shows it
                now = self.clock.now().isoformat(timespec="seconds")
                self.store.log(now, "error", "Klaida cikle", "%s: %s" % (type(exc).__name__, exc))
                echo("klaida: %s" % exc)
            now = self.clock.now()
            nxt = now.replace(second=0, microsecond=0)
            nxt += timedelta(minutes=5 - nxt.minute % 5)
            _time.sleep(max(1.0, ((nxt + SETTLE) - now).total_seconds()))


def replay_day(bot: Bot, date: str, start: str = "09:30", end: str = "16:05", echo=print) -> None:
    """Tick through one day on a pinned clock, every 5 minutes."""
    t = datetime.fromisoformat("%s %s" % (date, start))
    stop = datetime.fromisoformat("%s %s" % (date, end))
    while t <= stop:
        bot.clock = Clock(t.strftime("%Y-%m-%d %H:%M"))
        echo(bot.tick())
        t += TICK
