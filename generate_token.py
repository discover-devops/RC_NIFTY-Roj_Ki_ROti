import os
from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect

load_dotenv()

api_key = os.getenv("KITE_API_KEY")
api_secret = os.getenv("KITE_API_SECRET")

kite = KiteConnect(api_key=api_key)

# Step 1: Print login URL
print("\n" + "=" * 70)
print("STEP 1: Open this URL in your browser and login:")
print("=" * 70)
print(kite.login_url())
print("=" * 70)

# Step 2: After login, browser will redirect to your redirect URL
# Copy the 'request_token' from the URL
print("\nAfter logging in, you'll be redirected to a URL like:")
print("https://yourredirect.com/?request_token=XXXXX&action=login&status=success")
print("\nCopy the 'request_token' value from that URL.")
print()

request_token = input("Paste request_token here: ").strip()

# Step 3: Generate access token
try:
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    print(f"\n✓ Access token generated successfully!")
    print(f"Token: {access_token}")

    # Update .env file
    env_path = ".env"
    set_key(env_path, "KITE_ACCESS_TOKEN", access_token)

    print(f"\n✓ .env file updated automatically")
    print(f"\nNow you can run: python monitor.py")

except Exception as e:
    print(f"\n✗ Error: {e}")
