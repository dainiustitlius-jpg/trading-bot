"""The dashboard: a local web page over the workspace files.

Standard library only. Binds to 127.0.0.1 by default, so only this computer can
open it. The page polls GET /api/state; the buttons POST JSON to /api/control
and /api/settings. A POST must be application/json, which a page on another
site cannot send here without a CORS preflight this server never answers.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import config as config_mod
from .store import BotStore, read_json
from .workspace import Workspace

WEB = Path(__file__).resolve().parent / "web" / "index.html"


class Dashboard:
    def __init__(self, config_path: str | Path, workspace: str | None = None):
        self.config_path = Path(config_path)
        self.workspace_override = workspace

    def _load(self) -> tuple[config_mod.BotConfig, Workspace, BotStore]:
        cfg = config_mod.load(self.config_path if self.config_path.is_file() else None)
        ws = Workspace(self.workspace_override or cfg.workspace_dir())
        return cfg, ws, BotStore(ws.root)

    # -- GET /api/state ------------------------------------------------------

    def state(self) -> dict:
        cfg, ws, store = self._load()
        st = store.state()
        limits = ws.config()
        today = st.get("date")
        pf = ws.read("portfolio.json") or {}
        cash = float(pf.get("cash", limits.get("budget") or 0))
        pos = st.get("position")
        pos_value = round(pos["qty"] * pos["last_price"], 2) if pos else 0.0
        trades = store.trades()
        today_trades = [t for t in trades if t.get("date") == today]
        realized = round(sum(t["pnl"] for t in today_trades), 2)
        open_pnl = round(pos["qty"] * (pos["last_price"] - pos["entry"]), 2) if pos else 0.0
        broker = ws.read("state/broker.json") or {}
        entries = [
            o
            for o in broker.get("orders", [])
            if o.get("status") == "placed" and o.get("date") == today and o.get("kind", "entry") == "entry"
        ]
        return {
            "mode": cfg.mode,
            "workspace": str(ws.root),
            "updated": st.get("updated"),
            "date": today,
            "market": st.get("market"),
            "control": store.control(),
            "account": {
                "budget": limits.get("budget"),
                "cash": cash,
                "position_value": pos_value,
                "equity": round(cash + pos_value, 2),
            },
            "today": {
                "pnl_realized": realized,
                "pnl_open": open_pnl,
                "pnl": round(realized + open_pnl, 2),
                "r_realized": round(sum(t.get("r") or 0 for t in today_trades), 2),
                "entries": len(entries),
                "max_trades": limits.get("max_trades_per_day"),
                "loss_used": round(sum(float(o.get("max_loss") or 0) for o in entries), 2),
                "max_daily_loss": limits.get("max_daily_loss_usd"),
                "no_entries_after": limits.get("no_entries_after"),
            },
            "position": pos,
            "pending": st.get("pending"),
            "last_pass": st.get("last_pass"),
            "journal": list(reversed(store.journal(60))),
            "trades": list(reversed(trades[-30:])),
            "settings": self.settings(cfg, limits),
            "backtest": read_json(store.dir / "backtest.json"),
            "sweep": read_json(store.dir / "sweep.json"),
        }

    @staticmethod
    def settings(cfg: config_mod.BotConfig, limits: dict) -> dict:
        data = asdict(cfg)
        data["limits"] = {**limits, **cfg.limits}
        return data

    # -- POST /api/settings --------------------------------------------------

    def save_settings(self, body: dict) -> dict:
        cfg, _, _ = self._load()
        mode = body.get("mode", cfg.mode)
        plan = config_mod.PlanSettings(**{**asdict(cfg.plan), **(body.get("plan") or {})})
        scan = config_mod.ScanSettings(**{**asdict(cfg.scan), **(body.get("scan") or {})})
        filters = config_mod.FilterSettings(**{**asdict(cfg.filters), **(body.get("filters") or {})})
        paper = config_mod.PaperSettings(**{**asdict(cfg.paper), **(body.get("paper") or {})})
        notify = config_mod.NotifySettings(**{**asdict(cfg.notify), **(body.get("notify") or {})})
        limits = dict(cfg.limits)
        for k, v in (body.get("limits") or {}).items():
            if not isinstance(k, str) or not k.replace("_", "").isalnum():
                raise ValueError("bad limit name %r" % k)
            limits[k] = v
        new = config_mod.BotConfig(
            mode=mode,
            workspace=cfg.workspace,
            scan=scan,
            plan=plan,
            filters=filters,
            paper=paper,
            notify=notify,
            limits=limits,
        )
        text = config_mod.dump(new)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".toml.tmp")
        tmp.write_text(text, encoding="utf-8")
        config_mod.load(tmp)  # it must read back
        tmp.replace(self.config_path)
        return {"ok": True, "saved": str(self.config_path)}

    # -- POST /api/control ---------------------------------------------------

    def control(self, body: dict) -> dict:
        _, _, store = self._load()
        allowed = {k: bool(v) for k, v in body.items() if k in ("paused", "flatten", "clear_pending")}
        if not allowed:
            raise ValueError("nothing to change")
        return store.set_control(**allowed)


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tradingbot"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(
                status,
                json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(200, dash.state())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON only"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(length, 100_000)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("a JSON object, please")
                if self.path == "/api/settings":
                    return self._json(200, dash.save_settings(body))
                if self.path == "/api/control":
                    return self._json(200, dash.control(body))
                return self._json(404, {"error": "not found"})
            except (ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})

    return Handler


def serve(dash: Dashboard, host: str = "127.0.0.1", port: int = 8765, background: bool = False):
    httpd = ThreadingHTTPServer((host, port), make_handler(dash))
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    httpd.serve_forever()
    return httpd
