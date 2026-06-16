#!/usr/bin/env python3
"""Analyse a production trading run from the JSON logs — session-aware playback.

Reads the rotated structlog JSON logs pulled into ``logs/prod/`` (see
``just pull-logs``), filters to a single user, segments the stream into runs
(``engine_started`` → ``engine_stopped``/still-running), and reports performance,
anomalies and a price-annotated event timeline for one chosen run.

IMPORTANT — this is a PUBLIC repo. This script carries NO identifiers. The user
identity is read at runtime from ``analysis.local.json`` (gitignored) or a
``--user-id``/``--email`` flag. The logs it reads live under ``logs/prod/`` and
are gitignored. Nothing here writes PII into a tracked file.

Usage:
    python scripts/analyse_run.py                 # latest run, summary + anomalies
    python scripts/analyse_run.py --list          # list all runs, pick one
    python scripts/analyse_run.py --run 3         # analyse run #3
    python scripts/analyse_run.py --run 3 --timeline   # + full event playback
    python scripts/analyse_run.py --user-id 1234  # override identity (no local config)

Stdlib only — no third-party imports, no project imports. Safe to run anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Events that are pure noise for run analysis (HTTP traffic, debug dumps).
_NOISE_LOGGERS = {"httpx", "httpcore", "uvicorn.access"}
_NOISE_EVENTS = {"debug_trade_markers"}

# Events that delimit and describe a run.
_START = "engine_started"
_STOP = "engine_stopped"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = REPO_ROOT / "logs" / "prod"
LOCAL_CONFIG = REPO_ROOT / "analysis.local.json"


@dataclass
class LogLine:
    """One parsed JSON log record."""

    ts: datetime
    event: str
    raw: dict[str, object]


@dataclass
class Run:
    """A single bot run: engine_started → engine_stopped (or still running)."""

    index: int
    start: LogLine
    stop: LogLine | None
    lines: list[LogLine] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return str(self.start.raw.get("strategy", "?"))

    @property
    def params(self) -> dict[str, object]:
        p = self.start.raw.get("strategy_params")
        return p if isinstance(p, dict) else {}

    @property
    def is_running(self) -> bool:
        return self.stop is None

    @property
    def end_ts(self) -> datetime:
        if self.stop is not None:
            return self.stop.ts
        return self.lines[-1].ts if self.lines else self.start.ts

    @property
    def duration_str(self) -> str:
        secs = (self.end_ts - self.start.ts).total_seconds()
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # structlog ISO timestamps; tolerate trailing 'Z'
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_lines(log_dir: Path, user_id: str) -> list[LogLine]:
    """Read all rotated log files, filter to one user, drop noise, sort by time."""
    files = sorted(log_dir.glob("aurex_trade.log*"))
    if not files:
        sys.exit(
            f"No logs found in {log_dir}. Run `just pull-logs` first to fetch them."
        )

    lines: list[LogLine] = []
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for rawline in fh:
                rawline = rawline.strip()
                if not rawline:
                    continue
                try:
                    rec = json.loads(rawline)
                except json.JSONDecodeError:
                    continue
                if rec.get("user_id") != user_id:
                    continue
                if rec.get("logger") in _NOISE_LOGGERS:
                    continue
                event = str(rec.get("event", ""))
                if event in _NOISE_EVENTS:
                    continue
                ts = _parse_ts(rec.get("timestamp"))
                if ts is None:
                    continue
                lines.append(LogLine(ts=ts, event=event, raw=rec))

    lines.sort(key=lambda x: x.ts)
    return lines


def segment_runs(lines: list[LogLine]) -> list[Run]:
    """Split the chronological line stream into runs by engine_started/stopped."""
    runs: list[Run] = []
    current: Run | None = None
    for ln in lines:
        if ln.event == _START:
            if current is not None:
                runs.append(current)  # previous run ended without a stop marker
            current = Run(index=len(runs) + 1, start=ln, stop=None)
            continue
        if current is None:
            continue  # lines before the first start (carried-over run) — skip
        current.lines.append(ln)
        if ln.event == _STOP:
            current.stop = ln
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    return runs


def fmt_params(params: dict[str, object]) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items())


def print_run_list(runs: list[Run], lines: list[LogLine]) -> None:
    if lines:
        print(
            f"Log window: {lines[0].ts:%Y-%m-%d %H:%M} → {lines[-1].ts:%Y-%m-%d %H:%M} UTC"
            f"  ({len(lines)} events for this user)\n"
        )
    if not runs:
        print("No runs (engine_started) found for this user in the log window.")
        return
    print(f"{'#':>2}  {'Start (UTC)':<17}  {'Dur':>7}  {'Status':<8}  Strategy / params")
    print("-" * 100)
    for r in runs:
        status = "RUNNING" if r.is_running else "stopped"
        print(
            f"{r.index:>2}  {r.start.ts:%Y-%m-%d %H:%M}  {r.duration_str:>7}  "
            f"{status:<8}  {r.strategy}"
        )
        print(f"{'':>43}{fmt_params(r.params)}")


def analyse_run(run: Run, show_timeline: bool) -> None:
    """Print performance summary, anomalies and (optionally) a playback timeline."""
    closures: list[dict[str, object]] = []
    reason_counts: dict[str, int] = {}
    net_pnl = 0.0
    wins = losses = 0
    rejections = 0
    errors: list[LogLine] = []
    anomalies: list[str] = []
    last_summary: dict[str, object] | None = None
    last_position: dict[str, object] | None = None  # freshest position_updated

    # Anomaly event names worth flagging explicitly.
    anomaly_events = {
        "cycle_error",
        "fast_poll_error",
        "check_limit_fills_error",
        "signal_drain_limit_reached",
        "limit_order_cancelled_or_expired",
        "max_open_trades_reached",
        "order_execution_failed",
        "opposite_market_order_failed",
    }

    for ln in run.lines:
        ev = ln.event
        if ev == "trade_closed_by_broker":
            pnl = _num(ln.raw.get("realized_pnl")) or 0.0
            reason = str(ln.raw.get("close_reason", "?"))
            net_pnl += pnl
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            closures.append(ln.raw)
        elif ev == "session_summary":
            last_summary = ln.raw
        elif ev == "position_updated":
            last_position = ln.raw
        elif ev in ("signal_rejected", "rejected") or ev == "max_open_trades_reached":
            rejections += 1
        is_error = ln.raw.get("level") in ("error", "critical") or "exception" in ev
        if is_error:
            errors.append(ln)

        # Anomaly list excludes error-level lines — those are shown under Errors
        # already, so listing them here too would double-count.
        if ev in anomaly_events and not is_error:
            anomalies.append(f"{ln.ts:%H:%M:%S}  {ev}  {_brief(ln.raw)}")

    # --- Header ---
    status = "RUNNING (no engine_stopped seen)" if run.is_running else "stopped"
    sr = run.start.raw
    print(f"\n=== Run #{run.index} — {run.strategy} [{status}] ===")
    print(f"Started : {run.start.ts:%Y-%m-%d %H:%M:%S} UTC")
    print(f"End     : {run.end_ts:%Y-%m-%d %H:%M:%S} UTC  (duration {run.duration_str})")

    # --- Config ---
    print("\n-- Config --")
    print(f"Symbol      : {sr.get('symbol', '?')}")
    print(
        f"Interval    : {sr.get('interval', '?')}s"
        f"  (fill poll {sr.get('fill_poll_interval', '?')}s)"
    )
    print(f"Strategy    : {run.strategy}")
    print(f"Params      : {fmt_params(run.params)}")
    risk = sr.get("risk_params")
    if isinstance(risk, dict):
        if risk.get("enabled"):
            print(f"Risk engine : ENABLED — {fmt_params(risk)}")
        else:
            print("Risk engine : disabled")

    # --- Account ---
    # initial_equity is logged at start; equity/peak come from the hourly
    # session_summary; the freshest unrealized/realized P&L come from the last
    # position_updated (fires per trade, so more current than the summary).
    init_eq = _num(sr.get("initial_equity"))
    cur_eq = _num(last_summary.get("equity")) if last_summary else None
    peak_eq = _num(last_summary.get("peak_equity")) if last_summary else None
    print("\n-- Account --")
    print(f"Initial balance : {_dollars(init_eq)}")
    if cur_eq is not None:
        delta = (cur_eq - init_eq) if init_eq is not None else None
        delta_str = f"  ({_money(delta)})" if delta is not None else ""
        print(f"Current balance : {_dollars(cur_eq)}{delta_str}   [as of last hourly summary]")
        print(f"Peak balance    : {_dollars(peak_eq)}")
    else:
        print("Current balance : n/a (no hourly summary yet — run < 1h or just started)")
    if last_position is not None:
        print(
            f"Open position   : qty {last_position.get('quantity')}"
            f" @ {last_position.get('avg_cost')}"
            f"  unrealized {_money(_num(last_position.get('unrealized_pnl')) or 0.0)}"
        )

    # --- Performance ---
    closed = len(closures)
    decided = wins + losses
    win_rate = (wins / decided * 100) if decided else 0.0
    print("\n-- Performance --")
    print(f"Closures      : {closed}")
    print(f"Net realized  : {_money(net_pnl)}")
    print(f"Win / loss    : {wins} / {losses}  ({win_rate:.0f}% win rate)")
    if reason_counts:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items()))
        print(f"Close reasons : {breakdown}")

    # --- Anomalies ---
    print("\n-- Anomalies / events of note --")
    if errors:
        print(f"Errors/exceptions: {len(errors)}")
        for e in errors[:5]:
            print(f"  {e.ts:%H:%M:%S}  {e.event}  {_brief(e.raw)}")
    if anomalies:
        for a in anomalies[:20]:
            print(f"  {a}")
        if len(anomalies) > 20:
            print(f"  ... and {len(anomalies) - 20} more")
    if not errors and not anomalies:
        print("  none")

    # Biggest losers — most useful signal when tuning a grid.
    if closures:
        worst = sorted(closures, key=lambda c: _num(c.get("realized_pnl")) or 0.0)[:5]
        print("\n-- Largest losses --")
        for c in worst:
            pnl = _num(c.get("realized_pnl")) or 0.0
            if pnl >= 0:
                break
            print(
                f"  {c.get('grid_level')}  {_money(pnl)}  "
                f"@ {c.get('close_price')}  ({c.get('close_reason')})"
            )

    if show_timeline:
        _print_timeline(run)


def _print_timeline(run: Run) -> None:
    """Chronological playback: each event annotated with prevailing market price."""
    print("\n-- Timeline (price = latest market close at that moment) --")
    last_price: float | None = None
    # Events worth showing in the playback (skip per-cycle bars + debug chatter).
    show = {
        "grid_initialized",
        "signal_generated",
        "trade_executed",
        "limit_order_filled",
        "opposite_market_filled",
        "market_fill",
        "trade_closed_by_broker",
        "level_trimmed",
        "max_open_trades_reached",
        "cycle_error",
        "fast_poll_error",
        "session_summary",
    }
    for ln in run.lines:
        if ln.event == "bars_fetched":
            lp = ln.raw.get("latest_close")
            if isinstance(lp, (int, float)):
                last_price = float(lp)
            continue
        if ln.event not in show:
            continue
        price = f"{last_price:>9.2f}" if last_price is not None else "    --   "
        print(f"  {ln.ts:%m-%d %H:%M:%S}  {price}  {ln.event:<24} {_brief(ln.raw)}")


def _brief(raw: dict[str, object]) -> str:
    """Compact one-line view of the fields that matter per event type."""
    ev = raw.get("event")
    if ev == "trade_closed_by_broker":
        return (
            f"{raw.get('grid_level')} {_money(_num(raw.get('realized_pnl')) or 0.0)} "
            f"@ {raw.get('close_price')} ({raw.get('close_reason')})"
        )
    if ev in ("trade_executed", "opposite_market_filled", "market_fill"):
        return (
            f"{raw.get('side', '')} {raw.get('quantity', '')} @ "
            f"{raw.get('price') or raw.get('fill_price')} "
            f"(#{raw.get('broker_trade_id', '')})"
        )
    if ev == "limit_order_filled":
        return (
            f"{raw.get('side', '')} @ {raw.get('fill_price')} "
            f"({raw.get('grid_level')})"
        )
    if ev == "signal_generated":
        return (
            f"{raw.get('signal_type', '')} {raw.get('order_type', '')} "
            f"@ {raw.get('trigger_price')} SL={raw.get('stop_loss')}"
        )
    if ev == "level_trimmed":
        return str(raw.get("grid_level", ""))
    if ev == "grid_initialized":
        return f"anchor {raw.get('anchor_price')}"
    if ev == "session_summary":
        return (
            f"cycles={raw.get('cycles')} trades={raw.get('trades')} "
            f"equity=${raw.get('equity')}"
        )
    # Fallback: show a few non-bookkeeping keys.
    skip = {"event", "level", "logger", "timestamp", "user_id", "user_email"}
    parts = [f"{k}={v}" for k, v in raw.items() if k not in skip]
    return " ".join(parts[:6])


def _money(value: float) -> str:
    return f"+${value:.2f}" if value >= 0 else f"-${abs(value):.2f}"


def _num(value: object) -> float | None:
    """Coerce a logged value to float, or None if absent/unparseable."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _dollars(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def resolve_identity(args: argparse.Namespace) -> str:
    """Get the user_id from --user-id, --email lookup, or analysis.local.json."""
    if args.user_id:
        return str(args.user_id)
    if LOCAL_CONFIG.exists():
        cfg = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
        uid = cfg.get("user_id")
        if uid:
            return str(uid)
    sys.exit(
        "No identity. Create analysis.local.json with {\"user_id\": \"...\"} "
        "(gitignored) or pass --user-id. Never hardcode this in a tracked file."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    ap.add_argument("--user-id", help="Override identity (else read analysis.local.json)")
    ap.add_argument("--list", action="store_true", help="List runs and exit")
    ap.add_argument("--run", type=int, help="Run index to analyse (default: latest)")
    ap.add_argument("--timeline", action="store_true", help="Include event playback")
    args = ap.parse_args()

    user_id = resolve_identity(args)
    lines = load_lines(args.log_dir, user_id)
    runs = segment_runs(lines)

    if args.list or not runs:
        print_run_list(runs, lines)
        return

    if args.run is not None:
        match = [r for r in runs if r.index == args.run]
        if not match:
            sys.exit(f"Run #{args.run} not found. Use --list to see available runs.")
        target = match[0]
    else:
        target = runs[-1]  # latest

    print_run_list(runs, lines)
    analyse_run(target, show_timeline=args.timeline)


if __name__ == "__main__":
    main()
