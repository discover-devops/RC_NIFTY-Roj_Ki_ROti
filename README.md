# RC_NIFTY — Roj Ki Roti

## What This Project Is

A **NIFTY index options selling toolkit** built by Rahul , running on an Ubuntu server (`MyLabServer`). The primary strategy is **Short Iron Condors** on weekly NIFTY expiries, traded on **Sensibull** and monitored via **Kite Connect (Zerodha) API**. The goal is consistent, disciplined weekly income — hence "Roj Ki Roti" (daily bread).

This is NOT auto-execution. All trades are entered manually on Sensibull. The scripts provide analysis, strike selection, automated hourly monitoring with alerts, and performance tracking.

**Current phase:** Live trading at 4 lots (260 qty). Paper trading phase completed successfully with validated triggers and P&L tracking.

**Repo:** `https://github.com/discover-devops/RC_NIFTY-Roj_Ki_ROti/tree/main`

---

## Server & Environment

```
Server:       MyLabServer (Ubuntu)
Path:         /home/kumar/nifty-monitor/
Python:       venv at /home/kumar/nifty-monitor/venv/
Broker:       Zerodha (Kite Connect API)
Trading:      Sensibull (manual entry)
Config:       .env file with KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
Nifty Token:  256265
VIX Token:    264969
Lot Size:     65
```

---

## Files & What They Do

### 1. `generate_token.py` — Daily Auth (Run Manually Every Morning)

Generates a fresh Kite Connect access token. Requires manual browser login + pasting the request_token. Updates `.env` automatically.

```bash
cd ~/nifty-monitor
source venv/bin/activate
python generate_token.py
# → Opens browser URL, you login, paste the request_token
```

### 2. `dashboard.py` — Market Overview Dashboard

Pre-trade dashboard running in a 15-minute refresh loop. Provides live data (Nifty spot, VIX, day OHLC, gap analysis), pivot levels (R1-R3, S1-S3), OI walls (top 3 Call/Put OI strikes), Max Pain, PCR, market scenario detection (FLAT_QUIET / GAP_UP / GAP_DOWN / VOLATILE / NORMAL), and a trade suggestion engine.

Key decision rules baked into dashboard.py: VIX > 22 → skip; VIX < 12 → skip (premiums thin); DTE ≤ 1 → skip (gamma risk); distance to OI wall < 150pts → skip that side; gap up → sell calls only; gap down → sell puts only.

```bash
python dashboard.py
# Runs continuously, refreshes every 15 min. Ctrl+C to exit.
```

### 3. `iron_condor_setup.py` — Strike Selection Calculator

Run at ~9:20 AM to compute optimal IC strikes. Fetches live Nifty and VIX, finds next tradeable expiry (min 2 DTE), calculates 1SD / 1.5SD / 2SD expected moves using VIX as IV, builds IC structures at each level with live premiums, and computes economics (net credit, target, SL, Expected Value).

Default parameters: lot size 65, spread width 200pts, profit target 50% of credit, stop loss 50% of credit, min DTE 2 days. Standing recommendation is 1.5SD (80% win probability).

```bash
python iron_condor_setup.py
# Outputs strike recommendations + ready-to-paste positions.json entry
```

### 4. `monitor.py` — Position Monitor (v2, Production)

The main monitoring engine, run hourly by cron. This is the **v2 version** with significant improvements over the original.

**What it does every hour (9 AM to 3:30 PM, Mon-Fri):**
Loads OPEN trades from `positions.json`, fetches live premiums for all 4 legs, calculates per-leg and total P&L, checks trigger conditions, writes structured logs, and appends CSV tracking data.

**v2 improvements over original:**

**Instruments caching** — old code called `kite.instruments("NFO")` 4 times per trade per hour (full download of ~100K+ instruments each time). v2 caches once, reuses for 5 minutes. Cuts API calls from 8 to 2 per run.

**Batch quote fetching** — old code made 4 separate `kite.quote()` calls per trade. v2's `get_all_leg_ltps()` fetches all 4 legs in one call. 4 API hits reduced to 1 per trade.

**Escalating profit target alerts** — instead of one generic "CLOSE TRADE" message, it escalates based on how far past target you are:
- At 35-50%: 🟢 standard close alert
- At 50-80%: 🟢🟢 warning that you're risking unrealized gains
- At 80%+: 🟢🟢🟢 urgent "CLOSE NOW — you are risking ₹X of unrealized profit"

**VIX spike detection** — alerts when VIX jumps 25%+ from entry VIX, or crosses 20. Requires `vix_at_entry` field in positions.json.

**DTE countdown** — warns "EXPIRY DAY: Close all positions before 3:15 PM" on expiry day, and "gamma risk elevated" with 1 day to expiry.

**All 8 trigger conditions:**
1. Profit target hit (with 3-tier escalation)
2. Stop loss hit
3. Spot exit levels breached
4. Short strike approached (within 50pts)
5. Short leg premium doubled (2x entry)
6. VIX spike (25%+ from entry or VIX > 20)
7. Expiry day warning
8. 1-DTE gamma warning

**Supports two leg formats in positions.json:**
- Array format (current): `[{action: "SELL", strike: 23000, option_type: "PE", ...}]`
- Dict format (legacy): `{short_pe: {strike: 23000, premium: 20.4}, ...}`

### 5. `trade_journal.py` — Trade Journal & Performance Tracker

Records closed trades with actual exit data, calculates realized P&L, tracks win rate and performance metrics, and provides a scale-up readiness check.

**Commands:**

```bash
# Record a closed trade (interactive — picks up open trades from positions.json)
python trade_journal.py close
# → Select the trade, enter exit premiums, it auto-calculates P&L
# → Automatically marks the trade CLOSED in positions.json

# Show full performance history
python trade_journal.py summary
# → Win rate, total P&L, avg win/loss, profit factor, max drawdown, streaks, trade log

# This week's trades only
python trade_journal.py weekly

# Scale-up readiness check (checks 5 criteria on last 4 trades)
python trade_journal.py go_nogo
# → Win rate ≥ 70%?
# → Net positive P&L?
# → No 2 consecutive losses?
# → Avg win > Avg loss?
# → SL discipline maintained?
# → All pass = green light to add 2 more lots
```

**Journal data stored in:** `~/nifty-monitor/trade_journal.json`

**Example workflow after closing a trade:**
```bash
# 1. Close the trade on Sensibull
# 2. Record it in the journal:
python trade_journal.py close
# → Select trade, enter exit premiums
# → Automatically calculates realized P&L
# → Marks positions.json as CLOSED

# 3. Check weekly performance:
python trade_journal.py weekly

# 4. After 4+ trades, check if ready to scale:
python trade_journal.py go_nogo
```

### 6. `iron_condor_monitor.py` — Position Monitor (Legacy)

Earlier version of the monitor. Superseded by `monitor.py` v2. Kept for reference only. Does not have instruments caching, batch fetching, VIX alerts, or DTE warnings.

### 7. `staged_entry.py` — Advanced Staged Entry System

Layers positions based on OI wall tests + technical divergence confirmation. Runs in a 15-minute refresh loop.

Core logic: identifies OI walls, detects when spot is "testing" a wall (within 50pts), waits for divergence confirmation on hourly timeframe (RSI or MACD), then suggests Bull Put Spreads (support test + bullish divergence) or Bear Call Spreads (resistance test + bearish divergence). Stages up to 3 levels per side (max 6 lots, 2 per entry).

Parameters: lots per entry 2, max total 6, spread width 200pts, SL 50%, target 35%, RSI period 14.

```bash
python staged_entry.py
# Runs continuously, refreshes every 15 min. Ctrl+C to exit.
```

### 8. `oi_snapshot.py` — Quick OI Table

Prints option chain (ATM ± 5 strikes) with OI, volume, and LTP. Shows total Call/Put OI and PCR.

```bash
python oi_snapshot.py
```

### 9. `positions.json` — Trade State File

Stores all active and closed trades. Each trade includes trade ID, strategy, expiry, status, all 4 legs with strikes/premiums/quantities, max profit, max loss, breakeven levels, POP, entry rules, and margin.

**Required fields for v2 monitor** (include when creating new entries):
```json
{
  "active_trades": [{
    "trade_id": "NIFTY_IC_27MAY_001",
    "strategy": "Short Iron Condor",
    "symbol": "NIFTY",
    "expiry": "2026-06-02",
    "status": "OPEN",
    "market_view": "Neutral",
    "entry_spot": 23935,
    "vix_at_entry": 16.06,

    "legs": [
      {"action": "BUY",  "strike": 22800, "option_type": "PE", "quantity": 260, "premium": 9.95},
      {"action": "SELL", "strike": 23000, "option_type": "PE", "quantity": 260, "premium": 20.40},
      {"action": "SELL", "strike": 24200, "option_type": "CE", "quantity": 260, "premium": 18.85},
      {"action": "BUY",  "strike": 24400, "option_type": "CE", "quantity": 260, "premium": 8.05}
    ],

    "max_profit": 5525,
    "max_loss": 46475,
    "breakeven_low": 22979,
    "breakeven_high": 24221,
    "pop": 83,
    "margin_required": 293000,

    "entry_rules": {
      "profit_target_percent": 35,
      "stop_loss_percent": 100,
      "spot_exit_below": 23000,
      "spot_exit_above": 24200
    },

    "notes": "4 lots live. First live trade."
  }]
}
```

**Important:** `vix_at_entry` is new and required for VIX spike alerts in monitor v2. Always include it.

### 10. `trade_journal.json` — Closed Trade History

Auto-managed by `trade_journal.py`. Contains all closed trades with entry/exit data, realized P&L, exit reason, and metadata. Do not edit manually.

---

## Cron Schedule

```bash
# Hourly monitoring during market hours (9 AM to 2 PM, Mon-Fri)
0 9,10,11,12,13,14 * * 1-5 cd /home/kumar/nifty-monitor && /home/kumar/nifty-monitor/venv/bin/python /home/kumar/nifty-monitor/monitor.py >> /home/kumar/nifty-monitor/logs/cron.log 2>&1

# EOD snapshot at 3:30 PM
30 15 * * 1-5 cd /home/kumar/nifty-monitor && /home/kumar/nifty-monitor/venv/bin/python /home/kumar/nifty-monitor/monitor.py >> /home/kumar/nifty-monitor/logs/cron.log 2>&1
```

---

## Logs & Data Files

```
~/nifty-monitor/
├── monitor.py              # v2 production monitor
├── trade_journal.py        # Trade journal & performance tracker
├── iron_condor_setup.py    # Strike selection calculator
├── dashboard.py            # Pre-trade market dashboard
├── staged_entry.py         # Staged entry system
├── oi_snapshot.py          # Quick OI table
├── generate_token.py       # Daily auth token
├── positions.json          # Active/closed trade state
├── trade_journal.json      # Closed trade history (auto-managed)
├── .env                    # API credentials
└── logs/
    ├── ic_log_YYYY-MM-DD.log   # Detailed hourly position snapshots
    ├── daily_tracker.csv        # CSV: Timestamp, Trade_ID, Nifty, VIX, PnL, PnL_Pct, Alerts
    └── cron.log                 # Raw cron output
```

---

## Daily Workflow

### Morning (8:30–9:20 AM)
```bash
cd ~/nifty-monitor && source venv/bin/activate

# Step 1: Generate fresh token
python generate_token.py

# Step 2: Ask Claude for overnight news check
# (paste in Claude chat: "Market is open. Nifty prev close: _____, VIX: _____. Any risks?")

# Step 3: Run dashboard for market scenario
python dashboard.py          # Ctrl+C after first print

# Step 4: Run IC setup at 9:20 AM
python iron_condor_setup.py  # Paste output in Claude chat for strike review
```

### Trade Entry (9:20–9:45 AM)
```
1. Choose: full IC / one side only / skip (based on Claude's advice + setup output)
2. Enter trade on Sensibull (4 lots = 260 qty for each leg)
3. Update positions.json with new trade entry (include vix_at_entry!)
4. Verify cron is running: crontab -l
```

### During Market (Automated)
```
- monitor.py runs hourly via cron
- Check logs: tail -f ~/nifty-monitor/logs/ic_log_$(date +%Y-%m-%d).log
- When 🟢 TARGET HIT appears → close on Sensibull within 30 minutes
- When 🔴 STOP LOSS HIT appears → close IMMEDIATELY
```

### After Closing a Trade
```bash
# Record in journal (auto-calculates P&L, marks positions.json CLOSED)
python trade_journal.py close

# Check performance
python trade_journal.py summary

# Weekly review
python trade_journal.py weekly

# Ready to scale? (after 4+ trades)
python trade_journal.py go_nogo
```

### EOD (3:30 PM)
```
- monitor.py writes final snapshot automatically
- Review daily_tracker.csv if needed
```

---

## Trading Parameters (Current)

| Parameter | Value | Where Set |
|-----------|-------|-----------|
| Lot size | 65 | All scripts |
| Lots per trade | 4 (260 qty) | positions.json |
| Spread width | 200 pts | iron_condor_setup.py |
| SD level | 1.5SD (recommended) | iron_condor_setup.py |
| Profit target | 35% of credit | positions.json entry_rules |
| Stop loss | 100% of credit | positions.json entry_rules |
| Min DTE | 2 days | iron_condor_setup.py |
| Weekly target | ₹7,000–8,500 | 35% of ~₹20K–24K credit |
| Margin needed | ~₹3L for 4 lots | Zerodha |

---

## Key Trading Rules

1. **Never sell when VIX > 22** — premiums attractive but risk too high
2. **Never sell when VIX < 12** — premiums too thin to justify risk
3. **Minimum 2 DTE** — avoid gamma explosion near expiry
4. **1.5SD is the default** — 80% probability of profit, decent premium
5. **Short strikes must be beyond OI walls** — smart money has hedged there
6. **35% profit target** — when the monitor says CLOSE, close within 30 minutes
7. **100% stop loss** — if credit was ₹22K, exit at -₹22K loss, no questions
8. **If short strike touched (within 50pts)** — prepare to roll or exit
9. **If short leg premium doubles** — roll opposite side or exit the trade
10. **Gap up → sell calls only; Gap down → sell puts only** — never fight the trend
11. **Expiry day** — close all positions before 3:15 PM
12. **VIX spikes 25%+ from entry** — tighten SL or close if profitable

---

## Scaling Plan

| Phase | Lots | Margin | Weekly Target | Criteria to Advance |
|-------|------|--------|---------------|---------------------|
| ✅ Done | 1 (paper) | — | ₹2K–3K | System validation |
| Current | 4 (live) | ~₹3L | ₹7K–8.5K | 4 trades, 70%+ win rate |
| Next | 6 | ~₹4.5L | ₹10K–13K | `go_nogo` check passes |
| Target | 8 | ~₹6L | ₹15K–20K | Consistent 70%+ over 8 weeks |

Use `python trade_journal.py go_nogo` after every 4 trades to check readiness.

---

## How to Prompt Claude for This Project

### Starting a New Thread
Paste this README along with the repo URL. Then ask Claude to clone the repo for full code context. Claude acts as a **trusted trading advisor** — it understands the code, the strategy, and the risk management. It reviews outputs, recommends strikes, tracks performance, and modifies code.

### Morning Pre-Market
```
Market is open. Nifty prev close: _____, VIX: _____.
Here's my positions.json: [paste]
Any overnight news I should worry about? Should I trade today?
```

### Interpreting Setup Output
```
I ran iron_condor_setup.py, here's the output: [paste full output]
Which SD level for today? Full IC or one side only?
```

### Monitoring & Alerts
```
Here's the latest hourly log from monitor.py: [paste]
Any action needed — roll, exit, or hold?
```

### Generating positions.json Entry
```
I entered this trade on Sensibull:
SELL 24300 CE @ 15.2, BUY 24500 CE @ 6.8
SELL 22900 PE @ 18.5, BUY 22700 PE @ 8.1
Expiry: June 2, Spot: 23580, VIX: 14.2, Quantity: 260 (4 lots)
Generate the positions.json entry for me.
```

### Performance Review
```
Here's my trade_journal.py summary output: [paste]
And daily_tracker.csv: [paste/upload]
How am I doing? Any patterns? Should I adjust parameters?
```

### Code Changes
```
I want to add [feature] to [script]. Here's what I need: [describe behavior].
```

---

## Important Notes for Claude

- Claude CANNOT connect to Kite API or the server. All live data must be pasted by the user.
- Claude CAN: analyze outputs, recommend strikes, modify code, review performance, generate positions.json entries, check news via web search, and provide risk assessments.
- Claude's role: **Trusted trading advisor.** Not a trading desk (can't execute), not a financial advisor (not licensed). A knowledgeable co-pilot who helps make informed decisions.
- NIFTY lot size is 65. Strikes are in 50-point intervals. Weekly expiries on Tuesdays.
- All times IST (Asia/Kolkata). Market hours: 9:15 AM – 3:30 PM.
- The `vix_at_entry` field in positions.json is required for v2 monitor VIX spike alerts.
- When in doubt, Claude should recommend the conservative action (close early, skip if uncertain, take the wider strikes).

---

## Trade History

| Date | Trade ID | Strategy | P&L | % of Credit | Exit Reason | Result |
|------|----------|----------|-----|-------------|-------------|--------|
| 2026-05-26 | NIFTY_IC_26MAY_001 | Short Iron Condor | +₹5,486 | +99.3% | Expiry | ✅ WIN |

*Updated via `python trade_journal.py summary`*
