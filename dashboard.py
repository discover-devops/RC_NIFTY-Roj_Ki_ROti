(venv) kumar@MyLabServer:~/nifty-monitor$ cat dashboard.py
import os
import time
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


# ==================== DATA FETCHERS ====================

def get_pivot_levels():
    try:
        today = datetime.now(IST).date()
        from_date = today - timedelta(days=5)

        data = kite.historical_data(
            instrument_token=NIFTY_TOKEN,
            from_date=from_date,
            to_date=today,
            interval="day"
        )

        if len(data) < 2:
            return None

        prev = data[-2] if data[-1]['date'].date() == today else data[-1]
        H, L, C = prev['high'], prev['low'], prev['close']
        P = (H + L + C) / 3

        return {
            'P': round(P, 2),
            'R1': round((2 * P) - L, 2),
            'R2': round(P + (H - L), 2),
            'R3': round(H + 2 * (P - L), 2),
            'S1': round((2 * P) - H, 2),
            'S2': round(P - (H - L), 2),
            'S3': round(L - 2 * (H - P), 2),
            'prev_high': H, 'prev_low': L, 'prev_close': C
        }
    except Exception as e:
        print(f"Pivot error: {e}")
        return None


def get_live_data():
    try:
        quotes = kite.quote([NIFTY_TOKEN, VIX_TOKEN])
        return {
            'nifty': quotes[str(NIFTY_TOKEN)]['last_price'],
            'nifty_high': quotes[str(NIFTY_TOKEN)]['ohlc']['high'],
            'nifty_low': quotes[str(NIFTY_TOKEN)]['ohlc']['low'],
            'nifty_open': quotes[str(NIFTY_TOKEN)]['ohlc']['open'],
            'nifty_prev_close': quotes[str(NIFTY_TOKEN)]['ohlc']['close'],
            'vix': quotes[str(VIX_TOKEN)]['last_price']
        }
    except Exception as e:
        print(f"Live data error: {e}")
        return None


def get_nearest_expiry():
    instruments = kite.instruments("NFO")
    nifty_options = [
        i for i in instruments
        if i['name'] == 'NIFTY' and i['instrument_type'] in ['CE', 'PE']
    ]
    today = datetime.now(IST).date()
    expiries = sorted(set(i['expiry'] for i in nifty_options if i['expiry'] >= today))
    return expiries[0] if expiries else None


def get_oi_data(expiry, atm_strike, range_strikes=10):
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
            'oi': q.get('oi', 0),
            'volume': q.get('volume', 0)
        }

    return data


# ==================== ANALYSIS ====================

def calculate_max_pain(oi_data):
    strikes = sorted(oi_data.keys())
    pain_values = {}

    for test_strike in strikes:
        total_pain = 0
        for strike in strikes:
            ce_oi = oi_data[strike].get('CE', {}).get('oi', 0)
            pe_oi = oi_data[strike].get('PE', {}).get('oi', 0)

            if test_strike > strike:
                total_pain += (test_strike - strike) * ce_oi
            if test_strike < strike:
                total_pain += (strike - test_strike) * pe_oi

        pain_values[test_strike] = total_pain

    return min(pain_values, key=pain_values.get)


def find_top_oi_strikes(oi_data, opt_type, top_n=3):
    strikes_with_oi = []
    for strike, data in oi_data.items():
        oi = data.get(opt_type, {}).get('oi', 0)
        if oi > 0:
            strikes_with_oi.append((strike, oi))
    strikes_with_oi.sort(key=lambda x: x[1], reverse=True)
    return strikes_with_oi[:top_n]


def calculate_pcr(oi_data):
    total_call_oi = sum(d.get('CE', {}).get('oi', 0) for d in oi_data.values())
    total_put_oi = sum(d.get('PE', {}).get('oi', 0) for d in oi_data.values())
    return total_put_oi / total_call_oi if total_call_oi > 0 else 0


def get_atm_strike(spot):
    return round(spot / 50) * 50


def detect_market_scenario(spot, prev_close, day_high, day_low, vix, vix_yesterday=None):
    """Identify which market scenario we're in"""
    gap = spot - prev_close
    gap_pct = (gap / prev_close) * 100 if prev_close else 0
    day_range = day_high - day_low

    if abs(gap_pct) < 0.3 and day_range < 100:
        return "FLAT_QUIET"
    elif gap_pct > 0.5:
        return "GAP_UP"
    elif gap_pct < -0.5:
        return "GAP_DOWN"
    elif day_range > 200:
        return "VOLATILE"
    else:
        return "NORMAL"


def suggest_trade(spot, vix, days_to_expiry, top_calls, top_puts, max_pain, scenario, pcr):
    """
    Smart trade suggestion based on:
    - Distance from spot to OI walls
    - Market scenario
    - VIX level
    - Days to expiry
    """
    suggestions = []
    warnings = []

    # Filter checks
    if days_to_expiry <= 1:
        warnings.append("Too close to expiry — gamma risk too high")
        return None, warnings

    if vix > 22:
        warnings.append(f"VIX too high ({vix:.1f}) — wait for cooling")
        return None, warnings

    if vix < 12:
        warnings.append(f"VIX too low ({vix:.1f}) — premiums too thin")
        return None, warnings

    # Get strongest walls
    strongest_resistance = top_calls[0][0] if top_calls else None
    strongest_support = top_puts[0][0] if top_puts else None

    if not strongest_resistance or not strongest_support:
        warnings.append("OI data incomplete")
        return None, warnings

    # Calculate distances
    dist_to_resistance = strongest_resistance - spot
    dist_to_support = spot - strongest_support

    # Scenario-based logic
    if scenario == "GAP_UP":
        # Skip Bull Put if gap up (might be trending up)
        if dist_to_resistance >= 200:
            suggestions.append({
                'type': 'BEAR_CALL_ONLY',
                'short_ce': strongest_resistance,
                'long_ce': strongest_resistance + 200,
                'short_pe': None,
                'long_pe': None,
                'reason': 'Gap up — only sell calls above OI wall, skip put side'
            })
        else:
            warnings.append("Gap up + spot too close to call wall — skip")

    elif scenario == "GAP_DOWN":
        # Skip Bear Call if gap down (might be trending down)
        if dist_to_support >= 200:
            suggestions.append({
                'type': 'BULL_PUT_ONLY',
                'short_ce': None,
                'long_ce': None,
                'short_pe': strongest_support,
                'long_pe': strongest_support - 200,
                'reason': 'Gap down — only sell puts below OI wall, skip call side'
            })
        else:
            warnings.append("Gap down + spot too close to put wall — skip")

    elif scenario in ["FLAT_QUIET", "NORMAL"]:
        # Iron Condor possible if both sides have room
        if dist_to_resistance >= 150 and dist_to_support >= 150:
            suggestions.append({
                'type': 'IRON_CONDOR',
                'short_ce': strongest_resistance,
                'long_ce': strongest_resistance + 200,
                'short_pe': strongest_support,
                'long_pe': strongest_support - 200,
                'reason': f'Range-bound, both sides have room. Spot {dist_to_support}pts from PE wall, {dist_to_resistance}pts from CE wall'
            })
        elif dist_to_resistance >= 200:
            suggestions.append({
                'type': 'BEAR_CALL_ONLY',
                'short_ce': strongest_resistance,
                'long_ce': strongest_resistance + 200,
                'short_pe': None,
                'long_pe': None,
                'reason': 'Spot closer to support — only sell call side'
            })
        elif dist_to_support >= 200:
            suggestions.append({
                'type': 'BULL_PUT_ONLY',
                'short_ce': None,
                'long_ce': None,
                'short_pe': strongest_support,
                'long_pe': strongest_support - 200,
                'reason': 'Spot closer to resistance — only sell put side'
            })
        else:
            warnings.append("Spot too close to both walls — skip")

    elif scenario == "VOLATILE":
        warnings.append("Day too volatile (range >200pts) — wait for calm")
        return None, warnings

    return suggestions[0] if suggestions else None, warnings


# ==================== DISPLAY ====================

def print_dashboard(live, pivots, oi_data, expiry):
    now = datetime.now(IST)
    spot = live['nifty']
    vix = live['vix']
    atm = get_atm_strike(spot)
    days_to_expiry = (expiry - now.date()).days

    # Analysis
    max_pain = calculate_max_pain(oi_data)
    top_calls = find_top_oi_strikes(oi_data, 'CE', 3)
    top_puts = find_top_oi_strikes(oi_data, 'PE', 3)
    pcr = calculate_pcr(oi_data)

    strongest_resistance = top_calls[0][0] if top_calls else None
    strongest_support = top_puts[0][0] if top_puts else None

    scenario = detect_market_scenario(
        spot, live['nifty_prev_close'],
        live['nifty_high'], live['nifty_low'], vix
    )

    # ============================================
    print()
    print("=" * 80)
    print(f"NIFTY DASHBOARD | {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 80)

    # Live data
    gap = spot - live['nifty_prev_close']
    gap_pct = (gap / live['nifty_prev_close']) * 100 if live['nifty_prev_close'] else 0

    print(f"\nSPOT: {spot:,.2f} ({gap:+.0f} / {gap_pct:+.2f}%) | VIX: {vix:.2f}")
    print(f"DAY: O={live['nifty_open']:,.0f} H={live['nifty_high']:,.0f} L={live['nifty_low']:,.0f}")
    print(f"EXPIRY: {expiry} ({days_to_expiry} days) | ATM: {atm} | SCENARIO: {scenario}")

    # Pivot levels
    print("\n--- PIVOT LEVELS (Technical) ---")
    print(f"  R3: {pivots['R3']:>9,.0f}  R2: {pivots['R2']:>9,.0f}  R1: {pivots['R1']:>9,.0f}")
    print(f"  Pivot: {pivots['P']:>6,.0f}")
    print(f"  S1: {pivots['S1']:>9,.0f}  S2: {pivots['S2']:>9,.0f}  S3: {pivots['S3']:>9,.0f}")

    # OI walls
    print("\n--- OI WALLS (Smart Money) ---")
    print("  Resistance (Call OI):")
    for strike, oi in top_calls:
        marker = "  <-- STRONGEST" if strike == strongest_resistance else ""
        print(f"    {strike}: {oi:>12,}{marker}")
    print("  Support (Put OI):")
    for strike, oi in top_puts:
        marker = "  <-- STRONGEST" if strike == strongest_support else ""
        print(f"    {strike}: {oi:>12,}{marker}")

    # Key metrics
    print(f"\n--- KEY METRICS ---")
    print(f"  Max Pain:     {max_pain}")
    print(f"  PCR (OI):     {pcr:.2f} ({'Bullish' if pcr > 1.2 else 'Bearish' if pcr < 0.8 else 'Neutral'})")
    print(f"  Spot vs MP:   {spot - max_pain:+.0f} pts")

    # Position read
    print(f"\n--- POSITION READ ---")
    if strongest_support and strongest_resistance:
        dist_s = spot - strongest_support
        dist_r = strongest_resistance - spot

        if spot < strongest_support - 50:
            print(f"  ⚠️  BELOW OI support ({strongest_support}) by {strongest_support - spot:.0f}pts")
        elif spot > strongest_resistance + 50:
            print(f"  ⚠️  ABOVE OI resistance ({strongest_resistance}) by {spot - strongest_resistance:.0f}pts")
        else:
            print(f"  🟢  IN RANGE: Support {strongest_support} ({dist_s:+.0f}pts) | Resistance {strongest_resistance} ({dist_r:+.0f}pts)")

    # Trade suggestion
    print(f"\n--- TRADE SUGGESTION ---")
    suggestion, warnings = suggest_trade(
        spot, vix, days_to_expiry, top_calls, top_puts, max_pain, scenario, pcr
    )

    if warnings:
        for w in warnings:
            print(f"  ⚠️  {w}")

    if suggestion:
        print(f"\n  Type: {suggestion['type']}")
        print(f"  Logic: {suggestion['reason']}")
        print(f"\n  STRIKES:")
        if suggestion.get('short_ce'):
            print(f"    SELL {suggestion['short_ce']} CE | BUY {suggestion['long_ce']} CE")
        if suggestion.get('short_pe'):
            print(f"    SELL {suggestion['short_pe']} PE | BUY {suggestion['long_pe']} PE")
    elif not warnings:
        print("  No trade setup matches current conditions.")

    print("\n" + "=" * 80)


# ==================== MAIN ====================

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=15) <= now <= now.replace(hour=15, minute=30)


def main():
    print("Loading dashboard...")

    pivots = get_pivot_levels()
    if not pivots:
        print("ERROR: Could not load pivot data.")
        return

    expiry = get_nearest_expiry()
    if not expiry:
        print("ERROR: Could not find expiry.")
        return

    while True:
        live = get_live_data()
        if not live:
            time.sleep(60)
            continue

        atm = get_atm_strike(live['nifty'])
        oi_data = get_oi_data(expiry, atm, range_strikes=10)

        if not oi_data:
            print("ERROR: Could not fetch OI data.")
            time.sleep(60)
            continue

        print_dashboard(live, pivots, oi_data, expiry)

        if not is_market_open():
            print("\nMarket closed. Dashboard ran once.")
            break

        print("\nNext refresh in 15 minutes... (Ctrl+C to exit)")
        time.sleep(900)


if __name__ == "__main__":
    main()
(venv) kumar@MyLabServer:~/nifty-monitor$
