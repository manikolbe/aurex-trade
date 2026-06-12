# Glossary

Quick definitions of terms you'll encounter in AurexTrade.

---

## A

**Anchor Gap**
:   In the Ciby Sliding Grid strategy, the distance from the anchor to the first
    grid level above and below it. It is usually wider than the grid spacing, giving
    the first hedged pair more room before the evenly-spaced levels begin.

**Anchor Price**
:   The center price around which a grid is built. When the grid strategy starts,
    it uses the current market price as the anchor and places grid levels at fixed
    intervals above and below it.

**API Token**
:   A secret key that authorises AurexTrade to communicate with your broker account
    on your behalf. Generated in the OANDA Hub and entered once in Settings → Broker.

**ATR (Average True Range)**
:   A measure of how much the price typically moves in a given period. Used to set
    stop-losses at a sensible distance from the current price. Higher ATR = more
    volatile market.

## B

**Breakout Reinforcement**
:   A technique where positions are added in the direction of a price breakout.
    In grid trading, when price breaks through levels in one direction, the
    surviving positions on that side accumulate while the losing side gets stopped
    out, reinforcing the emerging trend.

**Broker**
:   A service that connects you to financial markets and executes trades. AurexTrade
    uses OANDA as its broker for market data and trade execution.

**Backtest**
:   Running a strategy against historical (past) market data to see how it would
    have performed. No real money is involved.

**Bar**
:   A single unit of price data for a time period. Each bar contains the open, high,
    low, and close prices. A "1-minute bar" (M1) represents one minute of trading.

## C

**Capital**
:   The amount of money you start with in a test (or in real trading). Default is
    $100,000 in backtests.

**Commission**
:   A fee charged by the broker for executing a trade. Measured in dollars per trade.

**Consecutive Losses**
:   The number of losing trades in a row. The bot can be configured to pause after
    a certain number of consecutive losses to let the market settle.

**Cycle**
:   One loop of the bot's operation: check the price, decide whether to trade, act
    (or not), then wait for the next cycle. By default, a cycle runs every 60 seconds.

**Connection Test**
:   A verification step that confirms your broker credentials are valid by contacting
    the broker's API. Use this after entering credentials to make sure they work
    before saving.

## D

**Doubling**
:   Adding extra units at an outer grid level to create a directional bet. In the
    Hedged Doubling Grid strategy, when price reaches the outermost level, extra
    units are placed on the reversal side (e.g., extra buy if price dropped to the
    outer-below level). These doubled units are the only source of profit.

**Daily Loss Limit**
:   The maximum amount of money the bot is allowed to lose in a single day. Once
    this limit is hit, the bot stops trading for the rest of the day.

**Demo Account**
:   See *Practice Account*.

**Drawdown**
:   The drop from a peak (highest point) to a trough (lowest point) in your account
    balance. If your account goes from $110,000 to $95,000, the drawdown is $15,000
    (or about 13.6%).

## E

**Encryption Key**
:   A secret value used to protect your broker credentials at rest. Generated once
    when you set up the application and stored in your server's environment. Without
    this key, stored credentials cannot be decrypted.

**Equity Curve**
:   A chart showing how your account balance changes over time during a test.
    Ideally slopes upward smoothly.

**Expectancy**
:   The average profit or loss per trade. Positive expectancy means the strategy
    makes money on average.

## G

**Grid Level**
:   A specific price point in a grid trading strategy where an order is placed.
    When price crosses a grid level, it triggers a buy or sell signal depending
    on the direction of the crossing.

**Grid Spacing**
:   The fixed distance (in price points) between adjacent grid levels. For gold
    trading, 15 points is typical for the Ciby Hedged Grid strategy.

**Grid Trading**
:   A strategy that places orders at regular price intervals (a "grid") above and
    below the current price. Captures movement in either direction without
    predicting which way the market will go.

**Granularity**
:   The time period each price bar represents. M1 = 1 minute, M5 = 5 minutes,
    H1 = 1 hour, D = 1 day. Smaller granularity means more data points and more
    detailed testing.

**Hedged Pair**
:   Two simultaneous trades in opposite directions (one buy and one sell) placed
    at the same price. In the Ciby Hedged Grid strategy, one side profits while
    the other gets stopped out. In the Hedged Doubling Grid strategy, hedged pairs
    have no stop loss and always net to zero P&L — they mark that price visited
    a level.

## I

**Interval**
:   How often the bot checks for new trading opportunities. An interval of 60
    seconds means the bot looks at the market once per minute.

## K

**Kill Switch**
:   An emergency stop that immediately halts all trading. Used when something goes
    wrong and you want to stop everything instantly.

## L

**Limit Order**
:   A resting order to buy *below* the current price or sell *above* it — that is,
    at a price more favourable than the market. It waits until the price reaches the
    chosen level and then fills there.

**Long (Buy)**
:   Buying an asset because you expect the price to go up. You profit if the price
    rises after you buy.

## M

**Max Position**
:   The largest trade size the bot is allowed to place at once. Acts as a cap on
    how big any single trade can be.

**Max Drawdown**
:   The worst (largest) drawdown during a test period. Tells you the most painful
    losing streak you would have experienced.

**Moving Average (MA)**
:   A smoothed version of the price that filters out short-term noise. Calculated
    by averaging the last N bars. A 10-bar MA averages the last 10 prices.

## O

**OANDA**
:   The broker that AurexTrade connects to for market data and trade execution.
    Offers free demo accounts with virtual money for risk-free testing.

**Overbought**
:   When the RSI is above a high threshold (default 70), suggesting the price has
    risen too fast and may pull back.

**Oversold**
:   When the RSI is below a low threshold (default 30), suggesting the price has
    fallen too fast and may bounce back.

**Overfitting**
:   When a strategy's parameters are so perfectly tuned to past data that they
    don't work on new data. Like memorising exam answers instead of learning the
    subject.

## P

**Paper Trading**
:   Trading with virtual (fake) money to practise and learn. Everything works
    exactly like real trading, but there's no financial risk. Also called
    "practice trading" or "demo trading".

**Practice Account**
:   A free broker account that uses virtual money. Lets you test strategies without
    financial risk. Also called a "demo account".

**Parameter**
:   A setting that controls how a strategy behaves. For example, the "short window"
    in SMA Crossover controls how quickly the fast moving average reacts.

**Parameter Sweep**
:   Testing every combination of parameter values to find the best-performing
    settings. Also called a grid search.

**P&L (Profit and Loss)**
:   The total money made or lost. Positive = profit, negative = loss.

**Position**
:   A current holding — you have an open position when you've bought (or sold short)
    and haven't yet closed the trade.

**Profit Factor**
:   Total money won divided by total money lost. Above 1.0 = profitable.

## R

**Risk Per Trade**
:   The fraction of your balance you're willing to lose on a single trade. Expressed
    as a decimal: 0.02 means 2%. If your balance is $100,000 and risk per trade is
    0.02, the most you could lose on one trade is $2,000.

**RSI (Relative Strength Index)**
:   A number from 0 to 100 that measures how fast the price has been moving up vs.
    down recently. Used to identify overbought and oversold conditions.

## S

**Session (Trading Session)**
:   In the Ciby Hedged Grid strategy, a session is one cycle of grid trading
    from start to close-all. A session ends when the profit target or loss limit
    is hit, then a new session starts fresh at the current price. Multiple
    sessions can occur in a single trading day.

**Session Profit Target**
:   The dollar amount of realized profit that triggers the bot to close all
    positions and restart fresh. Locks in gains before a reversal can erode them.

**Session Loss Limit**
:   The dollar amount of realized loss that triggers the bot to close all
    positions and restart fresh. Caps damage from whipsaw markets.

**Sharpe Ratio**
:   A measure of risk-adjusted return. Higher is better. Tells you how much return
    you get per unit of risk (volatility).

**Short (Sell)**
:   Selling an asset because you expect the price to go down. You profit if the
    price falls after you sell. (In CFD trading, you can sell without owning the asset.)

**Signal**
:   A buy or sell recommendation generated by a strategy. The strategy looks at
    market data and decides: buy, sell, or do nothing.

**Slippage**
:   The difference between the price you expected and the price you actually got.
    In fast-moving markets, prices can change between your decision and execution.

**SMA (Simple Moving Average)**
:   A type of moving average that gives equal weight to all bars in the window.
    A 10-bar SMA is the average of the last 10 closing prices.

**Spread**
:   The difference between the buy price and sell price offered by the broker.
    This is a cost you pay on every trade. Measured in pips (price units).

**Stop Order**
:   A resting order to buy *above* the current price or sell *below* it — the
    opposite of a limit order. It waits until the price reaches the trigger and then
    enters. The Ciby Sliding Grid uses stop orders for the breakout side of each pair
    so the order rests at its exact price instead of filling early.

**Stop-Loss**
:   A price level at which a trade is automatically closed to limit losses. If you
    buy at $100 with a stop-loss at $95, the trade closes automatically if the price
    drops to $95, limiting your loss to $5.

## T

**Trade Frequency**
:   How often the strategy opens trades. Higher frequency means more opportunities
    but also more commission costs.

**Trailing Stop**
:   A stop-loss that follows the price as it moves in your favour, locking in
    profit. Unlike a fixed stop-loss that stays at one price, a trailing stop
    moves with the market. If price reverses by the trail distance, the position
    is closed with some profit preserved.

## W

**Whipsaw**
:   Rapid back-and-forth price action that crosses the same level repeatedly without
    committing to a direction. In grid strategies, whipsaw can trigger the same
    level multiple times. The Hedged Doubling Grid detects this and pauses the
    session after a configurable number of re-triggers.

**Walk-Forward Validation**
:   A test that splits data into alternating learning and exam periods to check if
    optimised parameters work on unseen data. The gold standard for strategy validation.

**Window**
:   In walk-forward testing, a "window" is one learning + exam pair. Multiple windows
    test the strategy across different time periods.
