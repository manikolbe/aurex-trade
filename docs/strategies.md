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
