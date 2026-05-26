import os
import json
from datetime import datetime
import pytz
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

IST = pytz.timezone('Asia/Kolkata')
NIFTY_TOKEN = 256265
VIX_TOKEN = 264969
LOT_SIZE = 65

LOG_DIR = os.path.expanduser("~/nifty-monitor/logs")
POSITIONS_FILE = os.path.expanduser("~/nifty-monitor/positions.json")


def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def get_live_data():
    try:
        quotes = kite.quote([NIFTY_TOKEN, VIX_TOKEN])
        return {
            'nifty': quotes[str(NIFTY_TOKEN)]['last_price'],
            'vix': quotes[str(VIX_TOKEN)]['last_price']
        }
    except Exception as e:
        return None


def get_strike_premium(expiry, strike, opt_type):
    try:
        instruments = kite.instruments("NFO")
        match = [
            i for i in instruments
            if i['name'] == 'NIFTY'
            and str(i['expiry']) == str(expiry)
            and int(i['strike']) == strike
            and i['instrument_type'] == opt_type
        ]
        if not match:
            return 0
        symbol = f"NFO:{match[0]['tradingsymbol']}"
        quote = kite.quote([symbol])
        return quote[symbol].get('last_price', 0)
    except Exception:
        return 0


def calculate_pnl(trade, live_premiums):
    """Calculate current P&L for the trade"""
    legs = trade['legs']
    lots = trade['lots']

    # Short legs: gained value lost (entry - current)
    pe_short_pnl = (legs['short_pe']['premium'] - live_premiums['short_pe']) * LOT_SIZE * lots
    ce_short_pnl = (legs['short_ce']['premium'] - live_premiums['short_ce']) * LOT_SIZE * lots

    # Long legs: current value gained (current - entry)
    pe_long_pnl = (live_premiums['long_pe'] - legs['long_pe']['premium']) * LOT_SIZE * lots
    ce_long_pnl = (live_premiums['long_ce'] - legs['long_ce']['premium']) * LOT_SIZE * lots

    total_pnl = pe_short_pnl + ce_short_pnl + pe_long_pnl + ce_long_pnl

    return {
        'pe_short_pnl': pe_short_pnl,
        'pe_long_pnl': pe_long_pnl,
        'ce_short_pnl': ce_short_pnl,
        'ce_long_pnl': ce_long_pnl,
        'total': total_pnl
    }


def check_triggers(trade, spot, live_premiums, pnl):
    """Detect maneuver/exit triggers"""
    alerts = []

    # Trigger 1: Target hit
    if pnl['total'] >= trade['target']:
        alerts.append(f"🟢 TARGET HIT: P&L ₹{pnl['total']:.0f} >= Target ₹{trade['target']:.0f}. CLOSE TRADE.")

    # Trigger 2: Stop loss hit
    if pnl['total'] <= -trade['stop_loss']:
        alerts.append(f"🔴 STOP LOSS HIT: P&L ₹{pnl['total']:.0f} <= -₹{trade['stop_loss']:.0f}. EXIT IMMEDIATELY.")

    # Trigger 3: Short strike touched
    short_pe = trade['legs']['short_pe']['strike']
    short_ce = trade['legs']['short_ce']['strike']

    if spot <= short_pe + 30:
        alerts.append(f"⚠️ SHORT PE TOUCHED: Spot {spot:.0f} near/below {short_pe}. Consider rolling CE side or exit.")

    if spot >= short_ce - 30:
        alerts.append(f"⚠️ SHORT CE TOUCHED: Spot {spot:.0f} near/above {short_ce}. Consider rolling PE side or exit.")

    # Trigger 4: 2x premium on short leg
    entry_short_pe = trade['legs']['short_pe']['premium']
    entry_short_ce = trade['legs']['short_ce']['premium']

    if entry_short_pe > 0 and live_premiums['short_pe'] >= entry_short_pe * 2:
        alerts.append(f"⚠️ PE PREMIUM 2X: Short PE at ₹{live_premiums['short_pe']:.2f} (entry ₹{entry_short_pe:.2f}). Roll/exit.")

    if entry_short_ce > 0 and live_premiums['short_ce'] >= entry_short_ce * 2:
        alerts.append(f"⚠️ CE PREMIUM 2X: Short CE at ₹{live_premiums['short_ce']:.2f} (entry ₹{entry_short_ce:.2f}). Roll/exit.")

    return alerts


def log_status(log_file, trade, live, live_premiums, pnl, alerts):
    """Write structured log entry"""
    now = datetime.now(IST)

    spot = live['nifty']
    vix = live['vix']
    short_pe = trade['legs']['short_pe']['strike']
    short_ce = trade['legs']['short_ce']['strike']

    profit_pct = (pnl['total'] / trade['net_credit']) * 100 if trade['net_credit'] > 0 else 0

    entry = f"""
{'=' * 90}
[{now.strftime('%Y-%m-%d %H:%M:%S IST')}]
Trade: {trade['trade_id']} | Status: {trade['status']} | Type: {trade['type']} ({trade['sd_level']})
{'=' * 90}

MARKET:
  Nifty:   {spot:,.2f}
  VIX:     {vix:.2f}

POSITION:
  PE: SELL {short_pe} (entry ₹{trade['legs']['short_pe']['premium']:.2f} | now ₹{live_premiums['short_pe']:.2f})
      BUY  {trade['legs']['long_pe']['strike']} (entry ₹{trade['legs']['long_pe']['premium']:.2f} | now ₹{live_premiums['long_pe']:.2f})
  CE: SELL {short_ce} (entry ₹{trade['legs']['short_ce']['premium']:.2f} | now ₹{live_premiums['short_ce']:.2f})
      BUY  {trade['legs']['long_ce']['strike']} (entry ₹{trade['legs']['long_ce']['premium']:.2f} | now ₹{live_premiums['long_ce']:.2f})

P&L BREAKDOWN:
  PE Short: ₹{pnl['pe_short_pnl']:+.0f}
  PE Long:  ₹{pnl['pe_long_pnl']:+.0f}
  CE Short: ₹{pnl['ce_short_pnl']:+.0f}
  CE Long:  ₹{pnl['ce_long_pnl']:+.0f}
  TOTAL:    ₹{pnl['total']:+.0f} ({profit_pct:+.1f}% of credit)

TARGETS:
  Profit Target: +₹{trade['target']:.0f}
  Stop Loss:     -₹{trade['stop_loss']:.0f}
  Distance to Target: ₹{trade['target'] - pnl['total']:.0f}
  Distance to SL:     ₹{trade['stop_loss'] + pnl['total']:.0f}

STRIKE DISTANCES:
  Spot to Short PE: {spot - short_pe:+.0f} pts
  Spot to Short CE: {short_ce - spot:+.0f} pts
"""

    if alerts:
        entry += "\n⚠️ ALERTS:\n"
        for a in alerts:
            entry += f"  {a}\n"
    else:
        entry += "\n✓ No alerts. Trade running normally.\n"

    entry += "\n"

    print(entry)

    with open(log_file, 'a') as f:
        f.write(entry)


def main():
    ensure_log_dir()

    # Load positions
    if not os.path.exists(POSITIONS_FILE):
        print(f"No positions file found at {POSITIONS_FILE}")
        return

    with open(POSITIONS_FILE, 'r') as f:
        data = json.load(f)

    active_trades = [t for t in data.get('active_trades', []) if t.get('status') == 'OPEN']

    if not active_trades:
        # Log "no positions" snapshot anyway
        now = datetime.now(IST)
        log_file = os.path.join(LOG_DIR, f"ic_log_{now.strftime('%Y-%m-%d')}.log")
        live = get_live_data()
        if live:
            entry = f"\n[{now.strftime('%Y-%m-%d %H:%M:%S IST')}] No open trades. Nifty: {live['nifty']:,.2f} | VIX: {live['vix']:.2f}\n"
            print(entry)
            with open(log_file, 'a') as f:
                f.write(entry)
        return

    # Process each active trade
    now = datetime.now(IST)
    log_file = os.path.join(LOG_DIR, f"ic_log_{now.strftime('%Y-%m-%d')}.log")

    live = get_live_data()
    if not live:
        return

    for trade in active_trades:
        # Fetch current premiums for all 4 legs
        expiry = trade['expiry']
        live_premiums = {
            'short_pe': get_strike_premium(expiry, trade['legs']['short_pe']['strike'], 'PE'),
            'long_pe':  get_strike_premium(expiry, trade['legs']['long_pe']['strike'], 'PE'),
            'short_ce': get_strike_premium(expiry, trade['legs']['short_ce']['strike'], 'CE'),
            'long_ce':  get_strike_premium(expiry, trade['legs']['long_ce']['strike'], 'CE'),
        }

        pnl = calculate_pnl(trade, live_premiums)
        alerts = check_triggers(trade, live['nifty'], live_premiums, pnl)
        log_status(log_file, trade, live, live_premiums, pnl, alerts)


if __name__ == "__main__":
    main()
