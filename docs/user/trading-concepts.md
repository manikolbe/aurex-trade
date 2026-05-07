# Trading Concepts

This page explains the core ideas behind AurexTrade in plain language.
No prior trading experience needed.

## What Is a Trading Strategy?

A trading strategy is a set of rules that tells you when to buy and when to sell.
Instead of making decisions based on gut feeling, the strategy follows the same
rules every time — no emotions, no second-guessing.

Think of it like a recipe: given specific ingredients (market data), follow these
steps, and you get a specific output (a buy or sell decision).

## The Two Strategies

AurexTrade currently offers two strategies. They work in opposite ways, which means
they perform best in different market conditions.

### SMA Crossover (Trend-Following)

**The idea:** Follow the trend. If the price has been going up, keep buying.
If it's been going down, keep selling.

**How it works:**

Imagine smoothing out a bumpy price chart into two lines:

- A **fast line** (short window) — reacts quickly to price changes
- A **slow line** (long window) — moves more gradually

When the fast line crosses *above* the slow line, the market is trending up — **buy**.
When the fast line crosses *below* the slow line, the market is trending down — **sell**.

**When it works best:** Markets that have clear upward or downward trends.

**When it struggles:** Markets that bounce sideways without a clear direction.

**Analogy:** It's like following a river's current. You don't fight the flow — you
go where the water is already moving.

---

### RSI Mean-Reversion (Counter-Trend)

**The idea:** What goes up too far will come back down, and vice versa. Buy when
the market has fallen too much. Sell when it has risen too much.

**How it works:**

The RSI (Relative Strength Index) measures how "exhausted" the price movement is
on a scale of 0 to 100:

- **Below 30** = oversold — the price has fallen too far, too fast. Expect a bounce. **Buy.**
- **Above 70** = overbought — the price has risen too far, too fast. Expect a pullback. **Sell.**

**When it works best:** Markets that bounce between a range (sideways/choppy markets).

**When it struggles:** Strong trending markets where "overbought" keeps going higher.

**Analogy:** It's like a rubber band. The further you stretch it from the middle,
the harder it snaps back.

## Which Strategy Should I Use?

| Market Condition | Best Strategy |
|-----------------|---------------|
| Clear trends (up or down for weeks) | SMA Crossover |
| Choppy, sideways, range-bound | RSI Mean-Reversion |
| Not sure | Test both with a backtest and compare results |

In practice, no one knows in advance what the market will do. That's why testing
matters — you can see which strategy worked better on recent data.

## What Is the Risk Engine?

The risk engine is a safety system that sits between the strategy and your money.
Even if the strategy says "buy", the risk engine can block the trade if it would
be unsafe.

**Rules the risk engine enforces:**

| Rule | What it prevents |
|------|-----------------|
| **Kill switch** | Halts all trading immediately (emergency stop) |
| **Stop-loss required** | Every trade must have a maximum loss limit |
| **Max drawdown** | Stops trading if total losses get too large |
| **Consecutive losses** | Pauses after several losses in a row |
| **Position size limit** | Prevents betting too much at once |
| **Daily loss limit** | Stops trading for the day if losses exceed a threshold |
| **Trade frequency** | Prevents excessive trading (overtrading) |

Think of it as a co-pilot that can override the autopilot when things look dangerous.

## The 3-Step Validation Workflow

Before you trust a strategy with real money, you need to validate it. AurexTrade
provides three tools for this:

### Step 1: Backtest

**What it does:** Replays past market data through your strategy and records what
would have happened.

**Why it matters:** If a strategy doesn't work on past data, it almost certainly
won't work on future data either.

**Limitation:** Just because it worked in the past doesn't guarantee future success.

---

### Step 2: Parameter Sweep

**What it does:** Tests every combination of strategy settings (e.g., different
moving average lengths) and ranks them by performance.

**Why it matters:** The same strategy can be profitable with one set of parameters
and unprofitable with another. A sweep finds the best settings systematically.

**Limitation:** The "best" settings might be overfit — they could be tailored to
quirks of that specific historical period.

---

### Step 3: Walk-Forward Validation

**What it does:** Splits the data into alternating "learning" and "exam" periods.
Finds the best parameters on the learning period, then tests them on the exam
period (data the system has never seen).

**Why it matters:** This is the closest thing to testing on future data without
actually waiting. If settings work on the exam period, they're more likely to be
genuinely robust.

**Analogy:** It's like studying for an exam using practice questions (learning period),
then taking a real exam with different questions (exam period). If you pass the real
exam, you probably actually learned the material — not just memorised answers.

## What Happens When You Start the Bot?

Once you've validated a strategy, you can run the trading bot. Here's what it does
each cycle (every 60 seconds by default):

1. **Fetches the latest price data** from OANDA
2. **Runs the strategy** to see if there's a buy or sell signal
3. **Checks the risk engine** — is it safe to trade?
4. **Places the order** if approved (or skips if rejected)
5. **Records everything** — every decision is logged

The bot runs continuously until you stop it or the kill switch activates.
