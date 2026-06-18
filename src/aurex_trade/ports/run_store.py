"""Run store port — contract for the durable per-run history rollup.

A *run* is one engine lifecycle (engine_started → engine_stopped). The structured
JSON log is the complete, event-sourced record of a run, but it rotates out of the
retention window. This port persists a compact per-run SUMMARY (config + outcome +
net P&L) that survives rotation, so historical runs stay queryable.

It is NOT a replacement for the event log: it stores one row per run, not individual
fills. The log analyser remains the authoritative event-sourced view; this rollup is
the durable summary. The two should agree on net realized P&L for a given run_id.

All methods require a user_id for multi-tenant isolation.
"""

from datetime import datetime
from typing import Protocol


class RunStorePort(Protocol):
    """Port for persisting and retrieving per-run summaries."""

    def start_run(
        self,
        run_id: str,
        *,
        user_id: str,
        strategy: str,
        symbol: str,
        interval: int,
        strategy_params: dict[str, int | float],
        risk_params: dict[str, int | float | bool],
        started_at: datetime,
        initial_equity: float,
    ) -> None:
        """Record the start of a run with status='running'.

        A run that never reaches finish_run (crash/kill) is left as 'running' —
        itself diagnostic, mirroring "absence of engine_stopped ⇒ still running".
        """
        ...

    def finish_run(
        self,
        run_id: str,
        *,
        user_id: str,
        ended_at: datetime,
        stop_reason: str,
        total_cycles: int,
        sessions: int,
        closures: int,
        net_realized_pnl: float,
        final_equity: float,
    ) -> None:
        """Mark a run finished (status='stopped') and record its outcome."""
        ...
