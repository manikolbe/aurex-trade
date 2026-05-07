# Understanding Results

After running a backtest, sweep, or walk-forward test, you'll see a results table
and a chart. This page explains what every number means and how to interpret it.

## The Metrics Table

### Total P&L (Profit & Loss)

**What it is:** The total amount of money the strategy made or lost, in dollars.

**How to read it:**

- Positive = the strategy was profitable overall
- Negative = the strategy lost money

**Context:** A positive P&L is necessary but not sufficient. A strategy could make
money overall but have terrifying drawdowns along the way.

---

### Win Rate

**What it is:** The percentage of trades that ended in profit.

**How to read it:**

- 60% means 6 out of every 10 trades made money
- A win rate above 50% isn't always required — some strategies win rarely but win big

**Common misconception:** A high win rate doesn't guarantee profitability. A strategy
could win 90% of the time but lose so much on the 10% that it's unprofitable overall.

---

### Sharpe Ratio

**What it is:** How much return you get for the risk you take. It measures
consistency — not just profit, but how smooth the ride is.

**How to read it:**

| Sharpe | Interpretation |
|--------|---------------|
| Below 0 | Strategy loses money |
| 0 to 1 | Positive but not great — high volatility relative to returns |
| 1 to 2 | Good — decent return with manageable risk |
| Above 2 | Excellent — strong returns with relatively smooth performance |

**Why it matters:** Two strategies can both make 10%, but if one does it smoothly
and the other swings wildly between +30% and -20%, the smooth one has a better Sharpe.

---

### Profit Factor

**What it is:** Total money won divided by total money lost.

**How to read it:**

| Profit Factor | Interpretation |
|---------------|---------------|
| Below 1.0 | Losing money (losses > wins) |
| 1.0 to 1.5 | Marginal — barely breaking even after costs |
| 1.5 to 2.0 | Good |
| Above 2.0 | Very strong |

**Simple way to think about it:** For every $1 lost, how many dollars did you win?
A profit factor of 2.0 means you won $2 for every $1 you lost.

---

### Expectancy

**What it is:** The average profit (or loss) per trade, in dollars.

**How to read it:**

- Positive = on average, each trade makes money
- Negative = on average, each trade loses money

**Why it matters:** This is the simplest "is it worth it?" number. If expectancy
is $5, then over 100 trades you'd expect to make about $500.

---

### Max Drawdown

**What it is:** The largest peak-to-trough drop in your account balance during
the test period. It answers: "What was the worst losing streak?"

**How to read it:**

- Shown as both a dollar amount and a percentage of capital
- A drawdown of 15% means at some point, the account fell 15% from its highest value

**Why it matters:** This tells you how much pain you'd need to endure. Even a
profitable strategy can have periods where you're significantly down. If you can't
stomach a 20% drawdown, don't use a strategy that has one in testing.

**Rule of thumb:** Expect real-world drawdowns to be 1.5-2x worse than what you
see in backtesting.

---

### Initial Capital / Final Capital

**What it is:** The starting balance and ending balance.

**How to read it:** Final capital minus initial capital equals Total P&L.
These are included so you can see the scale relative to your investment.

---

### Total Commission

**What it is:** The total fees paid across all trades.

**Why it matters:** Strategies that trade frequently can eat their profits in fees.
If Total P&L is $500 but Total Commission is $400, the strategy only really made $100.

## The Equity Curve

The chart below the metrics table shows your account balance over time.

**How to read it:**

- **Upward slope** = making money during that period
- **Downward slope** = losing money during that period
- **Flat areas** = no trades happening or trades are breaking even

**What to look for:**

- A steadily rising curve is ideal (consistent profits)
- Large drops followed by recoveries are drawdowns
- A curve that goes up then crashes down might indicate a strategy that worked for
  a while then stopped working

## Sweep Results

When you run a parameter sweep, results are presented as a ranked table.

**How to read it:**

- Results are sorted by the metric you chose (Sharpe Ratio by default)
- The top row is the best-performing parameter combination
- Each row shows the parameters used and the key performance metrics

**What to look for:**

- Is the top result much better than the rest? (might be overfitting)
- Are there clusters of similar good results? (more robust — nearby settings also work)
- Is the top result suspiciously good? (probably overfitting — validate with walk-forward)

## Walk-Forward Results

Walk-forward results show two levels of detail:

### Aggregate Metrics

These combine all the "exam" periods together. This is the most important number —
it tells you how the strategy performed on data it had never seen.

### Per-Window Breakdown

Each row shows one learning/exam pair:

| Column | Meaning |
|--------|---------|
| **Window** | Which time period (1, 2, 3...) |
| **Best Params** | Settings that won during the learning period |
| **Train P&L** | How much the strategy made during learning (expected to be good) |
| **Test P&L** | How much it made during the exam (the real test) |
| **Test Sharpe** | Consistency during the exam period |

**What to look for:**

- Test metrics should be positive (strategy works on unseen data)
- If train metrics are amazing but test metrics are terrible, the strategy is **overfit**
- Consistent performance across multiple windows is a strong sign

## Rules of Thumb

!!! tip "What 'good' looks like"
    - **Sharpe > 1.0** on walk-forward test periods
    - **Profit Factor > 1.5** consistently
    - **Max Drawdown < 20%** of capital
    - **Win Rate** matters less than Sharpe and Profit Factor
    - **Consistency** across walk-forward windows matters more than any single number

!!! warning "Red flags"
    - Amazing backtest results that collapse in walk-forward
    - Only one or two parameter combinations work (fragile)
    - Very high trade count with tiny average profit (vulnerable to fee changes)
    - Max drawdown larger than you're comfortable losing in real life
