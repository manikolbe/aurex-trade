# Getting Started

This guide walks you through running your first backtest. It takes about 5 minutes.

## Step 1: Open the Web Interface

Navigate to [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.
You'll see the home page with a health status indicator and links to the main features.

## Step 2: Go to Backtest

Click **Strategy Testing** in the top menu, then select **Backtest**.

## Step 3: Run Your First Test

The backtest page has a form with several fields. For your first run, you only need
to do one thing:

1. **Leave all defaults as they are** — they're set to sensible starting values
2. Click **Run Backtest**

That's it. The system will test the SMA Crossover strategy against historical gold
price data and show you the results.

!!! tip "What just happened?"
    The system replayed past gold prices through a trading strategy to see what
    would have happened if you had followed its signals. No real money was involved.

## Step 4: Read Your Results

After a few seconds, you'll see:

- **A metrics table** — key numbers that tell you how the strategy performed
- **An equity curve** — a chart showing how your account balance changed over time

The most important numbers to look at first:

| Metric | What it tells you |
|--------|-------------------|
| **Total P&L** | Did the strategy make or lose money overall? |
| **Win Rate** | What percentage of trades were profitable? |
| **Max Drawdown** | What was the worst losing streak? |

For detailed explanations of all metrics, see [Understanding Results](understanding-results.md).

## What to Do Next

Once you've run a single backtest, the next steps are:

### Try Different Settings

Go back to the form and change some parameters. For example, with SMA Crossover:

- Try a shorter **Short Window** (e.g., 5) — makes the strategy react faster
- Try a longer **Long Window** (e.g., 50) — makes it more cautious

### Run a Parameter Sweep

Instead of testing one setting at a time, use the **Sweep** page to test hundreds
of combinations automatically. The system will rank them and show you which settings
performed best.

### Validate with Walk-Forward

Found settings you like? Use the **Walk-Forward** page to check whether those
settings only worked in the past (overfitting) or are genuinely robust.

## The 3-Step Workflow

The recommended workflow is:

```
1. Backtest  →  2. Sweep  →  3. Walk-Forward
   (sanity        (find         (prove it's
    check)         best)         not luck)
```

Each step builds on the previous one:

1. **Backtest** confirms the strategy can work at all
2. **Sweep** finds the best parameter combination
3. **Walk-Forward** proves the best settings aren't just a lucky fit to past data

!!! warning "Don't skip Walk-Forward"
    It's tempting to take the best sweep result and start trading immediately.
    But the best settings in a sweep are often **overfit** — they worked perfectly
    on that specific data but will fail on new data. Walk-Forward catches this.
