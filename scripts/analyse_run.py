#!/usr/bin/env python3
"""Analyse a production trading run from the JSON logs — session-aware playback.

Reads the rotated structlog JSON logs pulled into ``logs/prod/`` (see
``just pull-logs``), filters to a single user, groups the stream into runs by the
``run_id`` bound onto every log line, and reports performance, anomalies and a
price-annotated event timeline for one chosen run. Within a run, activity is broken
down by ``session_seq`` (one grid lifecycle: anchor → close-all → re-anchor).

Lines without a ``run_id`` (pre-instrumentation logs from before run identity was
added) are skipped — the analyser reports the skipped count so a stale pull is
obvious rather than silently empty.

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

    @property
    def run_id(self) -> str:
        return str(self.raw.get("run_id", ""))

    @property
    def session_seq(self) -> int | None:
        s = self.raw.get("session_seq")
        return s if isinstance(s, int) else None


@dataclass
class Run:
    """A single bot run, identified by run_id.

    All lines sharing a run_id form one run. ``start``/``stop`` are the
    ``engine_started``/``engine_stopped`` lines within the group, either of which may
    be absent if it rotated out of the log window (start) or the run is still active
    (stop). Config is sourced from ``engine_started`` when present, else from the
    latest ``session_summary`` (which re-emits config) so it survives rotation.
    """

    run_id: str
    index: int
    lines: list[LogLine] = field(default_factory=list)
    start: LogLine | None = None
    stop: LogLine | None = None

    @property
    def _config_raw(self) -> dict[str, object]:
        """Config fields, from engine_started or the latest session_summary."""
        if self.start is not None:
            return self.start.raw
        for ln in reversed(self.lines):
            if ln.event == "session_summary":
                return ln.raw
        return {}

    @property
    def strategy(self) -> str:
        # strategy is bound onto every line via contextvars — use any line.
        if self.lines:
            return str(self.lines[0].raw.get("strategy", "?"))
        return str(self._config_raw.get("strategy", "?"))

    @property
    def params(self) -> dict[str, object]:
        p = self._config_raw.get("strategy_params")
        return p if isinstance(p, dict) else {}

    @property
    def is_running(self) -> bool:
        return self.stop is None

    @property
    def start_ts(self) -> datetime:
        if self.start is not None:
            return self.start.ts
        return self.lines[0].ts if self.lines else self.end_ts

    @property
    def end_ts(self) -> datetime:
        if self.stop is not None:
            return self.stop.ts
        return self.lines[-1].ts if self.lines else self.start_ts

    @property
    def duration_str(self) -> str:
        secs = (self.end_ts - self.start_ts).total_seconds()
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
    """Read all rotated log files, filter to one user, drop noise, sort by time.

    Lines without a ``run_id`` are skipped (pre-instrumentation logs); the count is
    reported on stderr so a stale pull surfaces clearly instead of an empty result.
    """
    files = sorted(log_dir.glob("aurex_trade.log*"))
    if not files:
        sys.exit(
            f"No logs found in {log_dir}. Run `just pull-logs` first to fetch them."
        )

    lines: list[LogLine] = []
    skipped_no_run_id = 0
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
                if not rec.get("run_id"):
                    skipped_no_run_id += 1
                    continue
                lines.append(LogLine(ts=ts, event=event, raw=rec))

    if skipped_no_run_id:
        print(
            f"Note: skipped {skipped_no_run_id} pre-instrumentation log line(s) with "
            f"no run_id. If results look empty, the pull may predate run tracking.",
            file=sys.stderr,
        )

    lines.sort(key=lambda x: x.ts)
    return lines


def segment_runs(lines: list[LogLine]) -> list[Run]:
    """Group the line stream into runs by run_id.

    Every line carries a run_id (lines without one are dropped in load_lines), so a
    run is simply all lines sharing a run_id. Runs are ordered by first-seen time;
    ``index`` is a 1-based, human-friendly handle for ``--run N``.
    """
    by_run: dict[str, Run] = {}
    order: list[str] = []
    for ln in lines:
        run = by_run.get(ln.run_id)
        if run is None:
            run = Run(run_id=ln.run_id, index=0)
            by_run[ln.run_id] = run
            order.append(ln.run_id)
        run.lines.append(ln)
        if ln.event == _START:
            run.start = ln
        elif ln.event == _STOP:
            run.stop = ln

    runs = [by_run[rid] for rid in order]
    # Order by actual start time, then assign 1-based indices.
    runs.sort(key=lambda r: r.start_ts)
    for i, run in enumerate(runs, start=1):
        run.index = i
    return runs


def fmt_params(params: dict[str, object]) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items())


def run_nutshell(run: Run) -> dict[str, object]:
    """Compact one-record summary of a run — the quick entry point (also --list --json)."""
    st = compute_stats(run)
    return {
        "index": run.index,
        "run_id": run.run_id,
        "start": run.start_ts.isoformat(),
        "end": None if run.is_running else run.end_ts.isoformat(),
        "status": "running" if run.is_running else "stopped",
        "strategy": run.strategy,
        "symbol": run._config_raw.get("symbol", "?"),
        "net_pnl": round(st.net_pnl, 2),
        "closures": len(st.closures),
        "sessions": len(st.sessions),
        "win_rate": round(st.win_rate, 1),
    }


def print_run_list(runs: list[Run], lines: list[LogLine], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([run_nutshell(r) for r in runs], indent=2))
        return
    if lines:
        print(
            f"Log window: {lines[0].ts:%Y-%m-%d %H:%M} → {lines[-1].ts:%Y-%m-%d %H:%M} UTC"
            f"  ({len(lines)} events for this user)\n"
        )
    if not runs:
        print("No runs found for this user in the log window.")
        return
    print(
        f"{'#':>2}  {'run_id':<8}  {'Start (UTC)':<17}  {'Dur':>7}  "
        f"{'Status':<8}  {'Net P&L':>9}  Strategy / params"
    )
    print("-" * 100)
    for r in runs:
        status = "RUNNING" if r.is_running else "stopped"
        st = compute_stats(r)
        print(
            f"{r.index:>2}  {r.run_id[:8]:<8}  {r.start_ts:%Y-%m-%d %H:%M}  "
            f"{r.duration_str:>7}  {status:<8}  {_money(st.net_pnl):>9}  {r.strategy}"
        )
        print(f"{'':>55}{fmt_params(r.params)}")


# Anomaly event names worth flagging explicitly.
_ANOMALY_EVENTS = {
    "cycle_error",
    "fast_poll_error",
    "check_limit_fills_error",
    "signal_drain_limit_reached",
    "limit_order_cancelled_or_expired",
    "max_open_trades_reached",
    "order_execution_failed",
    "opposite_market_order_failed",
}


@dataclass
class SessionStats:
    """Per-grid-lifecycle (session_seq) aggregates within a run."""

    seq: int
    closures: int = 0
    net_pnl: float = 0.0
    wins: int = 0
    losses: int = 0


@dataclass
class RunStats:
    """Aggregates for one run, shared by the list view and the detail view."""

    closures: list[dict[str, object]] = field(default_factory=list)
    reason_counts: dict[str, int] = field(default_factory=dict)
    net_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    rejections: int = 0
    errors: list[LogLine] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    last_summary: dict[str, object] | None = None
    last_position: dict[str, object] | None = None
    sessions: dict[int, SessionStats] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return (self.wins / self.decided * 100) if self.decided else 0.0


def compute_stats(run: Run) -> RunStats:
    """Walk a run's lines once and compute all aggregates (run- and session-level)."""
    st = RunStats()
    for ln in run.lines:
        ev = ln.event
        if ev == "trade_closed_by_broker":
            pnl = _num(ln.raw.get("realized_pnl")) or 0.0
            reason = str(ln.raw.get("close_reason", "?"))
            st.net_pnl += pnl
            st.reason_counts[reason] = st.reason_counts.get(reason, 0) + 1
            won = pnl > 0
            lost = pnl < 0
            if won:
                st.wins += 1
            elif lost:
                st.losses += 1
            st.closures.append(ln.raw)
            # Per-session bucket (session_seq is bound onto the line).
            seq = ln.session_seq
            if seq is not None:
                sess = st.sessions.get(seq)
                if sess is None:
                    sess = SessionStats(seq=seq)
                    st.sessions[seq] = sess
                sess.closures += 1
                sess.net_pnl += pnl
                sess.wins += int(won)
                sess.losses += int(lost)
        elif ev == "session_summary":
            st.last_summary = ln.raw
        elif ev == "position_updated":
            st.last_position = ln.raw
        elif ev in ("signal_rejected", "rejected") or ev == "max_open_trades_reached":
            st.rejections += 1
        is_error = ln.raw.get("level") in ("error", "critical") or "exception" in ev
        if is_error:
            st.errors.append(ln)
        # Anomaly list excludes error-level lines — those are shown under Errors
        # already, so listing them here too would double-count.
        if ev in _ANOMALY_EVENTS and not is_error:
            st.anomalies.append(f"{ln.ts:%H:%M:%S}  {ev}  {_brief(ln.raw)}")
    return st


def analyse_run(run: Run, show_timeline: bool) -> None:
    """Print performance summary, anomalies and (optionally) a playback timeline."""
    st = compute_stats(run)
    cfg = run._config_raw

    # --- Header ---
    status = "RUNNING (no engine_stopped seen)" if run.is_running else "stopped"
    print(f"\n=== Run #{run.index} ({run.run_id[:8]}) — {run.strategy} [{status}] ===")
    print(f"Started : {run.start_ts:%Y-%m-%d %H:%M:%S} UTC")
    print(f"End     : {run.end_ts:%Y-%m-%d %H:%M:%S} UTC  (duration {run.duration_str})")

    # --- Config ---
    print("\n-- Config --")
    print(f"Symbol      : {cfg.get('symbol', '?')}")
    print(
        f"Interval    : {cfg.get('interval', '?')}s"
        f"  (fill poll {cfg.get('fill_poll_interval', '?')}s)"
    )
    print(f"Strategy    : {run.strategy}")
    print(f"Params      : {fmt_params(run.params)}")
    risk = cfg.get("risk_params")
    if isinstance(risk, dict):
        if risk.get("enabled"):
            print(f"Risk engine : ENABLED — {fmt_params(risk)}")
        else:
            print("Risk engine : disabled")

    # --- Account ---
    # initial_equity is logged at start; equity/peak come from the hourly
    # session_summary; the freshest unrealized/realized P&L come from the last
    # position_updated (fires per trade, so more current than the summary).
    last_summary = st.last_summary
    init_eq = _num(cfg.get("initial_equity"))
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
    if st.last_position is not None:
        lp = st.last_position
        print(
            f"Open position   : qty {lp.get('quantity')}"
            f" @ {lp.get('avg_cost')}"
            f"  unrealized {_money(_num(lp.get('unrealized_pnl')) or 0.0)}"
        )

    # --- Performance ---
    print("\n-- Performance --")
    print(f"Closures      : {len(st.closures)}")
    print(f"Net realized  : {_money(st.net_pnl)}")
    print(f"Win / loss    : {st.wins} / {st.losses}  ({st.win_rate:.0f}% win rate)")
    if st.reason_counts:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(st.reason_counts.items()))
        print(f"Close reasons : {breakdown}")

    # --- Sessions (per grid lifecycle) ---
    # The sliding grid re-anchors on each close-all; P&L per session is the breakdown
    # to tune against (each session is one grid at one anchor price).
    if st.sessions:
        print("\n-- Sessions (per grid lifecycle) --")
        for seq in sorted(st.sessions):
            s = st.sessions[seq]
            print(
                f"  session {seq:>2}: closures={s.closures:>3}  net={_money(s.net_pnl)}"
                f"  W/L {s.wins}/{s.losses}"
            )

    closures = st.closures
    errors = st.errors
    anomalies = st.anomalies

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
        seq = ln.session_seq
        sess = f"s{seq}" if seq is not None else "s-"
        print(
            f"  {ln.ts:%m-%d %H:%M:%S}  {sess:>3}  {price}  "
            f"{ln.event:<24} {_brief(ln.raw)}"
        )


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
    # Fallback: show a few non-bookkeeping keys. The bound-context fields (run_id,
    # strategy, session_seq) are on every line — skip them to keep the view focused.
    skip = {
        "event", "level", "logger", "timestamp", "user_id", "user_email",
        "run_id", "strategy", "session_seq",
    }
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


def _resolve_run(runs: list[Run], selector: str) -> Run:
    """Resolve --run: an integer index (1-based) or a run_id prefix."""
    if selector.isdigit():
        idx = int(selector)
        match = [r for r in runs if r.index == idx]
        if match:
            return match[0]
        sys.exit(f"Run #{idx} not found. Use --list to see available runs.")
    # Treat as a run_id prefix.
    match = [r for r in runs if r.run_id.startswith(selector)]
    if len(match) == 1:
        return match[0]
    if not match:
        sys.exit(f"No run with id prefix '{selector}'. Use --list to see runs.")
    sys.exit(f"Ambiguous run id prefix '{selector}' ({len(match)} matches).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    ap.add_argument("--user-id", help="Override identity (else read analysis.local.json)")
    ap.add_argument("--list", action="store_true", help="List runs and exit")
    ap.add_argument(
        "--json",
        action="store_true",
        help="With --list, emit the run index as JSON (machine-readable nutshell)",
    )
    ap.add_argument(
        "--run",
        help="Run to analyse: 1-based index or run_id prefix (default: latest)",
    )
    ap.add_argument("--timeline", action="store_true", help="Include event playback")
    args = ap.parse_args()

    user_id = resolve_identity(args)
    lines = load_lines(args.log_dir, user_id)
    runs = segment_runs(lines)

    if args.list or not runs:
        print_run_list(runs, lines, as_json=args.json)
        return

    target = _resolve_run(runs, args.run) if args.run is not None else runs[-1]

    print_run_list(runs, lines)
    analyse_run(target, show_timeline=args.timeline)


if __name__ == "__main__":
    main()
