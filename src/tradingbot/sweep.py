"""Compare settings: the same backtest once per variant, side by side.

A variant overrides the plan's stop and target and the flat-by time. Each one
runs as a full backtest in its own workspace (runs/sweep/<name>), several at a
time in separate processes, and runs/sweep/bot/sweep.json is rewritten every
few seconds so the dashboard can follow.

Ranking a variant first only says it did best over these days. With this
little history the best of several is partly luck; paper-trade it before
trusting it.
"""

from __future__ import annotations

import os
import shutil
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from . import backtest
from . import config as config_mod
from .store import read_json, write_json

# Every stop x target x flat_by: 2 x 4 x 3 = 24 variants, seconds in the fast mode.
DEFAULT_VARIANTS = [
    "stop=%s,target=%s,flat_by=%s" % (stop, target, flat)
    for stop in ("or-mid", "or-low")
    for target in ("1R", "1.5R", "2R", "3R")
    for flat in ("11:00", "12:30", "15:55")
]
# Six of them for the full (slow) mode.
FULL_VARIANTS = [
    "stop=or-mid,target=1R",
    "stop=or-mid,target=1.5R",
    "stop=or-mid,target=2R",
    "stop=or-low,target=1R",
    "stop=or-mid,target=1R,flat_by=12:00",
    "stop=or-mid,target=2R,flat_by=12:00",
]
KEYS = ("stop", "target", "flat_by")


def parse_variant(text: str) -> dict[str, str]:
    out = {}
    for part in text.split(","):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in KEYS or not value:
            raise ValueError("variant %r: use %s=..." % (text, "|".join(KEYS)))
        out[key] = value
    if not out:
        raise ValueError("empty variant")
    return out


def name_of(v: dict[str, str]) -> str:
    return "_".join("%s-%s" % (k, v[k].replace(":", "").replace(".", "p")) for k in KEYS if k in v)


def label_of(v: dict[str, str]) -> str:
    return ", ".join("%s %s" % (k, v[k]) for k in KEYS if k in v)


def variant_config(base: config_mod.BotConfig, v: dict[str, str]) -> config_mod.BotConfig:
    plan = replace(base.plan, stop=v.get("stop", base.plan.stop), target=v.get("target", base.plan.target))
    limits = dict(base.limits)
    if "flat_by" in v:
        limits["flat_by"] = v["flat_by"]
        # trader-exit refuses an entry at or after flat_by: stop looking 30 minutes before it
        cutoff = (datetime.strptime(v["flat_by"], "%H:%M") - timedelta(minutes=30)).strftime("%H:%M")
        limits["no_entries_after"] = min(limits.get("no_entries_after", "15:30"), cutoff)
    return replace(base, plan=plan, limits=limits, notify=config_mod.NotifySettings(events=[]))


def _run_one(config_text: str, history_dir: str, workspace: str, symbols, days) -> dict:
    """One variant, in a worker process."""
    ws = Path(workspace)
    cfg_path = ws.parent / (ws.name + ".toml")  # beside the workspace: backtest.run empties the workspace
    cfg_path.write_text(config_text, encoding="utf-8")
    return backtest.run(cfg_path, Path(history_dir), ws, symbols=symbols, days=days, echo=lambda *_: None)


def _snapshot(root: Path, variants: list[dict], status: dict[str, str], errors: dict[str, str]) -> dict:
    rows = []
    for v in variants:
        name = name_of(v)
        res = read_json(root / name / "bot" / "backtest.json") or {}
        rows.append(
            {
                "name": name,
                "label": label_of(v),
                "status": status[name] if status[name] != "running" else res.get("status", "starting"),
                "error": errors.get(name),
                "done": res.get("done", 0),
                **{
                    k: res.get(k)
                    for k in (
                        "days",
                        "trades",
                        "wins",
                        "win_rate",
                        "pnl",
                        "r_avg",
                        "r_total",
                        "max_drawdown",
                        "exits",
                        "from",
                        "to",
                    )
                },
            }
        )
    rank(rows)
    return {"status": "done" if all(s != "running" for s in status.values()) else "running", "variants": rows}


def rank(rows: list[dict]) -> None:
    """Best average R first (then the most money); variants without a trade have no R and go last."""
    finished = [r for r in rows if r["status"] == "done" and r["r_avg"] is not None]
    finished.sort(key=lambda r: (r["r_avg"], r["pnl"]), reverse=True)
    for n, r in enumerate(finished, 1):
        r["rank"] = n
    rows.sort(key=lambda r: (r.get("rank") is None, r.get("rank") or 0))


def run(
    config_path: Path | None,
    history_dir: Path,
    root: Path,
    variants: list[dict],
    symbols=None,
    days: int | None = None,
    workers: int | None = None,
    echo=print,
) -> dict:
    base = config_mod.load(config_path if config_path and Path(config_path).is_file() else None)
    names = [name_of(v) for v in variants]
    if len(set(names)) != len(names):
        raise ValueError("two variants are the same")
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    (root / "bot").mkdir(parents=True)
    workers = workers or max(1, min(len(variants), (os.cpu_count() or 2) - 1))
    status = {n: "running" for n in names}
    errors: dict[str, str] = {}
    echo("Palyginimas: %d variantai, %d procesai vienu metu" % (len(variants), workers))
    write_json(root / "bot" / "sweep.json", _snapshot(root, variants, status, errors))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_one,
                config_mod.dump(variant_config(base, v)),
                str(history_dir),
                str(root / name_of(v)),
                symbols,
                days,
            ): v
            for v in variants
        }
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            for f in done:
                v = futures[f]
                name = name_of(v)
                try:
                    res = f.result()
                    status[name] = "done"
                    echo(
                        "baigta: %-40s sandoriai %s · rezultatas %s · vid. R %s"
                        % (label_of(v), res["trades"], res["pnl"], res["r_avg"])
                    )
                except Exception as exc:  # one broken variant must not lose the others
                    status[name] = "error"
                    errors[name] = "%s: %s" % (type(exc).__name__, exc)
                    echo("klaida: %s: %s" % (label_of(v), errors[name]))
            write_json(root / "bot" / "sweep.json", _snapshot(root, variants, status, errors))
    return _snapshot(root, variants, status, errors)


def run_fast(
    config_path: Path | None,
    history_dir: Path,
    root: Path,
    variants: list[dict],
    symbols=None,
    days: int | None = None,
    echo=print,
) -> dict:
    """Every variant through fastbt, in this process: the data is loaded once."""
    from . import fastbt

    base = config_mod.load(config_path if config_path and Path(config_path).is_file() else None)
    names = [name_of(v) for v in variants]
    if len(set(names)) != len(names):
        raise ValueError("two variants are the same")
    data = fastbt.load(Path(history_dir), symbols)
    if not data:
        raise ValueError("no history in %s: run `tradingbot fetch` first" % history_dir)
    root = Path(root)
    rows = []
    for i, v in enumerate(variants, 1):
        row = {"name": name_of(v), "label": label_of(v), "status": "done", "error": None}
        try:
            res = fastbt.run(variant_config(base, v), data, days=days)
            row.update(
                {
                    k: res.get(k)
                    for k in (
                        "days",
                        "trades",
                        "wins",
                        "win_rate",
                        "pnl",
                        "r_avg",
                        "r_total",
                        "max_drawdown",
                        "exits",
                        "from",
                        "to",
                    )
                }
            )
            row["done"] = res["days"]
        except Exception as exc:  # one broken variant must not lose the others
            row.update(status="error", error="%s: %s" % (type(exc).__name__, exc))
        rows.append(row)
        echo(
            "%2d/%d %-45s %s"
            % (
                i,
                len(variants),
                row["label"],
                row["error"] or "sandoriai %s · vid. R %s" % (row["trades"], row["r_avg"]),
            )
        )
    rank(rows)
    out = {"status": "done", "mode": "fast", "variants": rows}
    write_json(root / "bot" / "sweep.json", out)
    return out
