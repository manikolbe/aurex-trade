# Getting Started

This guide walks you through starting your first trading bot. It uses fake money,
so there's nothing to worry about. Takes about 5 minutes.

## What You're About to Do

AurexTrade is a bot that watches gold prices and makes trades based on rules you
choose. Think of it as an autopilot — it follows a plan so you don't have to sit
and watch the market all day.

In this guide, you'll:

1. Connect a free practice account (no real money)
2. Start the bot
3. Watch it make a few trades
4. Stop it whenever you like

By the end, you'll have seen the bot in action and understand how it works at a
basic level.

---

## Step 1: Connect Your Practice Account

Before the bot can watch gold prices, it needs access to market data. You'll
connect a free OANDA practice account — this uses virtual money, so there's zero
financial risk.

!!! tip "One-time setup"
    You only need to do this once. After that, your connection is saved.

### 1a. Create a free OANDA practice account

If you don't already have one, [open a free demo account](https://help.oanda.com/us/en/faqs/open-demo-account.htm).
It gives you virtual money to trade with — like a flight simulator for trading.

!!! warning "Choose the **v20** account type"
    When creating your account, OANDA offers several account types.
    Select **v20**.

### 1b. Get your API Token

Go to [OANDA Hub → Personal Access Token](https://hub.oanda.com/tpa/personal_token)
and generate a token. This is like a password that lets AurexTrade connect to your
practice account. Copy it somewhere safe — you'll paste it in the next step.

### 1c. Get your Account ID

Go to [OANDA Hub → Accounts](https://hub.oanda.com/accounts) and copy your
**Primary** account number. It looks like `101-004-XXXXXXXX-001`.

### 1d. Save your details in AurexTrade

1. Open [AurexTrade](https://aurex.manikolbe.com) in your browser
2. Click **Settings** in the top menu
3. On the **Broker** tab, paste your details:
    - **Account ID** — the number from step 1c
    - **API Token** — the token from step 1b
    - **Server** — select "Practice"
4. Click **Save Credentials**
5. Click **Test Connection** — you should see a green success message

!!! info "Your details are safe"
    Your credentials are encrypted before they're stored. They're never shown
    back to you or shared with anyone.

---

## Step 2: Start Your First Bot

Now for the fun part.

1. Click **Trading Bot** in the top menu
2. You'll see a form on the left and a status panel on the right

The form has some fields already filled in with sensible defaults. **You don't need
to change anything.** Here's what's there:

- **Strategy** — The rules the bot will follow to decide when to buy and sell.
  The default (Ciby Sliding Grid) places hedged buy/sell orders around the current
  price and slides them as the market moves. Don't worry about understanding it
  yet — we'll explain it later.
- **Symbol** — What the bot trades. It's set to gold (XAU/USD).
- **Interval** — How often the bot checks for new opportunities. Default is
  every 60 seconds.

Below the main fields, there's a **Risk Settings** section (collapsed by default).
These are safety limits — like a speed limiter on a car. The defaults are
conservative and safe for practice. You can explore them later.

**When you're ready:**

Click the green **Start Paper Trading** button.

That's it. The bot is now running.

---

## Step 3: Watch It Run

Once started, the page changes to show you what's happening:

- **Status badge** — Shows "Running" in green
- **Cycles** — How many times the bot has checked the market. This ticks up
  every 60 seconds.
- **Signals** — How many times the bot spotted a potential trade
- **Trades** — How many trades it actually made (some signals get blocked by
  the safety limits — that's normal and good)
- **Peak Equity** — The highest your practice balance has reached
- **Uptime** — How long the bot has been running

!!! tip "Be patient"
    The bot only trades when its rules are triggered. It might run for several
    cycles before making its first trade — that's completely normal. It's being
    selective, not broken.

!!! note "This is all practice money"
    Nothing you see here costs real money. The bot is trading with virtual funds
    in your OANDA practice account. You can let it run, stop it, restart it —
    experiment freely.

---

## Step 4: Stop the Bot

Whenever you want to stop:

1. Click the red **Stop Bot** button
2. Confirm when asked

The bot stops immediately. Any open practice trades will be left as they are
in your OANDA practice account.

You can also use the **Kill Switch** (in Risk Settings) as an emergency stop —
it prevents all new trades instantly.

**You are always in control.** The bot only runs when you tell it to, and stops
the moment you ask.

---

## What Just Happened?

Let's recap what the bot did:

1. It watched gold prices every 60 seconds
2. It applied a set of rules (the strategy) to decide: buy, sell, or do nothing
3. Before placing any trade, it checked the safety limits (risk settings)
4. If everything looked safe, it placed the trade in your practice account
5. It repeated this cycle until you stopped it

All with virtual money. No financial risk at all.

---

## What to Do Next

Now that you've seen the bot in action, you might be curious:

**"Why did it buy/sell when it did?"**
:   The [Trading Concepts](trading-concepts.md) page explains how each strategy
    works in plain English — what rules it follows and why.

**"Can I find better settings?"**
:   The [Strategy Testing](strategy-testing.md) page shows you how to test
    different settings against past market data to see which ones perform best.
    This is a more advanced feature — come back to it once you're comfortable
    with the basics.

**"What do all the numbers mean?"**
:   The [Understanding Results](understanding-results.md) page explains every
    metric you'll encounter.

There's no rush. Take your time exploring, and remember — as long as you're using
a practice account, everything is risk-free.
