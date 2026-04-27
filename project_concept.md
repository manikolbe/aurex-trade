# PROJECT BRIEF: aurexTrade — Gold Trading Bot (IBKR, Python)

## 1) Objectives

Build **aurexTrade**, a single-user automated trading bot that:

* Trades **gold** via Interactive Brokers (IBKR)
* Uses **rule-based strategies** (no AI initially)
* Starts with **paper trading**, with a path to **live trading**
* Is **safe, observable, and deterministic**

---

## 2) MVP Scope (Strict)

The MVP of **aurexTrade** must:

* Be **single-user only**
* Run as a **single-process Python application**
* Support:

  * local development
  * IBKR paper trading
* Include:

  * strategy
  * risk engine
  * execution
  * logging
  * SQLite persistence

The MVP must NOT include:

* UI/frontend
* multi-user support
* remote access
* cloud-native architecture
* distributed systems
* AI/ML

---

## 3) Long-Term Vision (Guiding Design, Not Implementation)

### A. VPS-Hosted aurexTrade System

* aurexTrade runs **24/7 on a VPS**
* IB Gateway runs alongside it
* System is **always-on and reliable**

---

### B. Remote Access (Future)

* Users connect to **aurexTrade securely over the internet**
* Likely via:

  * REST API
  * or lightweight web interface

Users will be able to:

* start/stop strategies
* view trades and logs
* configure parameters

---

### C. Multi-User Platform (Future)

aurexTrade evolves into a multi-user system where:

* Multiple users are supported
* Each user has:

  * isolated strategies
  * separate portfolio state
  * independent risk limits

Isolation model:

```text id="u7sm9m"
user → strategies → trades → positions
```

---

### D. User Interface (Future)

* Web-based dashboard for aurexTrade
* Provides:

  * trade history
  * system status
  * configuration controls

---

### E. Live Trading Capability

* Fully automated execution with real capital
* Strong safeguards:

  * risk limits
  * kill switches
  * monitoring

---

## 4) Design Constraints from Long-Term Vision

Even in MVP, aurexTrade should:

* Avoid hardcoding IBKR across all logic

* Keep:

  * strategy
  * risk
  * execution
    loosely coupled

* Allow future introduction of:

  * user context (`user_id`)
  * API layer
  * multiple brokers

Do NOT implement these now — just **do not block them**.

---

## 5) Core System (MVP)

### High-Level Flow

```text id="i4g6vx"
Market Data → Strategy → Risk → Execution → Broker → Persistence
```

---

### Main Loop

```python id="mbrc0j"
while running:
    data = fetch_market_data()

    signal = strategy.generate(data)

    decision = risk.evaluate(signal, state)

    if decision.approved:
        execution.place_order(signal)

    persist(signal, decision, result)

    sleep(interval)
```

---

## 6) Core Components

### Strategy

* Deterministic signal generation

---

### Risk Engine (Mandatory)

Must enforce:

* max position size
* max daily loss
* trade frequency limits
* kill switch

---

### Execution Layer

* Handles trade placement
* Supports:

  * paper execution
  * IBKR execution

---

### Broker Integration

* Use `ib_insync`
* Connect to IBKR (TWS / IB Gateway)
* Handle:

  * reconnects
  * failures
  * timeouts

---

### Persistence

* SQLite database
* Stores:

  * signals
  * decisions
  * trades
  * positions
  * errors

---

### Logging

* File-based logs
* Log all:

  * inputs
  * outputs
  * decisions
  * errors

---

## 7) Operating Modes

```text id="6xf9o3"
TRADING_MODE=local | paper | live
```

* `local` → no broker
* `paper` → IBKR paper account
* `live` → real trading (disabled by default)

---

## 8) Configuration

Use `.env`:

```text id="j8y3h0"
APP_NAME=aurexTrade
SYMBOL=GOLD
INTERVAL_SECONDS=60
MAX_DAILY_LOSS=...
MAX_POSITION_SIZE=...
KILL_SWITCH=false
```

---

## 9) IBKR Requirements

* Use paper account initially
* TWS or IB Gateway must be running
* API enabled with localhost access

---

## 10) Development Workflow

1. Build locally
2. Implement core loop
3. Add tests (strategy + risk)
4. Run dry mode
5. Integrate IBKR paper account
6. Stabilise behaviour

---

## 11) Deployment (Future Phase)

* VPS-based hosting for aurexTrade
* IB Gateway running continuously
* Bot runs as background service
* Logs + DB persist on disk
* CI/CD via GitHub

---

## 12) Security Considerations (Future)

* Secure remote access (API auth)
* Protect API keys
* Encrypted communication
* Per-user isolation

---

## 13) Safety Requirements (Non-Negotiable)

* No live trading by default
* Risk engine must gate all trades
* Full logging of decisions
* Immediate stop capability (kill switch)

---

## 14) Acceptance Criteria (MVP)

* aurexTrade runs continuously
* Connects to IBKR paper account
* Fetches gold price
* Generates signals
* Applies risk checks
* Executes paper trades
* Logs everything

---

## 15) First Milestones

1. IBKR connection + price fetch
2. Basic strategy
3. Risk engine
4. Execution (paper)
5. Persistence + logging
6. Full loop running

---

## 16) Implementation Freedom

The coding agent may:

* choose module structure
* refactor as needed
* improve robustness

But must:

* keep system simple
* avoid overengineering
* respect safety constraints

---

## End of Brief — aurexTrade

