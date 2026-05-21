# Trading Concepts

This page explains the core ideas behind AurexTrade in plain language.
No prior trading experience needed.

## Paper Trading vs Live Trading

When you start the bot for the first time, it trades with **virtual money** in
your OANDA practice account. This is called **paper trading** — it works exactly
like real trading, but nothing you do costs real money.

| Mode | Money | Risk | Purpose |
|------|-------|------|---------|
| **Paper (practice)** | Virtual — provided free by OANDA | None | Learn how the bot works, test strategies |
| **Live** | Real money from your funded account | Real financial risk | Actual trading for profit/loss |

You should stay on paper trading until you're confident in your strategy and
settings. There's no time limit and no pressure to switch.

!!! info "Going live requires two confirmations"
    AurexTrade has a safety gate: switching to live trading requires you to
    explicitly confirm twice. You can never accidentally trade real money.

## What Is a Trading Strategy?

A trading strategy is a set of rules that tells you when to buy and when to sell.
Instead of making decisions based on gut feeling, the strategy follows the same
rules every time — no emotions, no second-guessing.

Think of it like a recipe: given specific ingredients (market data), follow these
steps, and you get a specific output (a buy or sell decision).

## The Three Strategies

AurexTrade currently offers three strategies. They work in different ways, which means
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

### Ciby Grid Hedging (Direction-Neutral)

**The idea:** Don't predict direction at all. Instead, place a grid of price
levels above and below the current price, and let the market reveal which way
it wants to go. Losing positions get stopped out; winning positions accumulate.

**How it works:**

Imagine drawing horizontal lines on a price chart at regular intervals (e.g.,
every 10 points) above and below where the price is right now. These are your
**grid levels**.

- When price crosses a level **upward** — **buy** (the market is breaking out higher)
- When price crosses a level **downward** — **sell** (the market is breaking out lower)

Each position gets a **wide stop-loss** (e.g., 30 points) to give it room to
breathe. Positions that go the wrong way get stopped out. Positions that go the
right way survive and accumulate — gradually shifting your total exposure in the
direction the market is actually moving.

**When it works best:** Choppy or slowly trending markets. Gold (XAU/USD) is a
natural fit due to its tendency to oscillate within ranges before breaking out.

**When it struggles:** Fast, strong directional moves where price blows through
multiple grid levels at once, triggering stops on several positions in rapid
succession.

**Key risk:** Multiple stops can trigger in quick succession during strong trends.
The wide stop distance and max-levels cap are your defences against this.

**Analogy:** It's like casting a net in both directions and seeing which side
catches fish. You don't need to know which way the fish are swimming — the net
does the work.

---

## Which Strategy Should I Use?

| Market Condition | Best Strategy |
|-----------------|---------------|
| Clear trends (up or down for weeks) | SMA Crossover |
| Choppy, sideways, range-bound | RSI Mean-Reversion |
| Uncertain direction, want to capture either way | Ciby Grid Hedging |
| Not sure | Test all three with a backtest and compare results |

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

## Understanding Risk Settings

When you start the bot, you'll see a "Risk Settings" section. Here's what each
setting means in plain English:

**Kill Switch**
:   An emergency stop button. When activated, the bot immediately stops placing
    any new trades. Use this if something feels wrong and you want everything to
    halt right now.

**Risk Per Trade (default: 2%)**
:   How much of your balance you're willing to risk on a single trade. At 2%, if
    your balance is $100,000, the most you could lose on one trade is $2,000.
    Lower = safer but slower growth. Higher = more aggressive.

**Max Position (default: 10 units)**
:   The largest trade size the bot can place at once. Think of it as a cap on
    how big any single bet can be.

**Max Daily Loss (default: $500)**
:   If the bot loses this much in a single day, it stops trading for the rest
    of that day. Prevents a bad day from becoming a terrible day.

**Max Drawdown (default: 20%)**
:   If your balance drops more than 20% from its highest point, the bot stops
    entirely. This is your "worst case" safety net.

**Max Consecutive Losses (default: 5)**
:   If the bot loses 5 trades in a row, it pauses. This gives the market time
    to settle before trying again.

**Max Trades Per Day (default: 10)**
:   Prevents the bot from trading too often. More trades = more fees, and
    sometimes doing less is better.

**Require Stop-Loss (default: on)**
:   Every trade must have a maximum loss limit set before it's placed. If the
    market moves against you, the trade closes automatically at that limit.
    This should almost always stay on.

!!! tip "Start with the defaults"
    The default risk settings are conservative and designed for safe practice
    trading. You don't need to change them until you understand what each one
    does and why you'd want a different value.

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
