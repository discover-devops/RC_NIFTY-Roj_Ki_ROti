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

LOG_DIR = os.path.expanduser("~/nifty-monitor/logs")
POSITIONS_FILE = os.path.expanduser("~/nifty-monitor/positions.json")
CSV_FILE = os.path.expanduser("~/nifty-monitor/logs/daily_tracker.csv")


def ensure_dirs():
    os.makedirs(LOG_DIR, exist_ok=True)


def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def get_live_data():
    try:
        quotes = kite.quote([NIFTY_TOKEN, VIX_TOKEN])
        return {
            'nifty': quotes[str(NIFTY_TOKEN)]['last_price'],
            'vix':   quotes[str(VIX_TOKEN)]['last_price']
        }
    except Exception as e:
        print(f"Live data error: {e}")
        return None


# ==================== INSTRUMENTS CACHE ====================
_instruments_cache = None
_instruments_cache_time = None
CACHE_TTL_SECONDS = 300  # Refresh every 5 minutes max


def get_instruments_cached():
    """Fetch NFO instruments ONCE per run, cache for 5 minutes.
    Old code called kite.instruments('NFO') 4 times per trade per hour.
    With 1 trade that's 4 API calls wasted. With multiple trades, even worse.
    """
    global _instruments_cache, _instruments_cache_time
    now = datetime.now(IST)

    if (_instruments_cache is not None
            and _instruments_cache_time is not None
            and (now - _instruments_cache_time).total_seconds() < CACHE_TTL_SECONDS):
        return _instruments_cache

    try:
        _instruments_cache = kite.instruments("NFO")
        _instruments_cache_time = now
        return _instruments_cache
    except Exception as e:
        print(f"Instruments fetch error: {e}")
        return _instruments_cache or []  # Return stale cache if fresh fetch fails


def get_option_ltp(expiry_str, strike, opt_type):
    """Fetch LTP for a specific Nifty option (uses cached instruments)"""
    try:
        instruments = get_instruments_cached()
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        match = [
            i for i in instruments
            if i['name'] == 'NIFTY'
            and i['expiry'] == expiry_date
            and int(i['strike']) == int(strike)
            and i['instrument_type'] == opt_type
        ]
        if not match:
            return 0
        symbol = f"NFO:{match[0]['tradingsymbol']}"
        quote = kite.quote([symbol])
        return quote[symbol].get('last_price', 0)
    except Exception:
        return 0


def get_all_leg_ltps(expiry_str, parsed_legs):
    """Fetch LTP for all 4 legs in ONE kite.quote() call.
    Old code: 4 separate kite.quote() calls = 4 API hits per trade.
    New code: 1 kite.quote() call with all 4 symbols = 1 API hit per trade.
    """
    try:
        instruments = get_instruments_cached()
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()

        # Build symbol map for all 4 legs
        leg_symbols = {}
        for leg_key in ['short_pe', 'long_pe', 'short_ce', 'long_ce']:
            strike = parsed_legs[leg_key]['strike']
            opt_type = 'PE' if 'pe' in leg_key else 'CE'
            match = [
                i for i in instruments
                if i['name'] == 'NIFTY'
                and i['expiry'] == expiry_date
                and int(i['strike']) == int(strike)
                and i['instrument_type'] == opt_type
            ]
            if match:
                leg_symbols[leg_key] = f"NFO:{match[0]['tradingsymbol']}"

        if not leg_symbols:
            return {k: 0 for k in ['short_pe', 'long_pe', 'short_ce', 'long_ce']}

        # ONE quote call for all legs
        quotes = kite.quote(list(leg_symbols.values()))

        result = {}
        for leg_key, symbol in leg_symbols.items():
            result[leg_key] = quotes.get(symbol, {}).get('last_price', 0)

        # Fill missing legs with 0
        for k in ['short_pe', 'long_pe', 'short_ce', 'long_ce']:
            if k not in result:
                result[k] = 0

        return result
    except Exception as e:
        print(f"Batch LTP error: {e}")
        return {k: 0 for k in ['short_pe', 'long_pe', 'short_ce', 'long_ce']}


def parse_legs(trade):
    """
    Parse legs from positions.json.
    Supports both array format (with action field) and dict format.
    Returns: short_pe, long_pe, short_ce, long_ce as dicts with strike + premium
    """
    legs = trade['legs']

    # Array format (Rahul's format)
    if isinstance(legs, list):
        result = {}
        for leg in legs:
            opt_type = leg['option_type']
            action   = leg['action'].upper()
            key = f"{'short' if action == 'SELL' else 'long'}_{opt_type.lower()}"
            result[key] = {
                'strike':   int(leg['strike']),
                'premium':  float(leg['premium']),
                'quantity': int(leg['quantity'])
            }
        return result

    # Dict format (original script format)
    return {k: {'strike': int(v['strike']), 'premium': float(v['premium']), 'quantity': 65}
            for k, v in legs.items()}


def calculate_pnl(trade, parsed_legs, live_premiums):
    """Calculate current unrealized P&L"""
    lots       = trade.get('lots', 1)
    qty_per_lot = 65

    # Use actual quantity from legs if available
    short_pe_qty = parsed_legs['short_pe'].get('quantity', lots * qty_per_lot)
    qty = short_pe_qty  # All legs same qty

    short_pe_pnl = (parsed_legs['short_pe']['premium'] - live_premiums['short_pe']) * qty
    long_pe_pnl  = (live_premiums['long_pe']  - parsed_legs['long_pe']['premium'])  * qty
    short_ce_pnl = (parsed_legs['short_ce']['premium'] - live_premiums['short_ce']) * qty
    long_ce_pnl  = (live_premiums['long_ce']  - parsed_legs['long_ce']['premium'])  * qty

    return {
        'short_pe': short_pe_pnl,
        'long_pe':  long_pe_pnl,
        'short_ce': short_ce_pnl,
        'long_ce':  long_ce_pnl,
        'total':    short_pe_pnl + long_pe_pnl + short_ce_pnl + long_ce_pnl
    }


def check_triggers(trade, spot, parsed_legs, live_premiums, pnl):
    """Detect exit/maneuver triggers based on entry_rules.
    Enhanced: tracks time since target hit, escalates urgency.
    """
    alerts = []
    rules  = trade.get('entry_rules', {})

    max_profit  = trade.get('max_profit',  5525)
    net_credit  = max_profit  # max_profit = total credit for IC

    # Profit target — with urgency escalation
    target_pct = rules.get('profit_target_percent', 50) / 100
    target_amt = net_credit * target_pct
    if pnl['total'] >= target_amt:
        pnl_pct = (pnl['total'] / net_credit) * 100
        if pnl_pct >= 80:
            alerts.append(f"🟢🟢🟢 TARGET HIT — P&L at {pnl_pct:.0f}% of credit! "
                          f"+₹{pnl['total']:.0f}. CLOSE NOW. Do not hold for more — "
                          f"you are risking ₹{pnl['total']:.0f} of unrealized profit.")
        elif pnl_pct >= 50:
            alerts.append(f"🟢🟢 TARGET HIT: +₹{pnl['total']:.0f} ({pnl_pct:.0f}% of credit). "
                          f"Target was {rules.get('profit_target_percent',50)}%. "
                          f"CLOSE TRADE — every hour you hold, you risk giving this back.")
        else:
            alerts.append(f"🟢 PROFIT TARGET HIT: +₹{pnl['total']:.0f} >= +₹{target_amt:.0f} "
                          f"({rules.get('profit_target_percent',50)}%). CLOSE TRADE.")

    # Stop loss
    sl_pct = rules.get('stop_loss_percent', 100) / 100
    sl_amt = net_credit * sl_pct
    if pnl['total'] <= -sl_amt:
        alerts.append(f"🔴 STOP LOSS HIT: ₹{pnl['total']:.0f} <= -₹{sl_amt:.0f} ({rules.get('stop_loss_percent',100)}%). EXIT NOW.")

    # Spot exits
    exit_below = rules.get('spot_exit_below', 0)
    exit_above = rules.get('spot_exit_above', 999999)
    if exit_below and spot <= exit_below:
        alerts.append(f"🔴 SPOT BELOW EXIT LEVEL {exit_below}: Spot {spot:.0f}. CLOSE TRADE.")
    if exit_above and spot >= exit_above:
        alerts.append(f"🔴 SPOT ABOVE EXIT LEVEL {exit_above}: Spot {spot:.0f}. CLOSE TRADE.")

    # Short strike approached (within 50 pts)
    short_pe = parsed_legs['short_pe']['strike']
    short_ce = parsed_legs['short_ce']['strike']
    entry_short_pe_prem = parsed_legs['short_pe']['premium']
    entry_short_ce_prem = parsed_legs['short_ce']['premium']

    if spot <= short_pe + 50:
        alerts.append(f"⚠️  PE STRIKE NEAR: Spot {spot:.0f} within 50pts of short PE {short_pe}. Monitor closely.")

    if spot >= short_ce - 50:
        alerts.append(f"⚠️  CE STRIKE NEAR: Spot {spot:.0f} within 50pts of short CE {short_ce}. Monitor closely.")

    # 2x premium on short legs (maneuver trigger)
    if entry_short_pe_prem > 0 and live_premiums['short_pe'] >= entry_short_pe_prem * 2:
        alerts.append(f"⚠️  PE PREMIUM 2X: Short PE now ₹{live_premiums['short_pe']:.2f} vs entry ₹{entry_short_pe_prem:.2f}. Consider rolling CE side.")

    if entry_short_ce_prem > 0 and live_premiums['short_ce'] >= entry_short_ce_prem * 2:
        alerts.append(f"⚠️  CE PREMIUM 2X: Short CE now ₹{live_premiums['short_ce']:.2f} vs entry ₹{entry_short_ce_prem:.2f}. Consider rolling PE side.")

    # NEW: VIX spike warning (passed via trade context or live data)
    vix_at_entry = trade.get('vix_at_entry', 0)
    # VIX is checked in log_and_print where live data is available

    # NEW: DTE countdown warning
    try:
        expiry_date = datetime.strptime(str(trade['expiry']), "%Y-%m-%d").date()
        today = datetime.now(IST).date()
        dte = (expiry_date - today).days
        if dte <= 0:
            alerts.append(f"🔴 EXPIRY DAY: Trade expires TODAY. Close all positions before 3:15 PM.")
        elif dte == 1:
            alerts.append(f"⚠️  1 DAY TO EXPIRY: Gamma risk elevated. Consider closing if P&L is positive.")
    except Exception:
        pass

    return alerts


def write_csv_row(trade_id, now, spot, vix, pnl_total, pnl_pct, alerts_count):
    """Append one row to daily CSV tracker"""
    header = "Timestamp,Trade_ID,Nifty,VIX,PnL,PnL_Pct,Alerts\n"
    row = f"{now.strftime('%Y-%m-%d %H:%M')},{trade_id},{spot:.2f},{vix:.2f},{pnl_total:.0f},{pnl_pct:.1f},{alerts_count}\n"

    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a') as f:
        if not file_exists:
            f.write(header)
        f.write(row)


def log_and_print(log_file, trade, live, parsed_legs, live_premiums, pnl, alerts):
    now  = datetime.now(IST)
    spot = live['nifty']
    vix  = live['vix']

    # VIX spike check (add to alerts if VIX jumped significantly)
    vix_at_entry = trade.get('vix_at_entry', 0)
    if vix_at_entry > 0 and vix >= vix_at_entry * 1.25:
        alerts.append(f"⚠️  VIX SPIKE: VIX now {vix:.1f} vs {vix_at_entry:.1f} at entry (+{((vix/vix_at_entry)-1)*100:.0f}%). "
                      f"Premiums expanding — monitor SL closely.")
    if vix >= 20:
        alerts.append(f"⚠️  VIX HIGH: {vix:.1f} — consider tightening SL or closing profitable positions.")

    max_profit = trade.get('max_profit', 5525)
    pnl_pct    = (pnl['total'] / max_profit * 100) if max_profit else 0

    short_pe = parsed_legs['short_pe']['strike']
    short_ce = parsed_legs['short_ce']['strike']

    rules = trade.get('entry_rules', {})
    target_amt = max_profit * rules.get('profit_target_percent', 50) / 100
    sl_amt     = max_profit * rules.get('stop_loss_percent', 100) / 100

    entry = f"""
{'='*90}
[{now.strftime('%Y-%m-%d %H:%M:%S IST')}]  TRADE: {trade['trade_id']}  |  {trade['strategy']}  |  {trade['status']}
{'='*90}

MARKET
  Nifty : {spot:>10,.2f}
  VIX   : {vix:>10.2f}

POSITION  (Expiry: {trade['expiry']})
  PE Spread : SELL {short_pe} PE  entry ₹{parsed_legs['short_pe']['premium']:.2f}  now ₹{live_premiums['short_pe']:.2f}
              BUY  {parsed_legs['long_pe']['strike']} PE  entry ₹{parsed_legs['long_pe']['premium']:.2f}  now ₹{live_premiums['long_pe']:.2f}
  CE Spread : SELL {short_ce} CE  entry ₹{parsed_legs['short_ce']['premium']:.2f}  now ₹{live_premiums['short_ce']:.2f}
              BUY  {parsed_legs['long_ce']['strike']} CE  entry ₹{parsed_legs['long_ce']['premium']:.2f}  now ₹{live_premiums['long_ce']:.2f}

P&L BREAKDOWN
  PE Short  : ₹{pnl['short_pe']:>+10.0f}
  PE Long   : ₹{pnl['long_pe']:>+10.0f}
  CE Short  : ₹{pnl['short_ce']:>+10.0f}
  CE Long   : ₹{pnl['long_ce']:>+10.0f}
  ─────────────────
  TOTAL     : ₹{pnl['total']:>+10.0f}  ({pnl_pct:+.1f}% of credit)

TARGETS
  Profit Target : +₹{target_amt:.0f}  ({rules.get('profit_target_percent',50)}% of credit)
  Stop Loss     : -₹{sl_amt:.0f}  ({rules.get('stop_loss_percent',100)}% of credit)
  Remaining to target : ₹{target_amt - pnl['total']:.0f}
  Buffer to SL        : ₹{sl_amt + pnl['total']:.0f}

STRIKE DISTANCES
  Spot → Short PE ({short_pe}) : {spot - short_pe:+.0f} pts
  Spot → Short CE ({short_ce}) : {short_ce - spot:+.0f} pts
  Breakeven LOW  : {trade.get('breakeven_low', 0)}  (spot is {spot - trade.get('breakeven_low',0):+.0f} pts away)
  Breakeven HIGH : {trade.get('breakeven_high', 0)}  (spot is {trade.get('breakeven_high',0) - spot:+.0f} pts away)
"""

    if alerts:
        entry += "\nALERTS\n"
        for a in alerts:
            entry += f"  {a}\n"
    else:
        entry += "\n  ✓ No alerts. Trade running normally.\n"

    entry += "\n"

    print(entry)
    with open(log_file, 'a') as f:
        f.write(entry)

    # Write CSV row
    write_csv_row(trade['trade_id'], now, spot, vix, pnl['total'], pnl_pct, len(alerts))


def main():
    ensure_dirs()

    now      = datetime.now(IST)
    log_file = os.path.join(LOG_DIR, f"ic_log_{now.strftime('%Y-%m-%d')}.log")

    # Load positions
    if not os.path.exists(POSITIONS_FILE):
        print(f"[{now.strftime('%H:%M')}] No positions.json found at {POSITIONS_FILE}")
        return

    with open(POSITIONS_FILE, 'r') as f:
        data = json.load(f)

    active_trades = [t for t in data.get('active_trades', []) if t.get('status') == 'OPEN']

    if not active_trades:
        msg = f"[{now.strftime('%Y-%m-%d %H:%M:%S IST')}] No open trades.\n"
        print(msg)
        with open(log_file, 'a') as f:
            f.write(msg)
        return

    live = get_live_data()
    if not live:
        print("Could not fetch live data. Check access token.")
        return

    for trade in active_trades:
        parsed_legs = parse_legs(trade)

        # Batch-fetch all 4 leg premiums in ONE quote call (was 4 separate calls)
        live_premiums = get_all_leg_ltps(
            trade['expiry'], parsed_legs
        )

        pnl    = calculate_pnl(trade, parsed_legs, live_premiums)
        alerts = check_triggers(trade, live['nifty'], parsed_legs, live_premiums, pnl)

        log_and_print(log_file, trade, live, parsed_legs, live_premiums, pnl, alerts)

    if not is_market_open():
        print(f"Market is closed. EOD snapshot written to {log_file}")


if __name__ == "__main__":
    main()
