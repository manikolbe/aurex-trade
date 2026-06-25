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

## The Strategies

AurexTrade offers two strategies, both **grid** strategies built for gold
(XAU_USD). They don't try to predict direction — they place orders around the
current price and manage risk through session and daily limits.

### Ciby Sliding Grid (Direction-Neutral, Sliding Window)

**The idea:** A variation on the Ciby hedged grid that **follows price with a small,
fixed window of levels** instead of leaving every level open forever. It opens a
hedged pair (one buy AND one sell) at each level, but keeps only a handful active at
a time — as price trends, levels far behind are closed to free up margin, while the
**anchor pair is never closed** and its winning side rides the whole move. That
anchor leg is where the real profit comes from.

**How it works:**

When the bot starts a session, it takes the current price as the **anchor** and
opens a hedged pair there. It then sets the first level a wider **anchor gap** away
(above and below), and every level beyond that one **grid spacing** apart. For
example, with an anchor of 4100, an anchor gap of 15 and a spacing of 10, the levels
sit at …4085, 4100 (anchor), 4115, 4125, 4135…

- At each level the **sell** rests at the level price and the **buy** rests a small
  **offset** above it (typically $0.90) to work around the spread.
- Each side carries a stop-loss just past the **next level in its losing direction**
  — a buy is stopped below, a sell above — so a stopped-out leg loses only about one
  grid gap.
- Orders rest at their **exact price** until the market reaches them (no early
  fills), so the grid is laid down precisely even in fast-moving markets.
- Position size is smaller at the anchor and larger at every other level.

**The sliding window (margin management):**

The bot caps how many levels are active at once — by default **2 levels on the side
price is trending into** and **1 on the trailing side** (the anchor is exempt and
always counts as neither). As price climbs and a new level opens beyond the cap, the
**trailing level nearest the anchor is closed**, banking its result and freeing
margin for the level ahead.

For example, anchored at 4100 with price rising:

- Price reaches 4115, then 4125 → both active (2 above the anchor).
- Price reaches 4135 → opening it would make 3 above, so **4115 is closed** → active
  above = 4125, 4135.
- Price reaches 4145 → **4125 is closed** → active above = 4135, 4145.

The anchor's pair at 4100 stays open the entire time. Because price kept rising, the
anchor's buy leg is deep in profit — that is the position the strategy is really
riding. Closing the trailing levels isn't a loss; those hedged pairs are closed at
or near break-even, and the point is simply to **stay in the game without running out
of margin**. If price turns and trends down instead, the window flips: 2 active
levels below the anchor and 1 above.

**Re-completing a pair:** If price leaves a level (still within the active window)
and later returns after one side was stopped out, the bot re-places only the
**missing** side — completing the pair again without stacking duplicates. Levels that
were deliberately closed for margin are *not* reopened.

**Session management:** Same safety net as the hedged grid — a session profit target
and session loss limit (close everything and restart fresh), plus a daily loss limit
that stops trading until the next day.

**When it works best:** Volatile instruments that trend (gold/XAU_USD is ideal). A
sustained directional move lets the anchor leg run while the window keeps margin
under control.

**When it struggles:** Tight whipsaw that repeatedly stops out both sides of pairs
near the same levels.

**Analogy:** It's like a moving walkway with a fixed number of footholds. You plant
your anchor foot and let it carry you the whole way; as you stride forward, the
foothold furthest behind folds away so you're never overstretched — but you keep
moving in whichever direction the walkway is taking you.

---

### Ciby Hedged Doubling Grid (Breakout Capture)

**The idea:** Do nothing in sideways markets, capture big directional moves, and
never bleed from whipsaw. Instead of using stop losses that get picked off in
choppy conditions, this strategy uses **hedged pairs with no stop loss** and only
profits from **doubled positions** at outer grid levels.

**How it works:**

When the bot starts, it places 4 grid levels around the current price (2 above,
2 below). At each level, it opens a **hedged pair** (one buy AND one sell) with
**no stop loss**. Hedged pairs always net to zero P&L — they exist to mark that
price has visited a level.

The real action happens at the **outer levels** (the 2nd level from the anchor):

- When price drops to the outer-below level: an extra **buy** is placed (betting
  on a bounce back up)
- When price rises to the outer-above level: an extra **sell** is placed (betting
  on a reversal back down)

These extra "doubled" positions have a **trailing stop** that locks in profit
once the price moves favourably by one grid spacing.

**Take profit:** Every trade has an automatic broker-side take-profit set at 2
grid spacings from entry. Long trades close when price rises 2 spacings; short
trades close when price drops 2 spacings. This locks in profit per-leg before
price can reverse and neutralise gains.

**Protection mechanisms:**

- **Auto take-profit on every trade** — each leg closes automatically when price
  moves 2 spacings in the profitable direction, banking gains before price reverses
- **No stop loss on hedged pairs** — eliminates all whipsaw bleeding
- **Trailing stop on doubled position** — captures breakout profit, limits giveback
- **Session loss limit** — circuit breaker if the doubled position goes against you
- **Whipsaw detection** — if the same level re-triggers too many times, the session
  pauses automatically

**When it works best:** Markets that range quietly, then break out in one direction.
Gold (XAU/USD) during news events or session opens is ideal.

**When it struggles:** Sustained adverse moves immediately after doubling (the
doubled position loses). However, loss is bounded by the session loss limit.

**Analogy:** It's like setting a net at the edge of a fish pond. You don't catch
anything while the fish swim in circles in the middle (zero cost). But when they
make a break for the edge, you catch them.

---

### Simple Grid (Direction-Neutral)

**The idea:** Place a grid of price levels above and below the current price,
and let the market reveal which way it wants to go. Losing positions get stopped
out; winning positions accumulate.

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
| Sustained trends, with margin kept under control | Ciby Sliding Grid |
| Quiet ranges followed by breakouts | Ciby Hedged Doubling Grid |
| Not sure | Test with a backtest and compare results |

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
grid spacings and anchor gaps) and ranks them by performance.

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
