import os
import math
from datetime import datetime, timedelta
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
SPREAD_WIDTH = 200
PROFIT_TARGET_PCT = 0.50
STOP_LOSS_PCT = 0.50
MIN_DTE = 2  # Skip expiries with less than 2 days


def get_live_data():
    try:
        quotes = kite.quote([NIFTY_TOKEN, VIX_TOKEN])
        return {
            'nifty': quotes[str(NIFTY_TOKEN)]['last_price'],
            'vix': quotes[str(VIX_TOKEN)]['last_price'],
            'high': quotes[str(NIFTY_TOKEN)]['ohlc']['high'],
            'low': quotes[str(NIFTY_TOKEN)]['ohlc']['low']
        }
    except Exception as e:
        print(f"Live data error: {e}")
        return None


def get_tradeable_expiry(min_dte=MIN_DTE):
    """Get next valid expiry with at least min_dte days"""
    instruments = kite.instruments("NFO")
    nifty_options = [
        i for i in instruments
        if i['name'] == 'NIFTY' and i['instrument_type'] in ['CE', 'PE']
    ]
    today = datetime.now(IST).date()

    # Filter expiries with enough DTE
    valid_expiries = sorted(set(
        i['expiry'] for i in nifty_options
        if (i['expiry'] - today).days >= min_dte
    ))

    return valid_expiries[0] if valid_expiries else None


def calculate_sd_moves(spot, iv_pct, dte_days):
    """Calculate 1SD, 1.5SD, 2SD price moves"""
    iv_decimal = iv_pct / 100
    sd1 = spot * iv_decimal * math.sqrt(dte_days / 365)
    return {
        '1SD': sd1,
        '1.5SD': sd1 * 1.5,
        '2SD': sd1 * 2
    }


def get_strike_premium(expiry, strike, opt_type, instruments_cache=None):
    """Fetch live premium for specific strike"""
    try:
        instruments = instruments_cache if instruments_cache else kite.instruments("NFO")
        match = [
            i for i in instruments
            if i['name'] == 'NIFTY'
            and i['expiry'] == expiry
            and int(i['strike']) == strike
            and i['instrument_type'] == opt_type
        ]
        if not match:
            return None
        symbol = f"NFO:{match[0]['tradingsymbol']}"
        quote = kite.quote([symbol])
        return quote[symbol].get('last_price', 0)
    except Exception as e:
        return None


def build_iron_condor(spot, expiry, sd_label, sd_move, instruments_cache):
    """Build IC structure for given SD level"""
    # Calculate strikes, ensure they're DIFFERENT from spot
    short_pe = round((spot - sd_move) / 50) * 50
    long_pe = short_pe - SPREAD_WIDTH
    short_ce = round((spot + sd_move) / 50) * 50
    long_ce = short_ce + SPREAD_WIDTH

    # Safety check: short strikes must not be at or beyond spot
    if short_pe >= spot:
        short_pe = round((spot - 100) / 50) * 50  # at least 100pts below
        long_pe = short_pe - SPREAD_WIDTH

    if short_ce <= spot:
        short_ce = round((spot + 100) / 50) * 50  # at least 100pts above
        long_ce = short_ce + SPREAD_WIDTH

    # Fetch premiums
    short_pe_prem = get_strike_premium(expiry, short_pe, 'PE', instruments_cache) or 0
    long_pe_prem = get_strike_premium(expiry, long_pe, 'PE', instruments_cache) or 0
    short_ce_prem = get_strike_premium(expiry, short_ce, 'CE', instruments_cache) or 0
    long_ce_prem = get_strike_premium(expiry, long_ce, 'CE', instruments_cache) or 0

    # Calculate economics (per lot of 65)
    pe_credit = (short_pe_prem - long_pe_prem) * LOT_SIZE
    ce_credit = (short_ce_prem - long_ce_prem) * LOT_SIZE
    total_credit = pe_credit + ce_credit

    # Max loss per side
    max_loss_theoretical = (SPREAD_WIDTH * LOT_SIZE) - max(pe_credit, ce_credit)

    # SL/Target with discipline
    sl_amount = abs(total_credit * STOP_LOSS_PCT)
    target_amount = total_credit * PROFIT_TARGET_PCT

    return {
        'label': sd_label,
        'short_pe': short_pe, 'long_pe': long_pe,
        'short_ce': short_ce, 'long_ce': long_ce,
        'short_pe_prem': short_pe_prem, 'long_pe_prem': long_pe_prem,
        'short_ce_prem': short_ce_prem, 'long_ce_prem': long_ce_prem,
        'pe_credit': pe_credit, 'ce_credit': ce_credit,
        'total_credit': total_credit,
        'max_loss_theoretical': max_loss_theoretical,
        'sl_amount': sl_amount,
        'target_amount': target_amount
    }


def calculate_ev(setup, win_prob):
    """Expected value with SL/Target discipline"""
    win = setup['target_amount']
    loss = setup['sl_amount']
    return (win_prob * win) - ((1 - win_prob) * loss)


def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def main():
    print("=" * 90)
    print("IRON CONDOR SETUP CALCULATOR")
    print("=" * 90)

    if not is_market_open():
        now = datetime.now(IST)
        print(f"\n⚠️  Market is CLOSED at {now.strftime('%H:%M IST')}.")
        print("   Showing analysis based on last available prices.")
        print("   Premiums may not reflect tradeable values until market opens.\n")

    live = get_live_data()
    if not live:
        print("Could not fetch live data.")
        return

    expiry = get_tradeable_expiry()
    if not expiry:
        print("No tradeable expiry found.")
        return

    today = datetime.now(IST).date()
    dte = (expiry - today).days

    spot = live['nifty']
    vix = live['vix']

    print(f"\nTime:      {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    print(f"Nifty:     {spot:,.2f}")
    print(f"VIX:       {vix:.2f}")
    print(f"Day Range: {live['low']:,.0f} - {live['high']:,.0f}")
    print(f"Expiry:    {expiry} ({dte} days)")
    print(f"Lot Size:  {LOT_SIZE}")

    if dte < 3:
        print(f"\n⚠️  Warning: Only {dte} days to expiry. High gamma risk.")

    # Calculate SDs
    sds = calculate_sd_moves(spot, vix, dte)

    print(f"\n--- Expected Move (using VIX as IV) ---")
    print(f"  1 SD:   ±{sds['1SD']:.0f} pts")
    print(f"  1.5 SD: ±{sds['1.5SD']:.0f} pts")
    print(f"  2 SD:   ±{sds['2SD']:.0f} pts")

    # Cache instruments to avoid multiple API calls
    print("\nFetching option chain data...")
    instruments_cache = kite.instruments("NFO")

    print("\n--- Iron Condor Options ---")

    win_probs = {'1SD': 0.68, '1.5SD': 0.80, '2SD': 0.92}

    setups = []
    for sd_label in ['1SD', '1.5SD', '2SD']:
        setup = build_iron_condor(spot, expiry, sd_label, sds[sd_label], instruments_cache)
        setup['ev'] = calculate_ev(setup, win_probs[sd_label])
        setup['win_prob'] = win_probs[sd_label]
        setups.append(setup)

    for s in setups:
        print(f"\n[{s['label']}] - Win Prob: {s['win_prob']*100:.0f}%")
        print(f"  PUT Side:  SELL {s['short_pe']} PE @ ₹{s['short_pe_prem']:.2f}  |  BUY {s['long_pe']} PE @ ₹{s['long_pe_prem']:.2f}")
        print(f"  CALL Side: SELL {s['short_ce']} CE @ ₹{s['short_ce_prem']:.2f}  |  BUY {s['long_ce']} CE @ ₹{s['long_ce_prem']:.2f}")
        print(f"  Credit (1 lot):    ₹{s['total_credit']:.0f}")
        print(f"  Target (50%):      +₹{s['target_amount']:.0f}")
        print(f"  Stop Loss (50%):   -₹{s['sl_amount']:.0f}")
        print(f"  EV per trade:      ₹{s['ev']:+.0f}")

    # Recommendation
    print(f"\n--- RECOMMENDATION ---")
    rec = setups[1]  # 1.5SD

    if rec['total_credit'] < 500:
        print(f"  ⚠️  Credit too thin (₹{rec['total_credit']:.0f}). Skip this trade.")
    else:
        print(f"  Use 1.5SD setup (best balance of probability and premium)")
        print(f"  STRUCTURE:")
        print(f"    SELL {rec['short_pe']} PE @ ₹{rec['short_pe_prem']:.2f}")
        print(f"    BUY  {rec['long_pe']} PE @ ₹{rec['long_pe_prem']:.2f}")
        print(f"    SELL {rec['short_ce']} CE @ ₹{rec['short_ce_prem']:.2f}")
        print(f"    BUY  {rec['long_ce']} CE @ ₹{rec['long_ce_prem']:.2f}")
        print(f"  Net Credit: ₹{rec['total_credit']:.0f}")
        print(f"  Target:     +₹{rec['target_amount']:.0f}")
        print(f"  Stop Loss:  -₹{rec['sl_amount']:.0f}")

        print(f"\n--- TO RECORD THIS TRADE ---")
        print(f"After entering paper trade on Sensibull, update positions.json with:")
        print(f"""
{{
  "trade_id": "PT001",
  "entry_date": "{today}",
  "entry_time": "HH:MM",
  "expiry": "{expiry}",
  "type": "IRON_CONDOR",
  "sd_level": "1.5SD",
  "spot_at_entry": {spot},
  "vix_at_entry": {vix},
  "lots": 1,
  "legs": {{
    "short_pe": {{"strike": {rec['short_pe']}, "premium": {rec['short_pe_prem']}}},
    "long_pe":  {{"strike": {rec['long_pe']}, "premium": {rec['long_pe_prem']}}},
    "short_ce": {{"strike": {rec['short_ce']}, "premium": {rec['short_ce_prem']}}},
    "long_ce":  {{"strike": {rec['long_ce']}, "premium": {rec['long_ce_prem']}}}
  }},
  "net_credit": {rec['total_credit']:.0f},
  "target": {rec['target_amount']:.0f},
  "stop_loss": {rec['sl_amount']:.0f},
  "is_paper": true,
  "status": "OPEN"
}}""")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()
