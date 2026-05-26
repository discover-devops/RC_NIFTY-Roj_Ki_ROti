# RC_NIFTY — Roj Ki Roti

## What This Project Is

This is a **NIFTY index options selling toolkit** built by Rahul (Kumar), running on a personal Ubuntu server (`MyLabServer`). The primary strategy is **Short Iron Condors** on weekly NIFTY expiries, paper-traded on **Sensibull** and monitored via **Kite Connect (Zerodha) API**. The goal is consistent, disciplined income — hence "Roj Ki Roti" (daily bread).

This is NOT auto-execution. All trades are entered manually on Sensibull. The scripts provide analysis, strike selection, and automated hourly monitoring with alerts.

---

## Server & Environment

```
Server:     MyLabServer (Ubuntu)
Path:       /home/kumar/nifty-monitor/
Python:     venv at /home/kumar/nifty-monitor/venv/
Broker:     Zerodha (Kite Connect API)
Paper Trade: Sensibull
Config:     .env file with KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
```

---

## Files & What They Do

### 1. `generate_token.py` — Daily Auth (Run Manually)
Generates a fresh Kite Connect access token each morning before market opens. Requires manual browser login + pasting the request_token. Updates `.env` automatically.

### 2. `dashboard.py` — Market Overview Dashboard
A comprehensive pre-trade dashboard that runs in a 15-minute refresh loop. It provides:
- **Live data**: Nifty spot, VIX, day OHLC, gap analysis
- **Pivot levels**: Classic pivot points (R1-R3, S1-S3) from previous day's HLC
- **OI walls**: Top 3 Call OI (resistance) and Put OI (support) strikes — these are the "smart money" levels
- **Max Pain**: Strike where option writers have minimum loss
- **PCR (Put-Call Ratio)**: Sentiment indicator (>1.2 bullish, <0.8 bearish)
- **Market scenario detection**: Classifies the day as FLAT_QUIET, GAP_UP, GAP_DOWN, VOLATILE, or NORMAL
- **Trade suggestion engine**: Based on scenario + OI walls + VIX, recommends IRON_CONDOR, BEAR_CALL_ONLY, BULL_PUT_ONLY, or SKIP

**Key decision rules in dashboard.py:**
- VIX > 22 → skip (too expensive/volatile)
- VIX < 12 → skip (premiums too thin)
- DTE ≤ 1 → skip (gamma risk)
- Distance to OI wall < 150pts → skip that side
- Gap up → sell calls only (skip put side)
- Gap down → sell puts only (skip call side)

### 3. `iron_condor_setup.py` — Strike Selection Calculator
Run at ~9:20 AM to compute optimal IC strikes. It:
- Fetches live Nifty spot and VIX
- Finds the next tradeable expiry (minimum 2 DTE)
- Calculates 1SD, 1.5SD, and 2SD expected moves using VIX as implied volatility
- Builds three IC structures at each SD level with live premiums
- Computes per-trade economics: net credit, target (50%), stop loss (50%), and Expected Value

**Default parameters:**
- Lot size: 65
- Spread width: 200 points
- Profit target: 50% of credit received
- Stop loss: 50% of credit received
- Minimum DTE: 2 days

**Standing recommendation:** Use **1.5SD** setup (80% win probability, best balance of premium vs safety). Outputs a ready-to-paste `positions.json` entry.

### 4. `iron_condor_monitor.py` — Position Monitor (Legacy)
Earlier version of the monitor. Reads `positions.json`, fetches live premiums for all 4 legs, calculates P&L, and checks triggers. Supports dict-format legs only.

### 5. `monitor.py` — Position Monitor (Current / Production)
The active hourly monitor, run by cron. This is the main monitoring engine.

**What it does every hour (9 AM to 3:30 PM, Mon-Fri):**
- Loads all OPEN trades from `positions.json`
- Fetches live premiums for all 4 legs of each trade
- Calculates per-leg and total P&L
- Checks 5 trigger conditions (see below)
- Writes structured logs to `~/nifty-monitor/logs/ic_log_YYYY-MM-DD.log`
- Appends a CSV row to `daily_tracker.csv` for performance tracking

**Trigger conditions:**
1. **Profit target hit**: P&L ≥ X% of net credit → CLOSE TRADE
2. **Stop loss hit**: P&L ≤ -X% of net credit → EXIT NOW
3. **Spot exit levels**: Spot crosses below/above predefined levels → CLOSE TRADE
4. **Short strike approached**: Spot within 50pts of short PE or CE → MONITOR CLOSELY
5. **2x premium on short leg**: Short leg premium doubles from entry → Consider rolling

**Supports two leg formats:**
- Array format (current): `[{action: "SELL", strike: 23000, option_type: "PE", ...}]`
- Dict format (legacy): `{short_pe: {strike: 23000, premium: 20.4}, ...}`

### 6. `staged_entry.py` — Advanced Staged Entry System
A more sophisticated entry system that layers positions based on OI wall tests + technical confirmation. Runs in a 15-minute refresh loop.

**Core logic:**
- Identifies OI walls (support = high Put OI, resistance = high Call OI)
- Detects when spot is "testing" a wall (within 50pts)
- Waits for **divergence confirmation** on hourly timeframe (RSI or MACD)
  - Support test + bullish divergence → enter Bull Put Spread
  - Resistance test + bearish divergence → enter Bear Call Spread
- Stages up to 3 levels per side (max 6 lots total, 2 lots per entry)
- Level 1: 1000pts beyond the tested wall
- Level 2+: Next OI wall + 200pt buffer

**Parameters:**
- Lots per entry: 2
- Max total lots: 6 (3 levels × 2 lots)
- Spread width: 200pts
- SL: 50% of credit
- Target: 35% of credit
- RSI period: 14

### 7. `oi_snapshot.py` — Quick OI Table
Simple utility to print the option chain (ATM ± 5 strikes) with OI, volume, and LTP for both calls and puts. Also shows total Call/Put OI and PCR.

### 8. `positions.json` — Trade State
Stores all active (and closed) trades. Each trade includes:
- Trade ID, strategy type, expiry, status
- All 4 legs with strikes, premiums, quantities
- Max profit, max loss, breakeven levels, probability of profit
- Entry rules (profit target %, stop loss %, spot exit levels)
- Margin required

**Current format example (array legs):**
```json
{
  "active_trades": [{
    "trade_id": "NIFTY_IC_26MAY_001",
    "strategy": "Short Iron Condor",
    "expiry": "2026-05-26",
    "status": "OPEN",
    "legs": [
      {"action": "SELL", "strike": 23000, "option_type": "PE", "quantity": 260, "premium": 20.40},
      {"action": "BUY",  "strike": 22800, "option_type": "PE", "quantity": 260, "premium": 9.95},
      {"action": "SELL", "strike": 24200, "option_type": "CE", "quantity": 260, "premium": 18.85},
      {"action": "BUY",  "strike": 24400, "option_type": "CE", "quantity": 260, "premium": 8.05}
    ],
    "max_profit": 5525,
    "max_loss": 46475,
    "entry_rules": {
      "profit_target_percent": 35,
      "stop_loss_percent": 100,
      "spot_exit_below": 23000,
      "spot_exit_above": 24200
    }
  }]
}
```

---

## Cron Schedule

```
# Hourly monitoring during market hours (9 AM to 2 PM, Mon-Fri)
0 9,10,11,12,13,14 * * 1-5  python monitor.py >> logs/cron.log 2>&1

# EOD snapshot at 3:30 PM
30 15 * * 1-5  python monitor.py >> logs/cron.log 2>&1
```

---

## Logs

```
~/nifty-monitor/logs/
├── ic_log_YYYY-MM-DD.log   # Detailed hourly position snapshots
├── daily_tracker.csv        # One-row-per-hour CSV (Timestamp, Trade_ID, Nifty, VIX, PnL, PnL_Pct, Alerts)
└── cron.log                 # Raw cron output
```

---

## Daily Workflow

```
1. Before market (8:30-9:00 AM):
   → Run generate_token.py (manual browser login)
   → Check overnight news (crude oil, geopolitics, global markets)

2. Market open analysis (9:15-9:20 AM):
   → Run dashboard.py to assess market scenario
   → Run iron_condor_setup.py at 9:20 AM for strike recommendations

3. Trade decision:
   → Choose: full IC / one side only / skip
   → Enter paper trade on Sensibull
   → Update positions.json with the new trade

4. During market (automated via cron):
   → monitor.py runs hourly, writes logs, tracks P&L
   → Review first hourly log to confirm everything is working

5. EOD (3:30 PM):
   → monitor.py writes final snapshot
   → Review daily_tracker.csv for performance summary
```

---

## How to Prompt Claude for This Project

### Starting a New Thread
Paste this README along with the repo URL: `https://github.com/discover-devops/RC_NIFTY-Roj_Ki_ROti/tree/main`

Then ask Claude to clone the repo so it has full code context.

### Morning Pre-Market
```
Here's my current positions.json: [paste]
Nifty closed at _____, VIX is at _____.
Any overnight news I should worry about? Should I trade today?
```

### Interpreting Setup Output
```
I ran iron_condor_setup.py, here's the output: [paste full output]
Which SD level should I pick today and why?
```

### Monitoring & Alerts
```
Here's the latest hourly log from monitor.py: [paste]
My positions.json is: [paste]
Any action needed — roll, exit, or hold?
```

### Updating Positions
```
I entered this trade on Sensibull:
SELL 24300 CE @ 15.2, BUY 24500 CE @ 6.8
SELL 22900 PE @ 18.5, BUY 22700 PE @ 8.1
Expiry: June 2, Spot: 23580, VIX: 14.2, Quantity: 260
Generate the positions.json entry for me.
```

### Performance Review
```
Here's my daily_tracker.csv from the last 2 weeks: [paste/upload]
How are my iron condors performing? Any patterns? Should I adjust parameters?
```

### Code Changes
```
I want to add [feature] to [script]. Here's what I need: [describe behavior].
```

Examples:
- "Add Telegram alerts when any trigger fires in monitor.py"
- "The staged_entry.py divergence detection gives too many false signals — tune it"
- "Add Greeks tracking (delta exposure) to iron_condor_monitor.py"
- "Create a weekly P&L summary script that reads daily_tracker.csv"

---

## Key Trading Rules Embedded in the Code

1. **Never sell when VIX > 22** (premiums attractive but risk too high)
2. **Never sell when VIX < 12** (premiums too thin to justify risk)
3. **Minimum 2 DTE** to avoid gamma explosion
4. **1.5SD is the default** — 80% probability, decent premium
5. **Short strikes must be beyond OI walls** — smart money has already hedged there
6. **50% profit target, 50-100% stop loss** on credit received
7. **If short strike is touched (within 30-50pts)** — prepare to roll or exit
8. **If short leg premium doubles** — roll the opposite side or exit
9. **Gap up → sell calls only; Gap down → sell puts only** — never fight the trend
10. **Staged entry requires divergence confirmation** — no blind entries on wall tests

---

## Important Notes for Claude

- Claude CANNOT connect to Kite API or the server. All live data must be pasted by the user.
- Claude CAN: analyze outputs, suggest trades, modify code, review performance, update positions.json, check news via web search.
- The scripts use Kite Connect Python SDK (`kiteconnect`). Instrument token for NIFTY is 256265, VIX is 264969.
- NIFTY lot size is 65. Strikes are in 50-point intervals.
- Weekly expiries are on Tuesdays (changed from Thursdays).
- All times are IST (Asia/Kolkata). Market hours: 9:15 AM to 3:30 PM.
- This is paper trading on Sensibull — no real money at risk yet.
