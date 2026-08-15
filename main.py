#!/usr/bin/env python3
"""
main.py - OHLC Data Fetcher & Multi-Factor Trend Analyzer

Usage:
    python main.py <symbol> <timeframe> [limit]
    python main.py                      (interactive mode)

Example:
    python main.py BTCUSDT 1h 100
"""

import requests
import json
import sys
import time
import math
from datetime import datetime

# ============================================================================
#  FETCH
# ============================================================================

def fetch_ohlc(symbol: str, timeframe: str, limit: int = 300):
    """Fetch OHLC data from the local API."""
    url = f"http://localhost:3001/api/ohlc/{symbol}/{timeframe}"
    params = {"limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            print("⚠️  Unexpected response format (not a list).")
            return None
        return data
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return None

# ============================================================================
#  INDICATORS (OHLC only)
# ============================================================================

def calculate_ema(data, period):
    """Exponential Moving Average of closing prices."""
    if len(data) < period:
        return None
    close = [c["close"] for c in data]
    multiplier = 2 / (period + 1)
    ema = [close[0]]
    for price in close[1:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema[-1]

def calculate_atr(data, period=14):
    """Average True Range."""
    if len(data) < period + 1:
        return None
    tr = []
    for i in range(1, len(data)):
        high = data[i]["high"]
        low = data[i]["low"]
        prev_close = data[i-1]["close"]
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(tr) < period:
        return None
    # Simple average of the last 'period' TR values
    return sum(tr[-period:]) / period

def calculate_rsi(data, period=14):
    """Relative Strength Index."""
    if len(data) < period + 1:
        return None
    close = [c["close"] for c in data]
    gains = []
    losses = []
    for i in range(1, len(close)):
        diff = close[i] - close[i-1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_adx(data, period=14):
    """
    Average Directional Index (requires at least period*2 bars).
    Returns ADX value or None if insufficient data.
    """
    if len(data) < period * 2:
        return None
    high = [c["high"] for c in data]
    low = [c["low"] for c in data]
    close = [c["close"] for c in data]

    # True Range
    tr = []
    for i in range(1, len(data)):
        tr.append(max(high[i] - low[i],
                      abs(high[i] - close[i-1]),
                      abs(low[i] - close[i-1])))

    # +DM and -DM
    plus_dm = []
    minus_dm = []
    for i in range(1, len(data)):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        if up > down and up > 0:
            plus_dm.append(up)
        else:
            plus_dm.append(0)
        if down > up and down > 0:
            minus_dm.append(down)
        else:
            minus_dm.append(0)

    # Smooth using Wilder's method (or simple average for simplicity)
    # We'll use simple average for the first period then smoothing
    atr = []
    for i in range(period, len(tr)):
        atr.append(sum(tr[i-period:i]) / period)

    if len(atr) < period:
        return None

    # +DI and -DI
    plus_di = []
    minus_di = []
    for i in range(period, len(plus_dm)):
        pdm_avg = sum(plus_dm[i-period:i]) / period
        mdm_avg = sum(minus_dm[i-period:i]) / period
        atr_val = sum(tr[i-period:i]) / period
        if atr_val != 0:
            plus_di.append(100 * pdm_avg / atr_val)
            minus_di.append(100 * mdm_avg / atr_val)
        else:
            plus_di.append(0)
            minus_di.append(0)

    # DX = |+DI - -DI| / (+DI + -DI) * 100
    dx = []
    for p, m in zip(plus_di, minus_di):
        if (p + m) != 0:
            dx.append(abs(p - m) / (p + m) * 100)
        else:
            dx.append(0)

    # ADX = average of DX over 'period'
    if len(dx) < period:
        return None
    return sum(dx[-period:]) / period

def linear_slope(data):
    """
    Slope of linear regression of closing prices.
    Returns slope coefficient (normalized by price level).
    """
    n = len(data)
    if n < 3:
        return None
    x = list(range(n))
    y = [c["close"] for c in data]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den = sum((x[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None
    slope = num / den
    # Normalize by average price to make comparable across assets
    avg_price = mean_y
    return slope / avg_price if avg_price != 0 else slope

# ============================================================================
#  PRICE ACTION DETECTORS
# ============================================================================

def detect_hh_hl(data):
    """
    Detect Higher Highs / Higher Lows using a simple comparison of recent
    swing points (local extrema). Returns 'UP', 'DOWN', or 'RANGE'.
    """
    if len(data) < 5:
        return "RANGE"

    # Find swing highs and lows (compare with neighbours)
    highs = []
    lows = []
    for i in range(2, len(data)-2):
        if data[i]["high"] > data[i-1]["high"] and data[i]["high"] > data[i-2]["high"] and \
           data[i]["high"] > data[i+1]["high"] and data[i]["high"] > data[i+2]["high"]:
            highs.append((i, data[i]["high"]))
        if data[i]["low"] < data[i-1]["low"] and data[i]["low"] < data[i-2]["low"] and \
           data[i]["low"] < data[i+1]["low"] and data[i]["low"] < data[i+2]["low"]:
            lows.append((i, data[i]["low"]))

    if len(highs) < 2 or len(lows) < 2:
        # Fallback: compare last few closes
        recent = data[-5:]
        if all(recent[i]["close"] < recent[i+1]["close"] for i in range(4)):
            return "UP"
        if all(recent[i]["close"] > recent[i+1]["close"] for i in range(4)):
            return "DOWN"
        return "RANGE"

    # Check if swing highs are rising and swing lows are rising
    last_highs = [h[1] for h in highs[-2:]]
    last_lows = [l[1] for l in lows[-2:]]

    high_up = last_highs[-1] > last_highs[-2] if len(last_highs) >= 2 else False
    low_up = last_lows[-1] > last_lows[-2] if len(last_lows) >= 2 else False

    if high_up and low_up:
        return "UP"
    elif (not high_up) and (not low_up):
        return "DOWN"
    else:
        return "RANGE"

def breakout_detection(data, lookback=20):
    """
    Detect if the last candle closes above the highest high of the lookback
    period (bullish) or below the lowest low (bearish).
    Returns 'BULLISH', 'BEARISH', or None.
    """
    if len(data) < lookback + 1:
        return None
    recent = data[-lookback-1:-1]  # exclude last candle
    highest = max(c["high"] for c in recent)
    lowest = min(c["low"] for c in recent)
    last_close = data[-1]["close"]
    if last_close > highest:
        return "BULLISH"
    elif last_close < lowest:
        return "BEARISH"
    else:
        return None

def candle_strength(data):
    """
    Assess the strength of the last candle.
    Returns +1 if bullish and body > 0.7*range, -1 if bearish, else 0.
    """
    if len(data) == 0:
        return 0
    c = data[-1]
    body = c["close"] - c["open"]
    high_low = c["high"] - c["low"]
    if high_low == 0:
        return 0
    strength = abs(body) / high_low
    if body > 0 and strength > 0.7:
        return 1
    elif body < 0 and strength > 0.7:
        return -1
    else:
        return 0

def support_resistance_score(data, lookback=20):
    """
    Check if price is near a recent resistance (high) or support (low).
    Returns +1 if above resistance, -1 if below support, else 0.
    """
    if len(data) < lookback:
        return 0
    recent = data[-lookback-1:-1]  # exclude last candle
    highest = max(c["high"] for c in recent)
    lowest = min(c["low"] for c in recent)
    last_close = data[-1]["close"]
    # Use a tolerance (0.5% of price)
    tol = 0.005 * last_close
    if last_close > highest + tol:
        return 1
    elif last_close < lowest - tol:
        return -1
    else:
        return 0

# ============================================================================
#  TREND SCORING
# ============================================================================

def compute_trend_score(data):
    """
    Compute a multi-factor trend score using available indicators.
    Returns dict with score, breakdown, and overall classification.
    """
    n = len(data)
    score = 0
    details = []
    max_score = 0

    # 1. EMA Alignment (if enough data)
    ema_20 = calculate_ema(data, 20)
    ema_50 = calculate_ema(data, 50)
    ema_100 = calculate_ema(data, 100)
    ema_200 = calculate_ema(data, 200)

    ema_scores = []
    if ema_20 and ema_50 and ema_100 and ema_200:
        if ema_20 > ema_50 > ema_100 > ema_200:
            ema_scores.append(2)
        elif ema_20 < ema_50 < ema_100 < ema_200:
            ema_scores.append(-2)
        else:
            # Check shorter term
            if ema_20 > ema_50:
                ema_scores.append(1)
            elif ema_20 < ema_50:
                ema_scores.append(-1)
            else:
                ema_scores.append(0)
        max_score += 2
    elif ema_20 and ema_50:
        if ema_20 > ema_50:
            ema_scores.append(1)
        else:
            ema_scores.append(-1)
        max_score += 1
    else:
        # Use a short EMA if available
        ema_5 = calculate_ema(data, 5)
        if ema_5:
            last_close = data[-1]["close"]
            if last_close > ema_5:
                ema_scores.append(1)
            else:
                ema_scores.append(-1)
            max_score += 1

    if ema_scores:
        ema_avg = sum(ema_scores) / len(ema_scores)
        score += ema_avg
        details.append(f"EMA Alignment: {ema_avg:+.1f}")

    # 2. HH/HL detection
    trend_dir = detect_hh_hl(data)
    if trend_dir == "UP":
        score += 2
        details.append("HH/HL: +2 (Uptrend)")
        max_score += 2
    elif trend_dir == "DOWN":
        score -= 2
        details.append("HH/HL: -2 (Downtrend)")
        max_score += 2
    else:
        details.append("HH/HL: 0 (Range)")
        # max_score unchanged

    # 3. ADX (trend strength)
    adx = calculate_adx(data, 14)
    if adx is not None:
        if adx > 40:
            score += 2
            details.append(f"ADX: +2 ({adx:.1f} Very Strong)")
        elif adx > 25:
            score += 1
            details.append(f"ADX: +1 ({adx:.1f} Good Trend)")
        elif adx > 20:
            details.append(f"ADX: 0 ({adx:.1f} Beginning Trend)")
        else:
            score -= 1
            details.append(f"ADX: -1 ({adx:.1f} No Trend)")
        max_score += 2

    # 4. ATR (volatility) - just inform, not directional
    atr = calculate_atr(data, 14)
    if atr is not None:
        avg_price = sum(c["close"] for c in data) / len(data)
        atr_pct = atr / avg_price * 100 if avg_price != 0 else 0
        if atr_pct > 2.0:
            score += 1  # high volatility can favour trend continuation
            details.append(f"ATR: +1 (High Volatility {atr_pct:.2f}%)")
        elif atr_pct < 0.5:
            score -= 0.5
            details.append(f"ATR: -0.5 (Low Volatility {atr_pct:.2f}%)")
        else:
            details.append(f"ATR: 0 ({atr_pct:.2f}%)")
        max_score += 1

    # 5. Breakout
    breakout = breakout_detection(data, lookback=min(20, n-1))
    if breakout == "BULLISH":
        score += 2
        details.append("Breakout: +2 (Bullish)")
        max_score += 2
    elif breakout == "BEARISH":
        score -= 2
        details.append("Breakout: -2 (Bearish)")
        max_score += 2
    else:
        details.append("Breakout: 0 (No breakout)")

    # 6. Linear regression slope
    slope = linear_slope(data[-min(20, n):])  # use recent 20 or less
    if slope is not None:
        if slope > 0.001:  # threshold
            score += 1
            details.append(f"Slope: +1 ({slope:.5f} positive)")
        elif slope < -0.001:
            score -= 1
            details.append(f"Slope: -1 ({slope:.5f} negative)")
        else:
            details.append(f"Slope: 0 ({slope:.5f} near zero)")
        max_score += 1

    # 7. Candle structure (last candle strength)
    candle = candle_strength(data)
    if candle == 1:
        score += 1
        details.append("Candle: +1 (Strong Bullish)")
        max_score += 1
    elif candle == -1:
        score -= 1
        details.append("Candle: -1 (Strong Bearish)")
        max_score += 1
    else:
        details.append("Candle: 0 (Neutral)")

    # 8. Support/Resistance proximity
    sr = support_resistance_score(data, lookback=min(20, n-1))
    if sr == 1:
        score += 1
        details.append("S/R: +1 (Above resistance)")
        max_score += 1
    elif sr == -1:
        score -= 1
        details.append("S/R: -1 (Below support)")
        max_score += 1
    else:
        details.append("S/R: 0 (Inside range)")

    # 9. RSI (momentum)
    rsi = calculate_rsi(data, 14)
    if rsi is not None:
        if rsi > 60:
            score += 1
            details.append(f"RSI: +1 ({rsi:.1f} Bullish momentum)")
        elif rsi < 40:
            score -= 1
            details.append(f"RSI: -1 ({rsi:.1f} Bearish momentum)")
        else:
            details.append(f"RSI: 0 ({rsi:.1f} Neutral)")
        max_score += 1

    # Normalize score relative to max possible
    if max_score > 0:
        normalized = score / max_score * 10  # scale to 0-10
    else:
        normalized = 0

    # Determine classification
    if normalized >= 7:
        classification = "STRONG BULLISH"
    elif normalized >= 4:
        classification = "BULLISH"
    elif normalized >= 1.5:
        classification = "WEAK BULLISH"
    elif normalized >= -1.5:
        classification = "SIDEWAYS"
    elif normalized >= -4:
        classification = "WEAK BEARISH"
    elif normalized >= -7:
        classification = "BEARISH"
    else:
        classification = "STRONG BEARISH"

    return {
        "score": normalized,
        "raw_score": score,
        "max_possible": max_score,
        "details": details,
        "classification": classification,
        "num_candles": n,
        "adx": adx,
        "atr": atr,
        "rsi": rsi,
    }

# ============================================================================
#  OUTPUT
# ============================================================================

def print_analysis(result, symbol, timeframe):
    """Pretty print the trend analysis."""
    print("\n" + "=" * 50)
    print(f"{symbol} ({timeframe})")
    print("=" * 50)
    print(f"Trend: {result['classification']}")
    print(f"Score: {result['score']:.1f}/10")
    print(f"Candles used: {result['num_candles']}")
    if result.get('adx') is not None:
        print(f"ADX: {result['adx']:.1f}")
    if result.get('atr') is not None:
        avg_price = 0  # we could compute
        print(f"ATR: {result['atr']:.2f}")
    if result.get('rsi') is not None:
        print(f"RSI: {result['rsi']:.1f}")

    print("\nBreakdown:")
    for line in result['details']:
        print(f"  {line}")

    # Recommendation based on classification
    if "BULLISH" in result['classification']:
        print("\nRecommendation: BUY ON PULLBACK")
    elif "BEARISH" in result['classification']:
        print("\nRecommendation: SELL ON RALLY")
    else:
        print("\nRecommendation: WAIT FOR CLEAR TREND")

    print("=" * 50 + "\n")

# ============================================================================
#  MAIN
# ============================================================================

def save_to_json(data, symbol, timeframe):
    filename = f"ohlc_{symbol}_{timeframe}_{int(time.time())}.json"
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Data saved to {filename}")

def get_user_input():
    print("No command-line arguments provided. Enter details interactively.")
    symbol = input("Symbol (e.g., BTCUSDT): ").strip()
    if not symbol:
        print("Symbol required.")
        sys.exit(1)

    valid_tf = {"1s","1m","5m","10m","15m","30m","45m","1h","2h","4h","1d"}
    while True:
        tf = input("Timeframe (1s,1m,5m,10m,15m,30m,45m,1h,2h,4h,1d): ").strip()
        if tf in valid_tf:
            break
        print("Invalid timeframe. Try again.")

    while True:
        lim = input("Number of candles (>5, default 300): ").strip()
        if not lim:
            limit = 300
            break
        try:
            limit = int(lim)
            if limit > 5:
                break
            print("Limit must be > 5.")
        except ValueError:
            print("Please enter a valid integer.")
    return symbol, tf, limit

if __name__ == "__main__":
    # Parse arguments or interactive
    if len(sys.argv) == 1:
        symbol, timeframe, limit = get_user_input()
    else:
        if len(sys.argv) < 3:
            print("Usage: python main.py <symbol> <timeframe> [limit]")
            sys.exit(1)
        symbol = sys.argv[1]
        timeframe = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
        if limit <= 5:
            print("❌ Limit must be > 5.")
            sys.exit(1)

    print(f"Fetching {limit} candles for {symbol} ({timeframe})...")
    ohlc = fetch_ohlc(symbol, timeframe, limit)
    if not ohlc:
        print("Failed to fetch data. Exiting.")
        sys.exit(1)

    # Save raw data
    save_to_json(ohlc, symbol, timeframe)

    # Analyze
    result = compute_trend_score(ohlc)
    print_analysis(result, symbol, timeframe)