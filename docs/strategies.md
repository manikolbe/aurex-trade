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
- Stop-loss is calculated using ATR from the shared `indicators.py` module

## Shared Indicators

`src/aurex_trade/domain/strategy/indicators.py` provides reusable calculations:

- `calculate_atr(bars, period)` — Average True Range (volatility measure)

## SMA Crossover

**File:** `src/aurex_trade/domain/strategy/sma_crossover.py`

A trend-following strategy using two Simple Moving Averages. The "fast" average
tracks recent price action while the "slow" average captures the longer-term trend.

### Signal Logic

- **LONG**: Short SMA crosses above Long SMA (upward momentum)
- **SHORT**: Short SMA crosses below Long SMA (downward momentum)
- Crossing = compare previous bar's SMA positions to current bar's

### Parameters

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `short_window` | 10 | 2-100 | Fast moving average lookback |
| `long_window` | 30 | 5-500 | Slow moving average lookback |
| `atr_multiplier` | 2.0 | 0.5-5.0 | Stop-loss distance in ATR units |
| `atr_period` | 14 | 2-50 | ATR calculation lookback |

### Minimum Bars Required

`long_window + 1` — needs previous + current SMA to detect crossing.

### When It Works Best

Trending markets with clear directional moves. Produces false signals in
sideways/choppy conditions.

## RSI Mean-Reversion

**File:** `src/aurex_trade/domain/strategy/rsi_mean_reversion.py`

A counter-trend strategy using the Relative Strength Index. Identifies when
selling/buying pressure is exhausted and price is likely to revert to the mean.

### Signal Logic

- **LONG**: RSI crosses below the oversold threshold (selling exhausted, expect bounce)
- **SHORT**: RSI crosses above the overbought threshold (buying exhausted, expect fall)
- Crossing = previous RSI on one side of threshold, current RSI on the other

### RSI Calculation (Wilder's Method)

1. Compute price changes: `change[i] = close[i] - close[i-1]`
2. First average: simple mean of first `period` gains/losses
3. Subsequent: Wilder's smoothing `avg = (prev_avg * (period-1) + current) / period`
4. RS = avg_gain / avg_loss
5. RSI = 100 - (100 / (1 + RS))

### Parameters

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `period` | 14 | 2-50 | RSI lookback period |
| `overbought` | 70 | 50-95 | Level above which asset is overbought |
| `oversold` | 30 | 5-50 | Level below which asset is oversold |
| `atr_multiplier` | 2.0 | 0.5-5.0 | Stop-loss distance in ATR units |
| `atr_period` | 14 | 2-50 | ATR calculation lookback |

### Minimum Bars Required

`period + 2` — needs two consecutive RSI values to detect crossing.

### When It Works Best

Ranging/sideways markets where price oscillates around a mean. May give false
signals during strong trends (price can stay overbought/oversold for extended
periods in trending markets).

## Ciby Hedged Grid

**File:** `src/aurex_trade/domain/strategy/ciby_hedged_grid.py`

A stateful grid strategy that places hedged pairs (buy + sell) at grid levels.
Unlike simple strategies, it uses callbacks to track fills and closures, and
manages its own session/daily risk limits.

### Signal Logic

- On first bar: anchor at current price, place initial hedged pair (market)
- When price crosses a grid level: place limit order at that level
- When a limit fills: runner places opposite market order (forming a pair)
- Each position gets a stop-loss just past the adjacent grid level
- Session profit target or loss limit triggers FLAT/close_all signal

### Parameters

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `grid_spacing` | 10.0 | 5-50 | Distance between grid levels ($) |
| `grid_units` | 10.0 | 1-100 | Units per grid pair |
| `session_profit_target` | 100.0 | 10-5000 | Close all & restart when hit ($) |
| `session_loss_limit` | 50.0 | 10-5000 | Close all & restart when hit ($) |
| `daily_loss_limit` | 200.0 | 50-10000 | Stop trading for the day ($) |

### Grid Strategy Protocol Extensions

Grid strategies extend the base `Strategy` Protocol with additional methods:

```python
def report_fill(self, grid_level_key: str, fill_price: float) -> None: ...
def report_trade_closed(self, grid_level_key: str, realized_pnl: float) -> None: ...
def notify_close_all_complete(self) -> None: ...
def update_unrealized_pnl(self, unrealized_pnl: float) -> None: ...
```

The backtest runner auto-detects grid strategies via `hasattr(strategy, "report_fill")`
and switches to grid orchestration mode (signal drain loop, limit order simulation,
stop-loss enforcement, strategy callbacks).

### Risk Engine

The risk engine is **disabled** for grid backtests — the strategy manages its own
risk via session/daily loss limits. Detection is automatic via duck-typing.

### When It Works Best

Volatile instruments with sustained directional moves (gold/XAU_USD). Oscillating
markets within one grid band cost nothing (no stops hit).

### When It Struggles

Whipsaw markets where price repeatedly reverses at exactly stop-loss distance,
hitting stops on both sides of pairs. Strong trends with tight grid spacing can
also bleed through rapid stop-outs.

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

## Simple Grid

**File:** `src/aurex_trade/domain/strategy/simple_grid.py`

A direction-neutral grid that places orders when price crosses levels. Unlike the
hedged grid, it places single-direction orders (not pairs).

### Parameters

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `grid_spacing` | 10.0 | 5-50 | Distance between grid levels ($) |
| `max_levels` | 6 | 2-20 | Maximum active grid levels |
| `stop_distance` | 30.0 | 5-100 | Stop-loss distance ($) |
| `num_levels_above` | 3 | 1-10 | Grid levels above anchor |
| `num_levels_below` | 3 | 1-10 | Grid levels below anchor |

## Adding a New Strategy

1. Create `src/aurex_trade/domain/strategy/your_strategy.py`
2. Implement the `Strategy` Protocol:
   - `name` property returning a unique string identifier
   - `min_bars` property returning the minimum number of bars needed for signal generation
     (e.g., `max(lookback_window + 1, atr_period + 1)`)
   - `generate(bars)` method with your signal logic
   - `metadata()` classmethod returning `StrategyMetadata` with `ParamMeta` entries
3. Use `calculate_atr` from `indicators.py` for stop-loss (recommended)
4. Register in `backtest/cli.py`:
   - Add factory to `STRATEGY_REGISTRY`
   - Add validator to `PARAM_VALIDATORS` (for param constraints)
   - Add to `STRATEGY_METADATA`
5. Add tests in `tests/unit/domain/test_your_strategy.py`
6. Verify: `just backtest --strategy your_strategy --param key=value`
7. **Web UI**: No template changes needed — the UI renders dynamically from
   `StrategyMetadata`. New strategies automatically appear in dropdowns with
   correct parameter fields, tooltips, and valid ranges.
