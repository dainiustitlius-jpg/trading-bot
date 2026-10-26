"""The paper broker: fills an order trader-risk passed, then watches the bracket bar by bar.

- Entry: the plan's entry (the trigger bar's close) plus slippage.
- Each closed 5-minute bar after the entry: the stop first, then the target (as the
  Time Machine does). A bar that opens past the stop fills at its open, not at the stop.
- At flat_by (or when the dashboard asks): out at the last closed bar's close, less slippage.

It writes the same records trader-broker would, so trader-risk counts the paper
trades in its daily limits: a "placed" entry in trader/state/broker.json and the
fills in trader/portfolio.json through `ticket.py fill`. It never records an
approval: paper orders are the bot's own, and live orders stay with the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bars import Bar
from .clock import parse_ny
from .skills import SkillRunner
from .store import read_json, write_json
from .workspace import Workspace


def cents(x: float) -> float:
    return round(x + 1e-9, 2)


@dataclass
class Exit:
    price: float
    reason: str  # stop | target | flat_by | manual
    at: datetime
    bar: str | None  # the bar's start, "10:35"


def scan_bars(
    pos: dict, bars: list[Bar], now: datetime, slippage_bps: float, force: str | None = None
) -> tuple[Exit | None, dict]:
    """Pure: walk the bars not checked yet. Returns (exit or None, fields to update on the position)."""
    slip = slippage_bps / 10_000
    entered = parse_ny(pos["entry_at"])
    checked = parse_ny(pos["checked_until"]) if pos.get("checked_until") else entered
    update: dict = {}
    for b in bars:
        if b.start < entered or b.end <= checked:
            continue
        update.update(
            checked_until=b.end.isoformat(), last_price=cents(b.close), last_price_at=b.end.isoformat()
        )
        if b.low <= pos["stop"]:
            fill = min(pos["stop"], b.open) * (1 - slip)
            return Exit(cents(fill), "stop", b.end, b.start.strftime("%H:%M")), update
        if b.high >= pos["target"]:
            return Exit(cents(max(pos["target"], b.open)), "target", b.end, b.start.strftime("%H:%M")), update
    flat_by = parse_ny("%s %s" % (now.date().isoformat(), pos["flat_by"]))
    reason = force or ("flat_by" if now >= flat_by else None)
    if reason:
        last = update.get("last_price", pos.get("last_price"))
        if last is None:
            return None, update  # no price yet; the next tick tries again
        return Exit(cents(last * (1 - slip)), reason, now, None), update
    return None, update


class PaperBroker:
    def __init__(self, ws: Workspace, runner: SkillRunner, slippage_bps: float):
        self.ws = ws
        self.runner = runner
        self.slippage_bps = slippage_bps

    def _log_placed(self, record: dict) -> None:
        path = self.ws.file("state/broker.json")
        state = read_json(path, None) or {"orders": []}
        state.setdefault("orders", []).append(record)
        write_json(path, state)

    def _fill(self, symbol: str, side: str, qty: int, price: float) -> None:
        res = self.runner.run(
            "broker",
            "fill",
            "--symbol",
            symbol,
            "--side",
            side,
            "--qty",
            str(qty),
            "--price",
            "%.2f" % price,
            json_out=False,
        )
        if not res.ok:
            raise RuntimeError("ticket.py fill: %s" % res.error())

    def open(self, order: dict, risk: dict, plan: dict, now: datetime) -> dict:
        """Fill the entry of a bracket order trader-risk passed. Returns the position."""
        qty = int(float(order["qty"]))
        entry_plan = float(plan["entry"])
        fill = cents(entry_plan * (1 + self.slippage_bps / 10_000))
        today = now.date().isoformat()
        oid = "paper-%s-%s-%s" % (today, order["symbol"], now.strftime("%H%M"))
        self._log_placed(
            {
                "at": now.isoformat(timespec="seconds"),
                "date": today,
                "status": "placed",
                "kind": risk.get("kind", "entry"),
                "symbol": order["symbol"],
                "side": order["side"],
                "qty": order["qty"],
                "max_loss": risk.get("max_loss"),
                "stop": risk.get("stop"),
                "fingerprint": risk.get("fingerprint"),
                "broker_order_id": oid,
                "plain": "BUY %s %s bracket (paper)" % (qty, order["symbol"]),
                "note": "tradingbot paper broker: simulated, no broker and no approval involved",
            }
        )
        self._fill(order["symbol"], "buy", qty, fill)
        return {
            "symbol": order["symbol"],
            "qty": qty,
            "entry": fill,
            "entry_plan": entry_plan,
            "entry_at": now.isoformat(timespec="seconds"),
            "stop": float(order["stop_loss_stop_price"]),
            "target": float(order["take_profit_limit_price"]),
            "max_loss": risk.get("max_loss"),
            "risk_per_share": plan.get("risk_per_share"),
            "flat_by": plan.get("flat_by", "15:55"),
            "order_id": oid,
            "last_price": fill,
            "last_price_at": now.isoformat(timespec="seconds"),
            "checked_until": None,
        }

    def close(self, pos: dict, ex: Exit) -> dict:
        """Record the exit fill and return the closed trade."""
        self._fill(pos["symbol"], "sell", pos["qty"], ex.price)
        return trade_record(pos, ex, "paper")


def trade_record(pos: dict, ex: Exit, mode: str) -> dict:
    risk_ps = pos.get("risk_per_share") or (pos["entry_plan"] - pos["stop"])
    pnl = cents(pos["qty"] * (ex.price - pos["entry"]))
    return {
        "date": parse_ny(pos["entry_at"]).date().isoformat(),
        "symbol": pos["symbol"],
        "qty": pos["qty"],
        "entry": pos["entry"],
        "entry_at": pos["entry_at"],
        "stop": pos["stop"],
        "target": pos["target"],
        "exit": ex.price,
        "exit_at": ex.at.isoformat(timespec="seconds"),
        "exit_bar": ex.bar,
        "reason": ex.reason,
        "pnl": pnl,
        "r": round((ex.price - pos["entry"]) / risk_ps, 2) if risk_ps else None,
        "max_loss": pos.get("max_loss"),
        "order_id": pos.get("order_id"),
        "mode": mode,
    }
