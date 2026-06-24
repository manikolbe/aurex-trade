"""Unit tests for scripts/analyse_run.py — the production log analyser.

Proves the analysis works end-to-end on synthetic JSON logs: grouping by run_id,
per-session P&L bucketing, skipping pre-instrumentation (no run_id) lines, config
fallback to session_summary when engine_started has rotated out, and the
--list --json nutshell index.

scripts/ is not an importable package, so the module is loaded by file path.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "analyse_run.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analyse_run", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve the module by __module__.
    sys.modules["analyse_run"] = mod
    spec.loader.exec_module(mod)
    return mod


ar = _load_module()


def _line(**kw: object) -> str:
    base: dict[str, object] = {"user_id": "u1", "logger": "aurex_trade.engine"}
    base.update(kw)
    return json.dumps(base)


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    (tmp_path / "aurex_trade.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _two_run_log(tmp_path: Path) -> Path:
    lines = [
        # Run A: complete, two grid sessions.
        _line(timestamp="2026-06-18T10:00:00Z", event="engine_started", run_id="aaaa",
              strategy="ciby_sliding_grid", symbol="XAU_USD", interval=60,
              initial_equity=10000, strategy_params={"grid_spacing": 15},
              risk_params={"enabled": False}),
        _line(timestamp="2026-06-18T10:00:06Z", event="grid_initialized", run_id="aaaa",
              strategy="ciby_sliding_grid", session_seq=1, anchor_price=2300.0),
        _line(timestamp="2026-06-18T10:05:00Z", event="trade_closed_by_broker",
              run_id="aaaa", strategy="ciby_sliding_grid", session_seq=1,
              realized_pnl=12.5, close_reason="close_tp", close_price=2312.0,
              grid_level="L1"),
        _line(timestamp="2026-06-18T10:06:00Z", event="trade_closed_by_broker",
              run_id="aaaa", strategy="ciby_sliding_grid", session_seq=1,
              realized_pnl=-8.0, close_reason="close_sl", close_price=2292.0,
              grid_level="L2"),
        _line(timestamp="2026-06-18T10:10:00Z", event="grid_initialized", run_id="aaaa",
              strategy="ciby_sliding_grid", session_seq=2, anchor_price=2310.0),
        _line(timestamp="2026-06-18T10:15:00Z", event="trade_closed_by_broker",
              run_id="aaaa", strategy="ciby_sliding_grid", session_seq=2,
              realized_pnl=20.0, close_reason="close_tp", close_price=2330.0,
              grid_level="L1"),
        _line(timestamp="2026-06-18T10:20:00Z", event="engine_stopped", run_id="aaaa",
              strategy="ciby_sliding_grid", total_cycles=20),
        # Run B: engine_started rotated out — config only via session_summary.
        _line(timestamp="2026-06-18T11:00:06Z", event="grid_initialized", run_id="bbbb",
              strategy="ciby_sliding_grid", session_seq=1, anchor_price=2350.0),
        _line(timestamp="2026-06-18T11:01:00Z", event="session_summary", run_id="bbbb",
              strategy="ciby_sliding_grid", cycles=60, trades=3, equity=10050,
              peak_equity=10080, symbol="XAU_USD", interval=60,
              strategy_params={"grid_spacing": 15},
              risk_params={"enabled": True, "max_daily_loss": 200}),
        _line(timestamp="2026-06-18T11:05:00Z", event="trade_closed_by_broker",
              run_id="bbbb", strategy="ciby_sliding_grid", session_seq=1,
              realized_pnl=-30.0, close_reason="close_sl", close_price=2320.0,
              grid_level="L3"),
        # Pre-instrumentation line: no run_id → must be skipped.
        json.dumps({"timestamp": "2026-06-18T09:00:00Z", "event": "engine_started",
                    "user_id": "u1", "strategy": "old"}),
    ]
    return _write_log(tmp_path, lines)


def test_groups_by_run_id_and_skips_no_run_id(tmp_path: Path) -> None:
    log_dir = _two_run_log(tmp_path)
    lines = ar.load_lines(log_dir, "u1")
    # The no-run_id line is dropped.
    assert all(ln.run_id for ln in lines)

    runs = ar.segment_runs(lines)
    assert [r.run_id for r in runs] == ["aaaa", "bbbb"]
    assert [r.index for r in runs] == [1, 2]


def test_per_run_and_per_session_pnl(tmp_path: Path) -> None:
    log_dir = _two_run_log(tmp_path)
    runs = ar.segment_runs(ar.load_lines(log_dir, "u1"))
    run_a = runs[0]

    st = ar.compute_stats(run_a)
    assert st.net_pnl == pytest.approx(24.5)
    assert st.wins == 2
    assert st.losses == 1
    # Per-session breakdown.
    assert set(st.sessions) == {1, 2}
    assert st.sessions[1].net_pnl == pytest.approx(4.5)
    assert st.sessions[1].closures == 2
    assert st.sessions[2].net_pnl == pytest.approx(20.0)
    assert st.sessions[2].closures == 1


def test_config_fallback_to_session_summary(tmp_path: Path) -> None:
    """Run B has no engine_started; config must come from session_summary."""
    log_dir = _two_run_log(tmp_path)
    runs = ar.segment_runs(ar.load_lines(log_dir, "u1"))
    run_b = runs[1]

    assert run_b.start is None  # engine_started rotated out
    assert run_b.is_running     # no engine_stopped
    assert run_b.strategy == "ciby_sliding_grid"
    assert run_b.params == {"grid_spacing": 15}
    cfg = run_b._config_raw
    assert cfg.get("symbol") == "XAU_USD"
    assert isinstance(cfg.get("risk_params"), dict)


def test_list_json_nutshell(tmp_path: Path) -> None:
    log_dir = _two_run_log(tmp_path)
    runs = ar.segment_runs(ar.load_lines(log_dir, "u1"))

    nut_a = ar.run_nutshell(runs[0])
    assert nut_a["run_id"] == "aaaa"
    assert nut_a["status"] == "stopped"
    assert nut_a["net_pnl"] == pytest.approx(24.5)
    assert nut_a["sessions"] == 2
    assert nut_a["closures"] == 3

    nut_b = ar.run_nutshell(runs[1])
    assert nut_b["status"] == "running"
    assert nut_b["end"] is None
    assert nut_b["net_pnl"] == pytest.approx(-30.0)


def _balance_delta_log(tmp_path: Path) -> Path:
    """A post-fix run: engine_started carries initial_balance; close_all_executed
    carries the authoritative per-session balance delta + running balance.
    """
    lines = [
        _line(timestamp="2026-06-24T08:00:00Z", event="engine_started", run_id="cccc",
              strategy="ciby_sliding_grid", symbol="XAU_USD", interval=60,
              initial_equity=100000, initial_balance=100000,
              strategy_params={"grid_spacing": 10}, risk_params={"enabled": False}),
        _line(timestamp="2026-06-24T08:00:06Z", event="grid_initialized", run_id="cccc",
              strategy="ciby_sliding_grid", session_seq=1, anchor_price=4000.0),
        # Session 1 closes-all with a realized loss of -15 → balance 99985.
        _line(timestamp="2026-06-24T08:30:00Z", event="close_all_executed", run_id="cccc",
              strategy="ciby_sliding_grid", session_seq=1, reason="session_profit_target",
              trades_closed=3, balance=99985.0, session_realized=-15.0),
        _line(timestamp="2026-06-24T08:30:06Z", event="grid_initialized", run_id="cccc",
              strategy="ciby_sliding_grid", session_seq=2, anchor_price=3990.0),
        # Session 2 closes-all with -20 → balance 99965.
        _line(timestamp="2026-06-24T09:00:00Z", event="close_all_executed", run_id="cccc",
              strategy="ciby_sliding_grid", session_seq=2, reason="session_profit_target",
              trades_closed=2, balance=99965.0, session_realized=-20.0),
        _line(timestamp="2026-06-24T09:05:00Z", event="engine_stopped", run_id="cccc",
              strategy="ciby_sliding_grid", total_cycles=65, balance=99965.0,
              run_realized=-35.0),
    ]
    return _write_log(tmp_path, lines)


def test_net_realized_from_balance_delta(tmp_path: Path) -> None:
    """Net realized P&L is the account-balance delta, not summed per-closure P&L."""
    log_dir = _balance_delta_log(tmp_path)
    run = ar.segment_runs(ar.load_lines(log_dir, "u1"))[0]
    st = ar.compute_stats(run)

    # initial_balance 100000 → last balance 99965 = -35 (the real banked result).
    assert st.net_realized_balance == pytest.approx(-35.0)
    assert st.net_best == pytest.approx(-35.0)
    # Sum of per-session deltas agrees.
    assert st.sum_session_realized == pytest.approx(-35.0)
    # Per-session authoritative deltas are surfaced.
    assert st.sessions[1].realized_balance == pytest.approx(-15.0)
    assert st.sessions[2].realized_balance == pytest.approx(-20.0)
    # The nutshell uses the balance-delta net.
    assert ar.run_nutshell(run)["net_pnl"] == pytest.approx(-35.0)


def test_legacy_run_without_balance_falls_back(tmp_path: Path) -> None:
    """Pre-fix runs (no balance logged) still net via summed per-closure P&L."""
    log_dir = _two_run_log(tmp_path)
    run_a = ar.segment_runs(ar.load_lines(log_dir, "u1"))[0]
    st = ar.compute_stats(run_a)
    assert st.net_realized_balance is None
    assert st.net_best == pytest.approx(st.net_pnl) == pytest.approx(24.5)


def test_resolve_run_by_index_and_prefix(tmp_path: Path) -> None:
    log_dir = _two_run_log(tmp_path)
    runs = ar.segment_runs(ar.load_lines(log_dir, "u1"))

    assert ar._resolve_run(runs, "1").run_id == "aaaa"
    assert ar._resolve_run(runs, "bb").run_id == "bbbb"

    with pytest.raises(SystemExit):
        ar._resolve_run(runs, "zzzz")
