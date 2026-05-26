import os
import time
from datetime import datetime, timedelta
import pytz
import numpy as np
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

IST = pytz.timezone('Asia/Kolkata')
NIFTY_TOKEN = 256265
VIX_TOKEN = 264969

# Rules
TEST_TRIGGER_DISTANCE = 50          # within 50pts of OI wall = test
LEVEL_1_OTM_DISTANCE = 1000         # first entry 1000pts beyond wall
SUBSEQUENT_LEVEL_BUFFER = 200       # +200pts beyond next wall
SPREAD_WIDTH = 200                  # 200pt wing protection
LOTS_PER_ENTRY = 2
MAX_LOTS_TOTAL = 6
SL_PERCENT = 0.50                   # 50% of credit
TARGET_PERCENT = 0.35               # 35% of credit
LOT_SIZE = 65
RSI_PERIOD = 14


# ==================== DATA FETCHERS ====================

def get_live_data():
    """Live Nifty + VIX"""
    try:
        quotes = kite.quote([NIFTY_TOKEN, VIX_TOKEN])
        return {
            'nifty': quotes[str(NIFTY_TOKEN)]['last_price'],
            'high': quotes[str(NIFTY_TOKEN)]['ohlc']['high'],
            'low': quotes[str(NIFTY_TOKEN)]['ohlc']['low'],
            'open': quotes[str(NIFTY_TOKEN)]['ohlc']['open'],
            'prev_close': quotes[str(NIFTY_TOKEN)]['ohlc']['close'],
            'vix': quotes[str(VIX_TOKEN)]['last_price']
        }
    except Exception as e:
        print(f"Live data error: {e}")
        return None


def get_hourly_data(days=10):
    """Hourly historical Nifty data for divergence calculation"""
    try:
        today = datetime.now(IST).date()
        from_date = today - timedelta(days=days)

        data = kite.historical_data(
            instrument_token=NIFTY_TOKEN,
            from_date=from_date,
            to_date=today,
            interval="60minute"
        )
        return data
    except Exception as e:
        print(f"Historical data error: {e}")
        return []


def get_nearest_expiry():
    """Next Tuesday Nifty expiry"""
    instruments = kite.instruments("NFO")
    nifty_options = [
        i for i in instruments
        if i['name'] == 'NIFTY' and i['instrument_type'] in ['CE', 'PE']
    ]
    today = datetime.now(IST).date()
    expiries = sorted(set(i['expiry'] for i in nifty_options if i['expiry'] >= today))
    return expiries[0] if expiries else None


def get_oi_data(expiry, atm_strike, range_strikes=15):
    """OI for ATM ± N strikes"""
    instruments = kite.instruments("NFO")
    target_strikes = [atm_strike + (i * 50) for i in range(-range_strikes, range_strikes + 1)]

    relevant = [
        i for i in instruments
        if i['name'] == 'NIFTY'
        and i['expiry'] == expiry
        and i['strike'] in target_strikes
        and i['instrument_type'] in ['CE', 'PE']
    ]

    if not relevant:
        return None

    symbols = [f"NFO:{i['tradingsymbol']}" for i in relevant]
    quotes = kite.quote(symbols)

    data = {}
    for inst in relevant:
        symbol_key = f"NFO:{inst['tradingsymbol']}"
        if symbol_key not in quotes:
            continue
        q = quotes[symbol_key]
        strike = int(inst['strike'])
        opt_type = inst['instrument_type']
        if strike not in data:
            data[strike] = {}
        data[strike][opt_type] = {
            'ltp': q.get('last_price', 0),
            'oi': q.get('oi', 0)
        }
    return data


def get_positions():
    """Fetch current open positions"""
    try:
        positions = kite.positions()
        net = positions.get('net', [])
        # Filter only open Nifty option positions
        open_pos = [p for p in net if p['quantity'] != 0 and 'NIFTY' in p['tradingsymbol']]
        return open_pos
    except Exception as e:
        print(f"Positions error: {e}")
        return []


# ==================== INDICATORS ====================

def calculate_rsi(closes, period=14):
    """RSI calculation"""
    if len(closes) < period + 1:
        return []

    closes = np.array(closes)
    deltas = np.diff(closes)

    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    # Initial averages
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    rsi_values = [50] * period  # padding

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        rsi_values.append(rsi)

    return rsi_values


def calculate_macd(closes, fast=12, slow=26, signal=9):
    """MACD calculation - returns histogram"""
    if len(closes) < slow + signal:
        return []

    closes = np.array(closes)

    def ema(data, period):
        alpha = 2 / (period + 1)
        ema_vals = [data[0]]
        for price in data[1:]:
            ema_vals.append(alpha * price + (1 - alpha) * ema_vals[-1])
        return np.array(ema_vals)

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return histogram.tolist()


def detect_regular_divergence(prices, indicator, lookback=20, min_separation=3):
    """
    Detect REGULAR divergence (reversal signal) on hourly TF.

    Bullish: Price makes Lower Low, Indicator makes Higher Low
    Bearish: Price makes Higher High, Indicator makes Lower High
    """
    if len(prices) < lookback or len(indicator) < lookback:
        return None

    recent_prices = prices[-lookback:]
    recent_indicator = indicator[-lookback:]

    # Find local lows and highs
    def find_extremes(data, find_min=True):
        extremes = []
        for i in range(1, len(data) - 1):
            if find_min:
                if data[i] < data[i-1] and data[i] < data[i+1]:
                    extremes.append((i, data[i]))
            else:
                if data[i] > data[i-1] and data[i] > data[i+1]:
                    extremes.append((i, data[i]))
        return extremes

    # Bullish divergence check
    price_lows = find_extremes(recent_prices, find_min=True)
    indicator_lows = find_extremes(recent_indicator, find_min=True)

    if len(price_lows) >= 2 and len(indicator_lows) >= 2:
        last_pl, prev_pl = price_lows[-1], price_lows[-2]
        last_il, prev_il = indicator_lows[-1], indicator_lows[-2]

        # Same window
        if abs(last_pl[0] - last_il[0]) <= min_separation and abs(prev_pl[0] - prev_il[0]) <= min_separation:
            if last_pl[1] < prev_pl[1] and last_il[1] > prev_il[1]:
                return "BULLISH"

    # Bearish divergence check
    price_highs = find_extremes(recent_prices, find_min=False)
    indicator_highs = find_extremes(recent_indicator, find_min=False)

    if len(price_highs) >= 2 and len(indicator_highs) >= 2:
        last_ph, prev_ph = price_highs[-1], price_highs[-2]
        last_ih, prev_ih = indicator_highs[-1], indicator_highs[-2]

        if abs(last_ph[0] - last_ih[0]) <= min_separation and abs(prev_ph[0] - prev_ih[0]) <= min_separation:
            if last_ph[1] > prev_ph[1] and last_ih[1] < prev_ih[1]:
                return "BEARISH"

    return None


# ==================== ANALYSIS ====================

def find_oi_walls(oi_data, top_n=5):
    """Find top OI strikes for CE and PE"""
    ce_walls = []
    pe_walls = []

    for strike, data in oi_data.items():
        ce_oi = data.get('CE', {}).get('oi', 0)
        pe_oi = data.get('PE', {}).get('oi', 0)
        if ce_oi > 0:
            ce_walls.append((strike, ce_oi))
        if pe_oi > 0:
            pe_walls.append((strike, pe_oi))

    ce_walls.sort(key=lambda x: x[1], reverse=True)
    pe_walls.sort(key=lambda x: x[1], reverse=True)

    return ce_walls[:top_n], pe_walls[:top_n]


def detect_level_test(spot, walls, side='support'):
    """Check if spot is testing any wall"""
    for strike, oi in walls:
        distance = abs(spot - strike)
        if distance <= TEST_TRIGGER_DISTANCE:
            return {'strike': strike, 'oi': oi, 'distance': distance}
    return None


def get_existing_legs_count(positions, side='PE'):
    """Count how many staged legs already open for PE or CE side"""
    short_legs = set()
    for p in positions:
        sym = p['tradingsymbol']
        if side in sym and p['quantity'] < 0:  # short
            # Extract strike from symbol like NIFTY26MAY22500PE
            short_legs.add(sym)
    return len(short_legs)


def suggest_staged_entry(spot, wall_tested, divergence, side, expiry, oi_data, existing_legs):
    """
    Decide which staged entry to suggest.

    side = 'PUT' for Bull Put Spread (support test)
    side = 'CALL' for Bear Call Spread (resistance test)
    """
    if existing_legs >= 3:
        return None, "Max 3 legs already deployed"

    # Stage determines entry strike
    stage = existing_legs + 1  # 1, 2, or 3

    if side == 'PUT':
        if divergence != 'BULLISH':
            return None, "Waiting for bullish divergence on hourly"

        if stage == 1:
            # 1000 pts below the tested wall
            short_strike = (wall_tested - LEVEL_1_OTM_DISTANCE)
        else:
            # Next OI wall below + 200pts buffer
            pe_walls_below = sorted(
                [s for s in oi_data.keys() if s < spot],
                reverse=True
            )
            # Find next strong PE wall below current short position
            short_strike = wall_tested - 200 - ((stage - 1) * 200)

        short_strike = round(short_strike / 50) * 50
        long_strike = short_strike - SPREAD_WIDTH

        return {
            'type': f'BULL PUT SPREAD - LEVEL {stage}',
            'short_strike': short_strike,
            'long_strike': long_strike,
            'option_type': 'PE',
            'lots': LOTS_PER_ENTRY,
            'reason': f"Support {wall_tested} tested + bullish divergence confirmed"
        }, None

    elif side == 'CALL':
        if divergence != 'BEARISH':
            return None, "Waiting for bearish divergence on hourly"

        if stage == 1:
            short_strike = wall_tested + LEVEL_1_OTM_DISTANCE
        else:
            short_strike = wall_tested + 200 + ((stage - 1) * 200)

        short_strike = round(short_strike / 50) * 50
        long_strike = short_strike + SPREAD_WIDTH

        return {
            'type': f'BEAR CALL SPREAD - LEVEL {stage}',
            'short_strike': short_strike,
            'long_strike': long_strike,
            'option_type': 'CE',
            'lots': LOTS_PER_ENTRY,
            'reason': f"Resistance {wall_tested} tested + bearish divergence confirmed"
        }, None

    return None, "No setup"


def get_spread_premium(oi_data, short_strike, long_strike, opt_type):
    """Calculate net credit for a spread"""
    try:
        short_ltp = oi_data[short_strike][opt_type]['ltp']
        long_ltp = oi_data[long_strike][opt_type]['ltp']
        return short_ltp - long_ltp
    except:
        return 0


# ==================== DISPLAY ====================

def print_dashboard(live, oi_data, expiry, hourly_data, positions):
    now = datetime.now(IST)
    spot = live['nifty']
    vix = live['vix']
    atm = round(spot / 50) * 50
    days_to_expiry = (expiry - now.date()).days

    # OI analysis
    ce_walls, pe_walls = find_oi_walls(oi_data, top_n=5)

    # Detect tests
    resistance_test = detect_level_test(spot, ce_walls, 'resistance')
    support_test = detect_level_test(spot, pe_walls, 'support')

    # Indicators
    closes = [c['close'] for c in hourly_data] if hourly_data else []
    rsi_vals = calculate_rsi(closes) if closes else []
    macd_hist = calculate_macd(closes) if closes else []

    rsi_now = rsi_vals[-1] if rsi_vals else 0
    macd_now = macd_hist[-1] if macd_hist else 0

    # Divergence detection
    rsi_div = detect_regular_divergence(closes, rsi_vals) if rsi_vals else None
    macd_div = detect_regular_divergence(closes, macd_hist) if macd_hist else None

    # Use either RSI or MACD divergence (any one is enough per Rahul's rule)
    bullish_div = (rsi_div == 'BULLISH') or (macd_div == 'BULLISH')
    bearish_div = (rsi_div == 'BEARISH') or (macd_div == 'BEARISH')

    # Existing positions
    pe_legs = get_existing_legs_count(positions, 'PE')
    ce_legs = get_existing_legs_count(positions, 'CE')

    # ============================================
    print()
    print("=" * 90)
    print(f"STAGED ENTRY DASHBOARD | {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 90)

    # Market state
    print(f"\nSPOT: {spot:,.2f} | VIX: {vix:.2f} | EXPIRY: {expiry} ({days_to_expiry}d) | ATM: {atm}")
    print(f"DAY: O={live['open']:,.0f} H={live['high']:,.0f} L={live['low']:,.0f} | Prev Close: {live['prev_close']:,.0f}")

    # OI walls
    print(f"\n--- OI WALLS ---")
    print("  CE (Resistance):")
    for strike, oi in ce_walls[:3]:
        marker = "  <-- TESTING" if resistance_test and resistance_test['strike'] == strike else ""
        print(f"    {strike}: {oi:>12,}{marker}")
    print("  PE (Support):")
    for strike, oi in pe_walls[:3]:
        marker = "  <-- TESTING" if support_test and support_test['strike'] == strike else ""
        print(f"    {strike}: {oi:>12,}{marker}")

    # Indicators
    print(f"\n--- HOURLY INDICATORS ---")
    print(f"  RSI(14):     {rsi_now:.1f}")
    print(f"  MACD Hist:   {macd_now:.2f}")
    print(f"  RSI Div:     {rsi_div if rsi_div else 'None'}")
    print(f"  MACD Div:    {macd_div if macd_div else 'None'}")

    # Position status
    print(f"\n--- CURRENT POSITIONS ---")
    print(f"  PE Spread Legs Open: {pe_legs}/3")
    print(f"  CE Spread Legs Open: {ce_legs}/3")
    if positions:
        for p in positions:
            print(f"    {p['tradingsymbol']}: Qty {p['quantity']} | LTP {p['last_price']:.2f}")

    # Trade decision
    print(f"\n--- ACTION ---")

    suggestion = None
    blocker = None

    if support_test:
        print(f"  🟡 SUPPORT TEST: {support_test['strike']} (distance {support_test['distance']:.0f}pts)")
        suggestion, blocker = suggest_staged_entry(
            spot, support_test['strike'],
            'BULLISH' if bullish_div else None,
            'PUT', expiry, oi_data, pe_legs
        )
    elif resistance_test:
        print(f"  🟡 RESISTANCE TEST: {resistance_test['strike']} (distance {resistance_test['distance']:.0f}pts)")
        suggestion, blocker = suggest_staged_entry(
            spot, resistance_test['strike'],
            'BEARISH' if bearish_div else None,
            'CALL', expiry, oi_data, ce_legs
        )
    else:
        # No test happening, but check if already in staged trade
        if pe_legs > 0:
            print(f"  📊 PE side has {pe_legs} legs open - monitoring for next level test")
        if ce_legs > 0:
            print(f"  📊 CE side has {ce_legs} legs open - monitoring for next level test")
        if pe_legs == 0 and ce_legs == 0:
            print(f"  ⚪ No level being tested. Watching for support/resistance approach.")

    if suggestion:
        net_credit = get_spread_premium(
            oi_data,
            suggestion['short_strike'],
            suggestion['long_strike'],
            suggestion['option_type']
        )
        credit_per_lot = net_credit * LOT_SIZE
        total_credit = credit_per_lot * suggestion['lots']
        max_loss_per_lot = (SPREAD_WIDTH * LOT_SIZE) - credit_per_lot
        total_max_loss = max_loss_per_lot * suggestion['lots']
        sl_amount = total_credit * SL_PERCENT
        target_amount = total_credit * TARGET_PERCENT

        print(f"\n  ✅ ENTRY SIGNAL: {suggestion['type']}")
        print(f"     Reason: {suggestion['reason']}")
        print(f"     SELL  {suggestion['short_strike']} {suggestion['option_type']} x {suggestion['lots']} lots")
        print(f"     BUY   {suggestion['long_strike']} {suggestion['option_type']} x {suggestion['lots']} lots")
        print(f"     Net Credit/lot: ₹{credit_per_lot:.0f}")
        print(f"     Total Credit:   ₹{total_credit:.0f}")
        print(f"     Max Loss:       ₹{total_max_loss:.0f}")
        print(f"     SL (50%):       Close at -₹{sl_amount:.0f}")
        print(f"     Target (35%):   Close at +₹{target_amount:.0f}")
    elif blocker:
        print(f"  ⏳ WAITING: {blocker}")

    print("\n" + "=" * 90)


# ==================== MAIN ====================

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=15) <= now <= now.replace(hour=15, minute=30)


def main():
    print("Loading staged entry dashboard...")

    expiry = get_nearest_expiry()
    if not expiry:
        print("ERROR: Could not find expiry.")
        return

    while True:
        live = get_live_data()
        if not live:
            time.sleep(60)
            continue

        atm = round(live['nifty'] / 50) * 50
        oi_data = get_oi_data(expiry, atm, range_strikes=15)

        if not oi_data:
            print("ERROR: Could not fetch OI data.")
            time.sleep(60)
            continue

        hourly_data = get_hourly_data(days=15)
        positions = get_positions()

        print_dashboard(live, oi_data, expiry, hourly_data, positions)

        if not is_market_open():
            print("\nMarket closed.")
            break
        else:
            print("\nNext refresh in 15 minutes... (Ctrl+C to exit)")
            time.sleep(900)


if __name__ == "__main__":
    main()
