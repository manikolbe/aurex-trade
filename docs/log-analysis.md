# Log Analysis — How To Analyse a Production Bot Run

*Repo-only / operator doc. This is a CLI + log workflow; it is intentionally NOT in
`docs/user/` (which must never reference logs, CLI, or operator concerns).*

This is the practical guide to answering "what did the bot actually do, and how did it
perform?" from production logs. If you only read one thing: pull the logs, list the
runs, drill into one.

```bash
just pull-logs              # fetch prod logs → logs/prod/ (gitignored)
just analyse --list         # quick nutshell of every run (run_id, net P&L)
just analyse --run 2        # full report for one run (by index or run_id prefix)
just analyse --run 2 --timeline   # + price-annotated event playback
```

## The model: identity hierarchy

Logs are an **event-sourced** record — every meaningful thing the engine does is one
JSON line. Each engine log line carries four **bound context** fields (set once via
structlog contextvars, merged onto every line):

```
user_id   →  who owns the bot (prod is multi-user)
  run_id    →  one engine lifecycle: engine_started → engine_stopped
    session_seq →  one grid lifecycle: anchor → close-all → re-anchor
      (trades)    →  fills / closures within that grid
```

- A **run** is one process lifetime of the engine. `run_id` is a uuid minted in
  `run()`.
- A **session** is one grid. The sliding grid re-anchors after a `session_profit_target`
  / `session_loss_limit` close-all; each re-anchor bumps `session_seq` (1, 2, 3 …).
  This is the breakdown you tune against — each session is one grid at one anchor price.

### Why logs, not the DB, for detail

The engine persists MARKET orders to SQLite, but **individual trade closures with
realized P&L are never written to the DB** (the `trades` table has no realized_pnl
column). The JSON log is the only complete record of fills and closures. The web UI's
equity/session charts are in-memory and lost on every restart.

There *is* a durable DB summary — see [Durable run history](#durable-run-history-bot_runs) —
but it is a per-run rollup, not the event log.

### Realized P&L comes from the account-balance delta

OANDA's transaction/trade **history** endpoints (`/transactions*`, `/trades/{id}`,
`/trades?state=CLOSED`) time out with **HTTP 504** once an account has a large
history (~10k+ transactions). So the engine never looks up per-trade realized P&L.
Instead it reads the account **balance** (`/summary`, always fast — balance moves
only when P&L is realized) and derives realized P&L from deltas:

- `engine_started` logs `initial_balance`; `engine_stopped` and `session_summary`
  log the running `balance` and `run_realized` (= balance − initial_balance).
- `close_all_executed` logs `balance` and `session_realized` (that grid lifecycle's
  realized P&L = balance delta since the session started) — **authoritative**.
- `trade_closed_by_broker` (broker-side stop-loss) carries `close_reason: close_sl`
  and `close_price` (the prevailing market price), but `realized_pnl: null` — the
  banked amount is reflected in the balance, not the per-trade event.

The analyser nets realized P&L from the balance delta, falling back to the sum of
`session_realized` deltas, then (for pre-fix runs that logged per-closure P&L) to
summing `trade_closed_by_broker.realized_pnl`. The chosen source is labelled in the
**Performance** block.

> Historical note: before this change the engine looked up each closure via the
> transactions endpoint. On long-lived accounts that 504'd, the exception was
> swallowed, and every closure was recorded as `$0` — so the session profit-target
> fired on unrealized P&L alone while realized stop-loss losses were invisible. The
> balance-delta model removes that whole failure mode.

## Workflow

### 1. Pull the logs

```bash
just pull-logs
```

Copies `/app/logs/aurex_trade.log*` from the prod container into `logs/prod/`
(gitignored — the logs contain emails, OANDA account ids, OAuth user ids). Reads all
rotated files (`.log`, `.log.1`, …); the window is ~110 MB (10 MB × 10 files).

### 2. List runs (the quick entry point)

```bash
just analyse --list
just analyse --list --json   # machine-readable
```

One glanceable record per run: index, `run_id`, start, duration, status, net P&L,
strategy. Use this to find the run you care about, then drill in. `--json` emits the
same as an array (handy for scripting or diffing).

### 3. Analyse one run

```bash
just analyse --run 2            # by 1-based index from --list
just analyse --run aaaa1111     # …or by run_id prefix
just analyse                    # no --run ⇒ latest run
```

Report blocks:

| Block | What it tells you |
|-------|-------------------|
| **Config** | strategy, params, risk settings, symbol, interval |
| **Account** | initial / current / peak balance (current as of last hourly summary) |
| **Performance** | closures, net realized P&L, win/loss, close-reason breakdown |
| **Sessions** | **per-grid-lifecycle P&L** — the sliding-grid tuning signal |
| **Anomalies** | errors/exceptions and notable events (rejections, failed orders) |
| **Largest losses** | worst closures — where a grid bled |

### 4. Timeline playback

```bash
just analyse --run 2 --timeline
```

Chronological replay. Each line is prefixed with its `session_seq` (`s1`, `s2`, …) and
annotated with the prevailing market price (`bars_fetched.latest_close` carried
forward), so you can see exactly what happened against price and grid state.

## Reading per-session P&L (sliding grid)

The `-- Sessions --` block is the one that matters for tuning. Example:

```
-- Sessions (per grid lifecycle) --
  session  1: closures=  2  net=+$4.50   W/L 1/1
  session  2: closures=  1  net=+$20.00  W/L 1/0
```

Each session is a fresh grid at a new anchor. A run that nets +$24.50 overall might be
one good session masking a bad one — the per-session split shows which anchors/regimes
the grid handled well. See the `project_sliding_grid_walkforward` findings for context
on regime dependence.

## Raw grep recipes

Every line carries the bound fields, so plain `grep` slices cleanly:

```bash
# Everything one run logged:
grep '"run_id": "<run_id>"' logs/prod/aurex_trade.log*

# One grid lifecycle within a run:
grep '"run_id": "<run_id>"' logs/prod/* | grep '"session_seq": 2'

# Per-session realized P&L (balance delta) at each close-all:
grep '"event": "close_all_executed"' logs/prod/*

# Closure events (broker-side stop-loss; realized_pnl is null — see balance delta):
grep '"event": "trade_closed_by_broker"' logs/prod/*

# Errors only:
grep -E '"level": "(error|critical)"|exception' logs/prod/*
```

## Config availability

Run config is logged on `engine_started`. For a long run whose `engine_started` has
rotated out of the window, the analyser falls back to the latest `session_summary`
(which re-emits `strategy_params`, `risk_params`, `symbol`, `interval`). So the Config
block renders real values for any run with activity still in-window.

## Durable run history (`bot_runs`)

`bot_runs` (SQLite, user-scoped) holds one summary row per run: config, runtime,
status, sessions, closures, net P&L. Written `status='running'` on `engine_started`
and finalized `status='stopped'` on `engine_stopped`. `net_realized_pnl` is the run's
**account-balance delta** (`final_balance − initial_balance`) — the same broker-truth
figure the analyser reports, so the two agree by construction.

- A row stuck at `'running'` with no recent log = a **crashed** run (it never reached
  finish). This is intentional and diagnostic — mirrors "absence of `engine_stopped`
  ⇒ still running" in the logs.
- It is a **rollup, not an event log**. The analyser stays authoritative; the two
  should agree on net P&L for a given `run_id`.
- True *independent* validation (against OANDA's own transaction history) is future
  work — the DB rollup shares the engine's code path, so it cross-checks consistency,
  not correctness.

## Identity & PII (PUBLIC REPO — STRICT)

This repo is public. The tooling carries **no identifiers**:

- User identity (`user_id`, email) is read at runtime from `analysis.local.json`
  (gitignored via `analysis.local.*`) or a `--user-id` flag. Never hardcode it.
- Pulled logs in `logs/prod/` are gitignored. The DB is gitignored.
- Before committing, scan for leaks:
  `git grep -nI "<your-email>|<account-id>|<oauth-user-id>"`.

## Gotchas

- **New-format only.** The analyser groups by `run_id` and **skips lines without one**
  (pre-instrumentation logs), reporting the skipped count. If a run looks empty, the
  pull may predate run-identity tracking — clear `logs/prod/` and re-pull.
- **Skipped-line notice ≠ error.** It just means some old lines had no `run_id`.
- **Rotation window.** Very old runs fall out of the 110 MB log window; use `bot_runs`
  for their summary.
