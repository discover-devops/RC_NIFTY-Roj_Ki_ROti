import os
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

IST = pytz.timezone('Asia/Kolkata')
NIFTY_TOKEN = 256265


def get_nifty_spot():
    """Get current Nifty spot price"""
    quote = kite.quote([NIFTY_TOKEN])
    return quote[str(NIFTY_TOKEN)]['last_price']


def get_nearest_expiry():
    """Get the nearest weekly Nifty expiry (Tuesday)"""
    instruments = kite.instruments("NFO")
    nifty_options = [
        i for i in instruments
        if i['name'] == 'NIFTY' and i['instrument_type'] in ['CE', 'PE']
    ]

    if not nifty_options:
        return None

    today = datetime.now(IST).date()
    expiries = sorted(set(i['expiry'] for i in nifty_options if i['expiry'] >= today))

    return expiries[0] if expiries else None


def get_atm_strike(spot):
    """Round to nearest 50 for ATM strike"""
    return round(spot / 50) * 50


def get_oi_data(expiry, atm_strike):
    """Fetch OI for ATM ± 5 strikes (calls and puts)"""
    instruments = kite.instruments("NFO")

    # Filter to our expiry, ATM ± 5 strikes (50pt intervals = 250pts each side)
    target_strikes = [atm_strike + (i * 50) for i in range(-5, 6)]

    relevant = [
        i for i in instruments
        if i['name'] == 'NIFTY'
        and i['expiry'] == expiry
        and i['strike'] in target_strikes
        and i['instrument_type'] in ['CE', 'PE']
    ]

    if not relevant:
        return None

    # Build symbol list for quote API
    symbols = [f"NFO:{i['tradingsymbol']}" for i in relevant]

    # Fetch quotes (kite.quote handles up to 500 instruments)
    quotes = kite.quote(symbols)

    # Organize by strike
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


def main():
    print("=" * 100)
    print("NIFTY OPTION CHAIN - OI SNAPSHOT")
    print("=" * 100)

    now = datetime.now(IST)
    print(f"\nTimestamp: {now.strftime('%Y-%m-%d %H:%M:%S IST')}")

    # Get spot
    spot = get_nifty_spot()
    print(f"Nifty Spot: {spot:,.2f}")

    # Get expiry
    expiry = get_nearest_expiry()
    if not expiry:
        print("Could not find expiry.")
        return
    print(f"Expiry: {expiry}")

    # ATM
    atm = get_atm_strike(spot)
    print(f"ATM Strike: {atm}")
    print()

    # Fetch OI
    print("Fetching OI data...")
    data = get_oi_data(expiry, atm)

    if not data:
        print("No data found.")
        return

    # Print table
    print()
    print("=" * 100)
    print(f"{'CALL OI':>15} {'CALL VOL':>12} {'CALL LTP':>10} | {'STRIKE':^8} | {'PUT LTP':<10} {'PUT VOL':<12} {'PUT OI':<15}")
    print("-" * 100)

    for strike in sorted(data.keys()):
        ce = data[strike].get('CE', {})
        pe = data[strike].get('PE', {})

        marker = "  <-- ATM" if strike == atm else ""

        print(
            f"{ce.get('oi', 0):>15,} "
            f"{ce.get('volume', 0):>12,} "
            f"{ce.get('ltp', 0):>10,.2f} | "
            f"{strike:^8} | "
            f"{pe.get('ltp', 0):<10,.2f} "
            f"{pe.get('volume', 0):<12,} "
            f"{pe.get('oi', 0):<15,}"
            f"{marker}"
        )

    print("=" * 100)

    # Summary
    total_call_oi = sum(data[s].get('CE', {}).get('oi', 0) for s in data)
    total_put_oi = sum(data[s].get('PE', {}).get('oi', 0) for s in data)
    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

    print(f"\nSummary (ATM ± 5 strikes):")
    print(f"  Total Call OI: {total_call_oi:,}")
    print(f"  Total Put OI:  {total_put_oi:,}")
    print(f"  PCR (OI):      {pcr:.2f}")
    print()


if __name__ == "__main__":
    main()
