# AurexTrade — Strategies

## Strategy Protocol

Every strategy must satisfy the `Strategy` Protocol defined in
`src/aurex_trade/domain/strategy/base.py`:

```python
class Strategy(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def min_bars(self) -> int: ...

    def generate(self, bars: list[BarData]) -> Signal | None: ...

    @classmethod
    def metadata(cls) -> StrategyMetadata: ...
```

**Key rules:**
- Strategies live in `src/aurex_trade/domain/strategy/`
- They import ONLY from stdlib and `aurex_trade.domain` (hexagonal boundary)
- They are pure functions: bars in, signal out, no side effects

## Registered strategies

Two strategies are registered, both stateful grids:

- **Ciby Sliding Grid** (`ciby_sliding_grid`) — the primary, live strategy.
- **Ciby Hedged Doubling Grid** (`ciby_hedged_doubling_grid`) — experimental.

Grid strategies extend the base `Strategy` Protocol with callbacks the backtest
runner and live engine drive on fills/closures:

```python
def report_fill(self, grid_level_key: str, fill_price: float) -> None: ...
def report_trade_closed(self, grid_level_key: str, realized_pnl: float) -> None: ...
def notify_close_all_complete(self) -> None: ...
def update_unrealized_pnl(self, unrealized_pnl: float) -> None: ...
```

The backtest runner auto-detects grid strategies via `hasattr(strategy, "report_fill")`
and switches to grid orchestration mode (signal drain loop, limit order simulation,
stop-loss enforcement, strategy callbacks). The risk engine is **disabled** for grid
backtests — each strategy manages its own risk via session/daily loss limits.

## Ciby Sliding Grid

**File:** `src/aurex_trade/domain/strategy/ciby_sliding_grid.py`

The primary strategy. A stateful grid that places hedged pairs (buy + sell) around
an anchor, then *slides* the active band as price trends — keeping a bounded number
of levels ahead of and behind the market. Each leg carries a stop just past the next
level; session profit-target / loss-limit / daily-loss-limit trigger a close-all and
re-anchor.

### Parameters

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `grid_spacing` | 10.0 | 5-50 | Distance between consecutive levels beyond the first ($) |
| `anchor_gap` | 15.0 | 5-50 | Anchor to the first level above/below ($) |
| `buy_sell_offset` | 0.90 | 0-5 | Gap between buy and sell of a hedged pair ($) |
| `anchor_units` | 10.0 | 1-100 | Units per side of the hedged pair at the anchor |
| `grid_units` | 20.0 | 1-100 | Units per side at non-anchor levels |
| `stop_buffer` | 1.0 | 0-10 | Extra distance past the next level for the stop ($) |
| `max_levels_ahead` | 2 | 1-10 | Max active levels on the trending side |
| `max_levels_behind` | 1 | 1-10 | Max active levels on the trailing side |
| `session_profit_target` | 100.0 | 10-5000 | Close all & restart when hit ($) |
| `session_loss_limit` | 50.0 | 10-5000 | Close all & restart when hit ($) |
| `daily_loss_limit` | 200.0 | 50-10000 | Stop trading for the day ($) |

### When It Works Best

Volatile instruments with sustained directional moves (gold/XAU_USD). Oscillating
markets within one grid band cost little.

### When It Struggles

Whipsaw markets where price repeatedly reverses at stop-loss distance, hitting stops
on both sides of pairs. Regime-dependent — see the walk-forward findings.

## Ciby Hedged Doubling Grid

**File:** `src/aurex_trade/domain/strategy/ciby_hedged_doubling_grid.py`

Evolution of the hedged grid that removes stop losses and adds a doubling
mechanism at outer levels. Designed for breakout capture with zero bleed in
sideways markets.

### Signal Logic

- On first bar: anchor at current price, build 4 levels (2 above, 2 below)
- Each level gets a single limit order; when it fills, the engine places the
  opposite-side market order (forming a hedged pair with no stop loss)
- When both sides fill at an outer level: a doubled market order is placed
  (long at outer-below, short at outer-above) betting on mean-reversion
- All orders (limit, opposite market, doubled) carry broker-side take-profit
  at `2 * spacing` from entry — OANDA executes server-side even if bot is offline
- Session loss limit triggers close-all if the doubled position goes against

### Parameters

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `spacing` | 20.0 | 5-100 | Distance between grid levels ($) |
| `units` | 2.0 | 1-100 | Units per trade |
| `trailing_stop_distance` | 20.0 | 5-100 | Trailing stop on doubled position ($) |
| `session_loss_limit` | 100.0 | 10-5000 | Close all & restart when hit ($) |
| `whipsaw_limit` | 3 | 1-10 | Max re-triggers per level before pause |

### Take-Profit Mechanism

Every trade placed by this strategy has an automatic take-profit at
`entry ± (2 * spacing)`:

- Long (buy): TP = entry + 2*spacing
- Short (sell): TP = entry - 2*spacing

This applies to hedged-pair legs and the doubled position. The broker
executes the TP server-side. For the doubled position, trailing stop and TP
coexist — whichever fires first closes the trade.

### When It Works Best

Markets that oscillate within the grid or break out then revert. Hedged pairs
cost nothing during sideways movement; TP locks in profit on each leg
independently before price can reverse and neutralise gains.

### When It Struggles

Sustained trends beyond the outer level. The doubled position loses, and
hedged-pair short legs accumulate unrealised loss until the session loss limit
fires a close-all.

## Adding a New Strategy

1. Create `src/aurex_trade/domain/strategy/your_strategy.py`
2. Implement the `Strategy` Protocol:
   - `name` property returning a unique string identifier
   - `min_bars` property returning the minimum number of bars needed for signal generation
     (e.g., `max(lookback_window + 1, atr_period + 1)`)
   - `generate(bars)` method with your signal logic
   - `metadata()` classmethod returning `StrategyMetadata` with `ParamMeta` entries
3. Register in `backtest/cli.py`:
   - Add factory to `STRATEGY_REGISTRY`
   - Add validator to `PARAM_VALIDATORS` (for param constraints)
   - Add to `STRATEGY_METADATA`
5. Add tests in `tests/unit/domain/test_your_strategy.py`
6. Verify: `just backtest --strategy your_strategy --param key=value`
7. **Web UI**: No template changes needed — the UI renders dynamically from
   `StrategyMetadata`. New strategies automatically appear in dropdowns with
   correct parameter fields, tooltips, and valid ranges.
