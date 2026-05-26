"""
trade_journal.py — Trade Journal & Performance Tracker
=======================================================
Records closed trades with actual exit data, tracks win rate,
average P&L, drawdown, and gives go/no-go signals for scaling.

Usage:
  python trade_journal.py close          → Interactive: record a closed trade
  python trade_journal.py summary        → Show performance summary
  python trade_journal.py weekly         → This week's summary
  python trade_journal.py go_nogo        → Scale-up readiness check
"""

import os
import sys
import json
import csv
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

BASE_DIR = os.path.expanduser("~/nifty-monitor")
JOURNAL_FILE = os.path.join(BASE_DIR, "trade_journal.json")
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")


def load_journal():
    if os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, 'r') as f:
            return json.load(f)
    return {"closed_trades": [], "metadata": {"created": str(datetime.now(IST))}}


def save_journal(journal):
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(journal, f, indent=2, default=str)
    print(f"✓ Journal saved to {JOURNAL_FILE}")


def close_trade():
    """Record a closed trade with actual exit data."""
    journal = load_journal()

    # Load current positions to find the trade
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, 'r') as f:
            positions = json.load(f)
        open_trades = [t for t in positions.get('active_trades', []) if t.get('status') == 'OPEN']
    else:
        open_trades = []

    if open_trades:
        print("\n--- OPEN TRADES ---")
        for i, t in enumerate(open_trades):
            print(f"  [{i+1}] {t['trade_id']} | {t['strategy']} | Expiry: {t['expiry']}")
        print(f"  [0] Enter manually")

        choice = input("\nWhich trade to close? ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(open_trades):
            trade = open_trades[int(choice) - 1]
            trade_id = trade['trade_id']
            entry_data = trade
        else:
            trade_id = input("Trade ID: ").strip()
            entry_data = None
    else:
        trade_id = input("Trade ID: ").strip()
        entry_data = None

    print(f"\n--- RECORDING EXIT FOR: {trade_id} ---")

    # Exit details
    exit_date = input(f"Exit date (YYYY-MM-DD) [{datetime.now(IST).strftime('%Y-%m-%d')}]: ").strip()
    if not exit_date:
        exit_date = datetime.now(IST).strftime('%Y-%m-%d')

    exit_time = input(f"Exit time (HH:MM) [{datetime.now(IST).strftime('%H:%M')}]: ").strip()
    if not exit_time:
        exit_time = datetime.now(IST).strftime('%H:%M')

    exit_spot = float(input("Nifty spot at exit: ").strip())
    exit_vix = float(input("VIX at exit: ").strip())

    print("\nExit premiums (what you closed at):")
    exit_short_pe = float(input("  Short PE exit premium: ").strip())
    exit_long_pe = float(input("  Long PE exit premium: ").strip())
    exit_short_ce = float(input("  Short CE exit premium: ").strip())
    exit_long_ce = float(input("  Long CE exit premium: ").strip())

    exit_reason = input("\nExit reason (target/sl/manual/expiry): ").strip().upper()

    # Calculate actual P&L
    if entry_data:
        legs = entry_data['legs']
        if isinstance(legs, list):
            # Array format
            entry_prems = {}
            for leg in legs:
                opt_type = leg['option_type'].lower()
                action = leg['action'].upper()
                key = f"{'short' if action == 'SELL' else 'long'}_{opt_type}"
                entry_prems[key] = {'premium': leg['premium'], 'quantity': leg['quantity']}
        else:
            entry_prems = {k: {'premium': v['premium'], 'quantity': v.get('quantity', 65)}
                          for k, v in legs.items()}

        qty = entry_prems['short_pe']['quantity']

        # P&L: short legs gain when premium drops, long legs gain when premium rises
        pnl_short_pe = (entry_prems['short_pe']['premium'] - exit_short_pe) * qty
        pnl_long_pe  = (exit_long_pe - entry_prems['long_pe']['premium']) * qty
        pnl_short_ce = (entry_prems['short_ce']['premium'] - exit_short_ce) * qty
        pnl_long_ce  = (exit_long_ce - entry_prems['long_ce']['premium']) * qty
        realized_pnl = pnl_short_pe + pnl_long_pe + pnl_short_ce + pnl_long_ce

        net_credit = entry_data.get('max_profit', 0)
        pnl_pct = (realized_pnl / net_credit * 100) if net_credit else 0
    else:
        realized_pnl = float(input("\nTotal realized P&L (₹): ").strip())
        net_credit = float(input("Original net credit (₹): ").strip())
        pnl_pct = (realized_pnl / net_credit * 100) if net_credit else 0
        qty = int(input("Quantity per leg: ").strip())

    # Build journal entry
    journal_entry = {
        "trade_id": trade_id,
        "exit_date": exit_date,
        "exit_time": exit_time,
        "exit_spot": exit_spot,
        "exit_vix": exit_vix,
        "exit_premiums": {
            "short_pe": exit_short_pe,
            "long_pe": exit_long_pe,
            "short_ce": exit_short_ce,
            "long_ce": exit_long_ce
        },
        "exit_reason": exit_reason,
        "realized_pnl": round(realized_pnl, 2),
        "net_credit": net_credit,
        "pnl_percent": round(pnl_pct, 1),
        "quantity": qty,
        "lots": qty // 65,
        "is_win": realized_pnl > 0,
        "recorded_at": str(datetime.now(IST))
    }

    # Copy entry data if available
    if entry_data:
        journal_entry["entry_date"] = entry_data.get("entry_date", "")
        journal_entry["entry_spot"] = entry_data.get("entry_spot", 0)
        journal_entry["strategy"] = entry_data.get("strategy", "")
        journal_entry["expiry"] = entry_data.get("expiry", "")
        journal_entry["sd_level"] = entry_data.get("sd_level", "")

    journal["closed_trades"].append(journal_entry)
    save_journal(journal)

    # Mark trade as CLOSED in positions.json
    if entry_data and os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, 'r') as f:
            positions = json.load(f)
        for t in positions.get('active_trades', []):
            if t['trade_id'] == trade_id:
                t['status'] = 'CLOSED'
                t['exit_date'] = exit_date
                t['realized_pnl'] = round(realized_pnl, 2)
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2, default=str)
        print(f"✓ positions.json updated — {trade_id} marked CLOSED")

    # Print summary
    result = "WIN ✅" if realized_pnl > 0 else "LOSS ❌"
    print(f"\n{'='*60}")
    print(f"  {result}: ₹{realized_pnl:+,.0f} ({pnl_pct:+.1f}% of credit)")
    print(f"  Reason: {exit_reason}")
    print(f"{'='*60}\n")


def print_summary():
    """Show overall performance summary."""
    journal = load_journal()
    trades = journal.get("closed_trades", [])

    if not trades:
        print("\nNo closed trades recorded yet.")
        return

    total = len(trades)
    wins = [t for t in trades if t.get('is_win')]
    losses = [t for t in trades if not t.get('is_win')]

    win_rate = len(wins) / total * 100 if total else 0
    total_pnl = sum(t['realized_pnl'] for t in trades)
    avg_win = sum(t['realized_pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['realized_pnl'] for t in losses) / len(losses) if losses else 0

    # Max drawdown (consecutive losses)
    max_dd = 0
    current_dd = 0
    for t in trades:
        if t['realized_pnl'] < 0:
            current_dd += t['realized_pnl']
            max_dd = min(max_dd, current_dd)
        else:
            current_dd = 0

    # Win/Loss streak
    current_streak = 0
    streak_type = None
    for t in reversed(trades):
        if streak_type is None:
            streak_type = 'W' if t['is_win'] else 'L'
            current_streak = 1
        elif (t['is_win'] and streak_type == 'W') or (not t['is_win'] and streak_type == 'L'):
            current_streak += 1
        else:
            break

    # Profit factor
    gross_profit = sum(t['realized_pnl'] for t in wins)
    gross_loss = abs(sum(t['realized_pnl'] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    print(f"\n{'='*70}")
    print(f"  TRADE JOURNAL — PERFORMANCE SUMMARY")
    print(f"{'='*70}")
    print(f"\n  Total Trades:   {total}")
    print(f"  Wins:           {len(wins)} ({win_rate:.0f}%)")
    print(f"  Losses:         {len(losses)} ({100-win_rate:.0f}%)")
    print(f"\n  Total P&L:      ₹{total_pnl:+,.0f}")
    print(f"  Avg Win:        ₹{avg_win:+,.0f}")
    print(f"  Avg Loss:       ₹{avg_loss:+,.0f}")
    print(f"  Profit Factor:  {profit_factor:.2f}")
    print(f"\n  Max Drawdown:   ₹{max_dd:,.0f}")
    print(f"  Current Streak: {current_streak} {'wins' if streak_type == 'W' else 'losses'}")

    print(f"\n  --- TRADE LOG ---")
    print(f"  {'Date':<12} {'Trade ID':<25} {'P&L':>10} {'%':>8} {'Reason':<10}")
    print(f"  {'-'*65}")
    for t in trades:
        marker = "✅" if t['is_win'] else "❌"
        print(f"  {t['exit_date']:<12} {t['trade_id']:<25} ₹{t['realized_pnl']:>+8,.0f} "
              f"{t['pnl_percent']:>+6.1f}% {t['exit_reason']:<10} {marker}")

    print(f"\n{'='*70}\n")


def weekly_summary():
    """Show this week's trades."""
    journal = load_journal()
    trades = journal.get("closed_trades", [])

    today = datetime.now(IST).date()
    week_start = today - timedelta(days=today.weekday())  # Monday

    week_trades = [
        t for t in trades
        if datetime.strptime(t['exit_date'], '%Y-%m-%d').date() >= week_start
    ]

    if not week_trades:
        print(f"\nNo closed trades this week (since {week_start}).")
        return

    total_pnl = sum(t['realized_pnl'] for t in week_trades)
    wins = sum(1 for t in week_trades if t['is_win'])

    print(f"\n{'='*60}")
    print(f"  WEEKLY SUMMARY — Week of {week_start}")
    print(f"{'='*60}")
    print(f"  Trades: {len(week_trades)} | Wins: {wins} | Losses: {len(week_trades) - wins}")
    print(f"  Week P&L: ₹{total_pnl:+,.0f}")

    for t in week_trades:
        marker = "✅" if t['is_win'] else "❌"
        print(f"    {t['exit_date']} {t['trade_id']}: ₹{t['realized_pnl']:>+8,.0f} ({t['exit_reason']}) {marker}")

    print(f"\n{'='*60}\n")


def go_nogo_check():
    """Scale-up readiness assessment."""
    journal = load_journal()
    trades = journal.get("closed_trades", [])

    print(f"\n{'='*70}")
    print(f"  SCALE-UP READINESS CHECK")
    print(f"{'='*70}")

    if len(trades) < 4:
        print(f"\n  ❌ NOT READY: Only {len(trades)} trades recorded. Need minimum 4.")
        print(f"     Complete at least 4 weekly trades before scaling up.")
        print(f"\n{'='*70}\n")
        return

    # Last 4 trades analysis
    recent = trades[-4:]
    recent_wins = sum(1 for t in recent if t['is_win'])
    recent_pnl = sum(t['realized_pnl'] for t in recent)
    win_rate = recent_wins / len(recent) * 100

    # Check criteria
    checks = {
        "Win rate ≥ 70% (last 4)": win_rate >= 70,
        "Net positive P&L (last 4)": recent_pnl > 0,
        "No 2 consecutive losses": not has_consecutive_losses(recent, 2),
        "Avg win > Avg loss (magnitude)": check_win_loss_ratio(recent),
        "All trades followed SL discipline": check_sl_discipline(recent),
    }

    all_pass = all(checks.values())

    for check, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"\n  {status} {check}")

    print(f"\n  {'─'*50}")
    print(f"  Last 4 trades: {recent_wins}W / {4-recent_wins}L | P&L: ₹{recent_pnl:+,.0f}")

    if all_pass:
        current_lots = recent[-1].get('lots', 1)
        print(f"\n  ✅ ALL CLEAR — You can scale from {current_lots} to {current_lots + 2} lots.")
        print(f"     New margin needed: ~₹{(current_lots + 2) * 75000:,}")
    else:
        print(f"\n  ⏳ NOT YET — Fix the failing criteria before scaling up.")
        print(f"     Continue at current lot size for 2 more weeks.")

    print(f"\n{'='*70}\n")


def has_consecutive_losses(trades, n):
    count = 0
    for t in trades:
        if not t['is_win']:
            count += 1
            if count >= n:
                return True
        else:
            count = 0
    return False


def check_win_loss_ratio(trades):
    wins = [t['realized_pnl'] for t in trades if t['is_win']]
    losses = [abs(t['realized_pnl']) for t in trades if not t['is_win']]
    if not wins:
        return False
    if not losses:
        return True
    return (sum(wins) / len(wins)) > (sum(losses) / len(losses))


def check_sl_discipline(trades):
    """Check if all losses were within SL limits (not blown past)."""
    for t in trades:
        if not t['is_win']:
            # A loss beyond -120% of credit suggests SL was not respected
            if t['pnl_percent'] < -120:
                return False
    return True


def main():
    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  python trade_journal.py close    — Record a closed trade")
        print("  python trade_journal.py summary  — Overall performance")
        print("  python trade_journal.py weekly   — This week's trades")
        print("  python trade_journal.py go_nogo  — Scale-up readiness check")
        return

    command = sys.argv[1].lower()

    if command == 'close':
        close_trade()
    elif command == 'summary':
        print_summary()
    elif command == 'weekly':
        weekly_summary()
    elif command in ('go_nogo', 'gonogo', 'go'):
        go_nogo_check()
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
