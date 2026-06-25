# Strategy Testing

Once you've watched the bot trade for a while, you might wonder: "Are these the
best settings? Could it do better?" Strategy testing helps you answer that question.

## What Is Backtesting?

Backtesting means replaying past market data through a strategy to see what
*would* have happened. It's like rewinding time and asking: "If I'd followed
these rules last month, would I have made money?"

No real money is involved — it's a simulation using historical prices.

!!! note "Why bother?"
    If a strategy doesn't work on past data, it almost certainly won't work on
    future data either. Testing first saves you from running a bad strategy on
    your practice (or eventually, real) account.

## Running Your First Backtest

1. Click **Strategy Testing** in the top menu, then select **Backtest**
2. You'll see a form with several fields — the defaults are sensible, so you
   can leave them as they are for your first run
3. Click **Run Backtest**

That's it. The system will replay past gold prices through the strategy and
show you the results.

!!! tip "First run takes a little longer"
    The first time you run a backtest, AurexTrade downloads historical data from
    OANDA. You'll see a "Downloading..." status — this typically takes 10–30 seconds.
    Subsequent runs reuse the cached data and are much faster.

## Reading Your Results

After a few seconds, you'll see:

- **A metrics table** — key numbers showing how the strategy performed
- **An equity curve** — a chart showing how the account balance changed over time

The most important numbers to look at first:

| Metric | What it tells you |
|--------|-------------------|
| **Total P&L** | Did the strategy make or lose money overall? |
| **Win Rate** | What percentage of trades were profitable? |
| **Max Drawdown** | What was the worst losing streak? |

For detailed explanations of every metric, see [Understanding Results](understanding-results.md).

## Trying Different Settings

Go back to the form and change some parameters. For example, with Ciby Sliding Grid:

- Try a wider **Grid Spacing** (e.g., 20) — fewer, more widely-spaced levels
- Try a larger **Stop Buffer** (e.g., 3) — gives each leg more room before stopping out

Each combination will give different results. But testing one at a time is slow —
which is where the next tools come in.

## The 3-Step Workflow

AurexTrade provides three testing tools that build on each other:

```
1. Backtest  →  2. Sweep  →  3. Walk-Forward
   (does it       (find the     (prove it's
    work?)         best)         not luck)
```

### Step 1: Backtest

Test a single set of parameters to confirm the strategy can work at all.

### Step 2: Parameter Sweep

Instead of testing one setting at a time, the **Sweep** page tests hundreds
of combinations automatically and ranks them. It finds the best settings for you.

Click **Strategy Testing → Sweep**, set the parameter ranges you want to explore,
and let it run.

### Step 3: Walk-Forward Validation

Found settings you like? Use **Strategy Testing → Walk-Forward** to check
whether those settings genuinely work — or just got lucky on that specific data.

It splits the data into alternating "learning" and "exam" periods. If the settings
work on the exam period (data they've never seen), they're more likely to be
genuinely reliable.

!!! warning "Don't skip Walk-Forward"
    It's tempting to take the best sweep result and start trading immediately.
    But the best settings in a sweep are often **overfit** — they worked perfectly
    on that specific time period but will fail on new data. Walk-Forward catches this.

## Saving Your Defaults

Once you've found settings you like, you can save them so all forms pre-fill
automatically next time.

1. Go to **Settings** in the top menu
2. Under **Strategy Defaults**, pick a strategy, adjust the parameters, and
   click **Save Strategy Defaults**
3. Under **Risk & Cost Defaults**, set your preferred values and click
   **Save Risk Defaults**

!!! tip "Reset to Defaults"
    Made a mess of the settings? Click **Reset to Defaults** to restore the
    original values instantly.

## From Testing to Trading

Once you've found settings that pass all three steps (backtest, sweep,
walk-forward), you can use those same settings when starting your bot.

Go to **Trading Bot**, select the strategy and parameters you validated,
and start paper trading with confidence that you've done your homework.
