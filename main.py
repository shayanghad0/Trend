import requests
import json
import sys
import time

def fetch_ohlc(symbol: str, timeframe: str, limit: int = 300):
    """
    Fetch OHLC data from the API.

    Args:
        symbol (str): Trading pair symbol (e.g., BTCUSDT).
        timeframe (str): One of: 1s, 1m, 5m, 10m, 15m, 30m, 45m, 1h, 2h, 4h, 1d.
        limit (int): Number of candles to retrieve (must be > 5).

    Returns:
        list or None: Parsed JSON response on success, None on failure.
    """
    url = f"http://localhost:3001/api/ohlc/{symbol}/{timeframe}"
    params = {"limit": limit}

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"❌ Request error: {e}")
        return None
    except json.JSONDecodeError:
        print("❌ Response is not valid JSON.")
        return None

def save_to_json(data, symbol: str, timeframe: str):
    """Save OHLC data to a JSON file with a timestamped filename."""
    filename = f"ohlc_{symbol}_{timeframe}_{int(time.time())}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Data saved to {filename}")

def get_user_input():
    """Prompt the user for symbol, timeframe, and limit interactively."""
    print("No command-line arguments provided. Enter the required details:")
    symbol = input("Symbol (e.g., BTCUSDT): ").strip()
    if not symbol:
        print("Symbol cannot be empty. Exiting.")
        sys.exit(1)

    valid_timeframes = {"1s", "1m", "5m", "10m", "15m", "30m", "45m", "1h", "2h", "4h", "1d"}
    while True:
        timeframe = input("Timeframe (1s, 1m, 5m, 10m, 15m, 30m, 45m, 1h, 2h, 4h, 1d): ").strip()
        if timeframe in valid_timeframes:
            break
        print(f"Invalid timeframe. Allowed: {', '.join(sorted(valid_timeframes))}")

    while True:
        limit_input = input("Number of candles (must be > 5, default 300): ").strip()
        if not limit_input:
            limit = 300
            break
        try:
            limit = int(limit_input)
            if limit > 5:
                break
            print("Limit must be greater than 5. Please try again.")
        except ValueError:
            print("Please enter a valid integer.")

    return symbol, timeframe, limit

if __name__ == "__main__":
    # If no arguments are given, interactively ask for inputs
    if len(sys.argv) == 1:
        symbol, timeframe, limit = get_user_input()
    else:
        # Command-line arguments: python script.py <symbol> <timeframe> [limit]
        if len(sys.argv) < 3:
            print("Usage: python script.py <symbol> <timeframe> [limit]")
            print("Or run without arguments for interactive input.")
            sys.exit(1)
        symbol = sys.argv[1]
        timeframe = sys.argv[2]

        # Parse and validate limit (if provided)
        if len(sys.argv) > 3:
            try:
                limit = int(sys.argv[3])
                if limit <= 5:
                    print("❌ Limit must be greater than 5. Please provide a larger value.")
                    sys.exit(1)
            except ValueError:
                print("❌ Limit must be an integer.")
                sys.exit(1)
        else:
            limit = 300  # default

    print(f"Fetching {limit} candles for {symbol} ({timeframe})...")
    ohlc_data = fetch_ohlc(symbol, timeframe, limit)

    if ohlc_data is not None:
        save_to_json(ohlc_data, symbol, timeframe)
    else:
        print("⚠️  No data received. Check the symbol and timeframe.")