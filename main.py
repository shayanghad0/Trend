#!/usr/bin/env python3
"""
main.py - OHLC Fetcher, Adaptive Trend Analyzer & Exporter

Usage:
    python main.py <symbol> <timeframe> [limit]
    python main.py                      (interactive mode)

Exports:
    - analysis_<symbol>_<timeframe>_<timestamp>.json   (metadata)
    - chart_<symbol>_<timeframe>_<timestamp>.png       (candlestick chart)
    - report_<symbol>_<timeframe>_<timestamp>.html     (analysis + chart)
"""

import requests
import json
import sys
import time
import math
import base64
from datetime import datetime
import os

# ============================================================================
#  OPTIONAL IMPORTS (matplotlib for charting)
# ============================================================================
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.ticker import FuncFormatter
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("⚠️  matplotlib not installed. Chart PNG/HTML will be skipped.")

# ============================================================================
#  FETCH
# ============================================================================

def fetch_ohlc(symbol: str, timeframe: str, limit: int = 300):
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
#  INDICATORS (improved)
# ============================================================================

def calculate_ema(data, period):
    """
    Exponential Moving Average of closing prices.
    Initialised with SMA of first 'period' values (standard approach).
    Returns the last value or None if insufficient data.
    """
    if len(data) < period:
        return None
    close = [c["close"] for c in data]
    # start with SMA
    sma = sum(close[:period]) / period
    ema = [sma] * period  # first 'period' values = SMA
    multiplier = 2 / (period + 1)
    for price in close[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema[-1] if ema else None

def calculate_ema_full(data, period):
    """Return the full EMA array (same length as data)."""
    if len(data) < period:
        return None
    close = [c["close"] for c in data]
    sma = sum(close[:period]) / period
    ema = [sma] * period
    multiplier = 2 / (period + 1)
    for price in close[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def calculate_atr(data, period=14):
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
    return sum(tr[-period:]) / period

def calculate_rsi(data, period=14):
    if len(data) < period + 1:
        return None
    close = [c["close"] for c in data]
    gains, losses = [], []
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

def linear_slope(data):
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
    avg_price = mean_y
    return slope / avg_price if avg_price != 0 else slope

# ============================================================================
#  PRICE ACTION DETECTORS (enhanced)
# ============================================================================

def find_swings(data, lookback=2):
    """Find swing highs and lows using local extrema."""
    highs = []
    lows = []
    n = len(data)
    for i in range(lookback, n - lookback):
        # swing high
        is_high = True
        for j in range(1, lookback + 1):
            if data[i]["high"] <= data[i-j]["high"] or data[i]["high"] <= data[i+j]["high"]:
                is_high = False
                break
        if is_high:
            highs.append((i, data[i]["high"]))
        # swing low
        is_low = True
        for j in range(1, lookback + 1):
            if data[i]["low"] >= data[i-j]["low"] or data[i]["low"] >= data[i+j]["low"]:
                is_low = False
                break
        if is_low:
            lows.append((i, data[i]["low"]))
    return highs, lows

def detect_market_structure(data):
    """
    Classify market structure: Uptrend, Downtrend, Range, Breakout, Pullback, Reversal.
    Uses swing highs/lows and recent price action.
    Returns dict with structure, detail, and swing info.
    """
    n = len(data)
    if n < 5:
        return {"structure": "Insufficient data", "detail": "", "swing_highs": [], "swing_lows": []}

    highs, lows = find_swings(data, lookback=2)
    last_close = data[-1]["close"]
    last_idx = n - 1

    # Determine if breakout
    lookback_break = min(20, n-1)
    recent = data[-lookback_break-1:-1]
    if recent:
        highest_prev = max(c["high"] for c in recent)
        lowest_prev = min(c["low"] for c in recent)
    else:
        highest_prev = None
        lowest_prev = None

    breakout = None
    if highest_prev and last_close > highest_prev:
        breakout = "UP"
    elif lowest_prev and last_close < lowest_prev:
        breakout = "DOWN"

    # Pullback detection: if we are in an uptrend but price has retraced some but not broken structure
    # Reversal: if we have previous uptrend and now lower highs/lows, or vice versa.
    # We'll implement a simplified version using swing points.

    # Determine if we have at least 2 swings each
    structure = "RANGE"
    detail = ""
    if len(highs) >= 2 and len(lows) >= 2:
        # Check for higher highs and higher lows
        hh = highs[-1][1] > highs[-2][1]
        ll = lows[-1][1] > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]
        hl = lows[-1][1] < lows[-2][1]

        if hh and ll:
            structure = "UPTREND"
            detail = "Higher Highs and Higher Lows"
        elif lh and hl:
            structure = "DOWNTREND"
            detail = "Lower Highs and Lower Lows"
        elif (hh and not ll) or (not hh and ll):
            structure = "PULLBACK"
            detail = "Mixed swings, possibly a pullback"
        else:
            structure = "RANGE"
            detail = "No clear directional swings"

        # Override if breakout detected
        if breakout == "UP":
            structure = "BREAKOUT"
            detail = "Bullish breakout above recent resistance"
        elif breakout == "DOWN":
            structure = "BREAKOUT"
            detail = "Bearish breakout below recent support"

        # Reversal detection: if we had a trend and now opposite swings
        # For simplicity, we'll check if last two swings show a change in direction
        # (complex, skip for now)
    else:
        # Fallback: use recent consecutive closes
        if n >= 5:
            recent_closes = [c["close"] for c in data[-5:]]
            if all(recent_closes[i] < recent_closes[i+1] for i in range(4)):
                structure = "UPTREND"
                detail = "Consecutive higher closes"
            elif all(recent_closes[i] > recent_closes[i+1] for i in range(4)):
                structure = "DOWNTREND"
                detail = "Consecutive lower closes"
            else:
                structure = "RANGE"
                detail = "Sideways"

    return {
        "structure": structure,
        "detail": detail,
        "swing_highs": highs,
        "swing_lows": lows,
        "breakout": breakout,
    }

def detect_candle_patterns(data):
    """Recognize common candlestick patterns on the last candle."""
    if len(data) < 2:
        return {"pattern": "None", "confidence": 0}
    last = data[-1]
    prev = data[-2]
    o, h, l, c = last["open"], last["high"], last["low"], last["close"]
    body = c - o
    high_low = h - l
    if high_low == 0:
        return {"pattern": "None", "confidence": 0}
    body_pct = abs(body) / high_low
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    pattern = "None"
    conf = 0

    # Doji: very small body
    if body_pct < 0.1:
        pattern = "Doji"
        conf = 70
    # Hammer: small body near top, long lower wick, minimal upper wick
    elif body > 0 and lower_wick > 2 * abs(body) and upper_wick < 0.3 * abs(body):
        pattern = "Hammer"
        conf = 70
    # Shooting Star: small body near bottom, long upper wick, minimal lower wick
    elif body < 0 and upper_wick > 2 * abs(body) and lower_wick < 0.3 * abs(body):
        pattern = "Shooting Star"
        conf = 70
    # Bullish Engulfing: current body > previous body and current close > previous open, current open < previous close
    elif data[-2]["close"] < data[-2]["open"] and body > 0 and c > data[-2]["open"] and o < data[-2]["close"]:
        pattern = "Bullish Engulfing"
        conf = 80
    # Bearish Engulfing
    elif data[-2]["close"] > data[-2]["open"] and body < 0 and c < data[-2]["open"] and o > data[-2]["close"]:
        pattern = "Bearish Engulfing"
        conf = 80

    return {"pattern": pattern, "confidence": conf}

def find_support_resistance_zones(data, lookback=20, zone_pct=0.005):
    """
    Find support/resistance zones based on swing highs/lows.
    Returns list of zones as tuples (price, type, strength).
    """
    if len(data) < lookback:
        return [], []
    highs, lows = find_swings(data, lookback=2)
    # Take last few swings
    recent_highs = [p for idx, p in highs if idx >= len(data)-lookback]
    recent_lows = [p for idx, p in lows if idx >= len(data)-lookback]
    # Cluster prices within zone_pct to form zones
    def cluster(prices, tol):
        if not prices:
            return []
        prices = sorted(prices)
        clusters = []
        current = [prices[0]]
        for p in prices[1:]:
            if abs(p - current[-1]) / current[-1] < tol:
                current.append(p)
            else:
                clusters.append(current)
                current = [p]
        clusters.append(current)
        # return average of each cluster
        return [sum(cl)/len(cl) for cl in clusters if len(cl) >= 2]
    res_zones = cluster(recent_highs, zone_pct) if recent_highs else []
    sup_zones = cluster(recent_lows, zone_pct) if recent_lows else []
    return res_zones, sup_zones

# ============================================================================
#  ADAPTIVE TREND SCORING ENGINE (improved)
# ============================================================================

def compute_trend_score(data):
    """
    Adaptive multi‑factor trend score based on number of candles.
    Returns dict with score, confidence, structure, patterns, etc.
    """
    n = len(data)
    if n < 5:
        return {
            "score": 0,
            "confidence": 0,
            "classification": "INSUFFICIENT DATA",
            "structure": {"structure": "Insufficient data", "detail": ""},
            "patterns": {"pattern": "None", "confidence": 0},
            "support_zones": [],
            "resistance_zones": [],
            "summary": "Not enough candles to perform analysis (minimum 5).",
            "details": [],
            "indicators": {}
        }

    # ---------------------- Market Structure (always) -----------------------
    structure = detect_market_structure(data)
    structure_type = structure["structure"]

    # ---------------------- Candle Patterns (always) -----------------------
    pattern = detect_candle_patterns(data)

    # ---------------------- Support/Resistance Zones (always) ----------------
    res_zones, sup_zones = find_support_resistance_zones(data, lookback=min(20, n-1))

    # ---------------------- Adaptive indicator selection --------------------
    # We'll compute indicators only if enough candles exist
    ema20 = calculate_ema(data, 20) if n >= 20 else None
    ema50 = calculate_ema(data, 50) if n >= 50 else None
    ema100 = calculate_ema(data, 100) if n >= 100 else None
    ema200 = calculate_ema(data, 200) if n >= 200 else None

    # For short-term, use EMA10 if available
    ema10 = calculate_ema(data, 10) if n >= 10 else None

    rsi14 = calculate_rsi(data, 14) if n >= 15 else None
    atr14 = calculate_atr(data, 14) if n >= 15 else None
    slope = linear_slope(data[-min(20, n):]) if n >= 5 else None

    # ---------------------- Weighted scoring --------------------------------
    # Weights: structure 30%, EMA alignment 25%, breakout 20%, momentum (RSI) 10%,
    # candle strength 5%, slope 5%, ATR 5% (but adaptive)
    score = 0
    max_score = 0
    details = []
    weights = []

    # 1. Market Structure (30%)
    struct_score = 0
    if structure_type == "UPTREND":
        struct_score = 1.0
    elif structure_type == "DOWNTREND":
        struct_score = -1.0
    elif structure_type == "BREAKOUT":
        # breakout direction
        if structure["breakout"] == "UP":
            struct_score = 1.0
        else:
            struct_score = -1.0
    else:  # RANGE, PULLBACK, etc.
        struct_score = 0.0
    score += struct_score * 3.0   # weight 30% of 10 = 3.0
    max_score += 3.0
    details.append(f"Market Structure: {structure_type} ({struct_score:+.1f})")

    # 2. EMA Alignment (25%) - only if we have enough data
    ema_score = 0
    ema_weights = 0
    if n >= 100 and ema20 and ema50 and ema100 and ema200:
        if ema20 > ema50 > ema100 > ema200:
            ema_score = 1.0
        elif ema20 < ema50 < ema100 < ema200:
            ema_score = -1.0
        else:
            # partial alignment
            if ema20 > ema50:
                ema_score += 0.5
            else:
                ema_score -= 0.5
            if ema50 > ema100:
                ema_score += 0.5
            else:
                ema_score -= 0.5
            if ema100 > ema200:
                ema_score += 0.5
            else:
                ema_score -= 0.5
            # normalize
            ema_score = max(-1, min(1, ema_score / 1.5))
        ema_weights = 1.0
    elif n >= 50 and ema20 and ema50:
        if ema20 > ema50:
            ema_score = 1.0
        else:
            ema_score = -1.0
        ema_weights = 1.0
    elif n >= 10 and ema10:
        last_close = data[-1]["close"]
        if last_close > ema10:
            ema_score = 0.5
        else:
            ema_score = -0.5
        ema_weights = 0.5  # lower weight when only EMA10

    if ema_weights > 0:
        score += ema_score * 2.5 * ema_weights  # 25% of 10 = 2.5
        max_score += 2.5 * ema_weights
        details.append(f"EMA Alignment: {ema_score:+.1f} (weighted {ema_weights:.1f})")

    # 3. Breakout (20%) - already captured in structure but we add a separate breakout factor
    # Actually breakout is already in structure, so we can skip or add extra for breakout strength
    # We'll add a small bonus/penalty if breakout is detected
    if structure_type == "BREAKOUT":
        if structure["breakout"] == "UP":
            score += 1.0
            details.append("Breakout bonus: +1.0 (Bullish)")
        else:
            score -= 1.0
            details.append("Breakout bonus: -1.0 (Bearish)")
        max_score += 1.0

    # 4. Momentum (RSI) (10%) - if available
    if rsi14 is not None:
        rsi_score = (rsi14 - 50) / 50  # scale from -1 to 1
        rsi_score = max(-1, min(1, rsi_score))
        score += rsi_score * 1.0
        max_score += 1.0
        details.append(f"RSI: {rsi14:.1f} ({rsi_score:+.2f})")

    # 5. Candle Strength (5%)
    last_body = abs(data[-1]["close"] - data[-1]["open"])
    last_range = data[-1]["high"] - data[-1]["low"]
    if last_range > 0:
        candle_power = last_body / last_range
        if data[-1]["close"] > data[-1]["open"]:
            candle_score = min(1, candle_power * 2 - 0.5)  # 0.5 to 1.0 for bullish
        else:
            candle_score = -min(1, candle_power * 2 - 0.5)
        score += candle_score * 0.5
        max_score += 0.5
        details.append(f"Candle Strength: {candle_score:+.2f}")

    # 6. Regression Slope (5%)
    if slope is not None:
        slope_score = max(-1, min(1, slope * 1000))  # normalize
        score += slope_score * 0.5
        max_score += 0.5
        details.append(f"Slope: {slope:.5f} ({slope_score:+.2f})")

    # 7. ATR (volatility) - just info, not directional
    atr_info = atr14 if atr14 is not None else 0
    atr_pct = None
    if atr14 is not None:
        avg_price = sum(c["close"] for c in data) / len(data)
        atr_pct = atr14 / avg_price * 100 if avg_price != 0 else 0
        details.append(f"ATR: {atr14:.2f} ({atr_pct:.2f}%)")

    # Normalize score to 0-10 range (since max_score might be less than 10)
    if max_score > 0:
        normalized = (score / max_score) * 10
    else:
        normalized = 0

    # Confidence = absolute score / max_score * 100
    confidence = abs(score) / max_score * 100 if max_score > 0 else 0

    # Classification based on normalized score
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

    # Generate human-friendly summary
    summary = ""
    if structure_type in ["UPTREND", "DOWNTREND", "BREAKOUT"]:
        summary = f"The market is in a {structure_type.lower()} with {classification.lower()}. "
    else:
        summary = f"The market is in a {structure_type.lower()} (sideways). "
    if pattern["pattern"] != "None":
        summary += f"A {pattern['pattern']} pattern was detected (confidence {pattern['confidence']}%). "
    if atr_pct is not None:
        if atr_pct > 2:
            summary += "Volatility is high. "
        elif atr_pct < 0.5:
            summary += "Volatility is low. "
    # Add recommendation
    if "BULLISH" in classification:
        summary += "Consider buying on pullbacks."
    elif "BEARISH" in classification:
        summary += "Consider selling on rallies."
    else:
        summary += "Wait for a clearer trend."

    return {
        "score": normalized,
        "raw_score": score,
        "max_possible": max_score,
        "confidence": confidence,
        "classification": classification,
        "structure": structure,
        "patterns": pattern,
        "support_zones": sup_zones,
        "resistance_zones": res_zones,
        "summary": summary,
        "details": details,
        "indicators": {
            "ema20": ema20,
            "ema50": ema50,
            "ema100": ema100,
            "ema200": ema200,
            "rsi14": rsi14,
            "atr14": atr14,
            "atr_pct": atr_pct,
            "slope": slope,
        }
    }

# ============================================================================
#  EXPORT FUNCTIONS (enhanced)
# ============================================================================

def export_metadata_json(result, symbol, timeframe, timestamp):
    filename = f"analysis_{symbol}_{timeframe}_{timestamp}.json"
    # Prepare a clean dict for JSON
    data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "datetime": datetime.now().isoformat(),
        "classification": result["classification"],
        "score": result["score"],
        "confidence": result["confidence"],
        "raw_score": result["raw_score"],
        "max_possible": result["max_possible"],
        "num_candles": result.get("num_candles", 0),
        "structure": result["structure"]["structure"],
        "structure_detail": result["structure"]["detail"],
        "pattern": result["patterns"]["pattern"],
        "pattern_confidence": result["patterns"]["confidence"],
        "support_zones": result["support_zones"],
        "resistance_zones": result["resistance_zones"],
        "summary": result["summary"],
        "details": result["details"],
        "indicators": result["indicators"]
    }
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Metadata saved to {filename}")
    return filename

def generate_chart(data, symbol, timeframe, result, timestamp):
    if not MATPLOTLIB_AVAILABLE:
        print("⚠️  matplotlib not installed. Skipping chart generation.")
        return None, None

    # Prepare data
    dates = [datetime.fromtimestamp(c["timestamp"]/1000) for c in data]
    opens = [c["open"] for c in data]
    highs = [c["high"] for c in data]
    lows = [c["low"] for c in data]
    closes = [c["close"] for c in data]

    # Compute EMAs for plotting (full arrays)
    ema20_full = calculate_ema_full(data, 20) if len(data) >= 20 else None
    ema50_full = calculate_ema_full(data, 50) if len(data) >= 50 else None
    ema100_full = calculate_ema_full(data, 100) if len(data) >= 100 else None
    ema200_full = calculate_ema_full(data, 200) if len(data) >= 200 else None

    fig, ax = plt.subplots(figsize=(12, 6))

    # Plot candlesticks
    width = 0.6
    for i, (o, h, l, c, dt) in enumerate(zip(opens, highs, lows, closes, dates)):
        color = 'green' if c >= o else 'red'
        body_bottom = min(o, c)
        body_height = abs(c - o)
        if body_height > 0:
            ax.add_patch(patches.Rectangle((i - width/2, body_bottom), width, body_height,
                                           facecolor=color, edgecolor=color, alpha=0.9))
        else:
            ax.plot([i - width/2, i + width/2], [o, o], color=color, linewidth=1)
        ax.plot([i, i], [l, h], color='black', linewidth=1, alpha=0.7)

    # Plot EMAs
    if ema20_full:
        ax.plot(range(len(ema20_full)), ema20_full, label='EMA20', color='blue', alpha=0.7)
    if ema50_full:
        ax.plot(range(len(ema50_full)), ema50_full, label='EMA50', color='orange', alpha=0.7)
    if ema100_full:
        ax.plot(range(len(ema100_full)), ema100_full, label='EMA100', color='purple', alpha=0.7)
    if ema200_full:
        ax.plot(range(len(ema200_full)), ema200_full, label='EMA200', color='red', alpha=0.7)

    # Support/Resistance zones (shaded)
    for zone in result["support_zones"]:
        ax.axhspan(zone - 0.005*zone, zone + 0.005*zone, alpha=0.2, color='green', label='Support' if zone == result["support_zones"][0] else "")
    for zone in result["resistance_zones"]:
        ax.axhspan(zone - 0.005*zone, zone + 0.005*zone, alpha=0.2, color='red', label='Resistance' if zone == result["resistance_zones"][0] else "")

    # Annotate pattern
    pattern = result["patterns"]["pattern"]
    if pattern != "None":
        ax.annotate(pattern, xy=(len(data)-1, closes[-1]), xytext=(len(data)-2, closes[-1]*1.02),
                    arrowprops=dict(facecolor='black', shrink=0.05), fontweight='bold')

    # Title with score and confidence
    title = f"{symbol} ({timeframe}) - {result['classification']} (Score: {result['score']:.1f}/10, Conf: {result['confidence']:.0f}%)"
    ax.set_title(title)
    ax.set_xlabel("Candle Index")
    ax.set_ylabel("Price")
    ax.legend(loc='upper left')

    # Format x-axis
    if len(dates) > 20:
        step = max(1, len(dates) // 10)
        ax.set_xticks(range(0, len(dates), step))
        ax.set_xticklabels([dates[i].strftime('%H:%M' if timeframe in ['1s','1m','5m','15m','30m','45m'] else '%d %H:%M')
                            for i in range(0, len(dates), step)], rotation=45, ha='right')
    else:
        ax.set_xticks(range(len(dates)))
        ax.set_xticklabels([d.strftime('%H:%M') for d in dates], rotation=45, ha='right')

    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    # Save PNG
    png_file = f"chart_{symbol}_{timeframe}_{timestamp}.png"
    fig.savefig(png_file, dpi=150, bbox_inches='tight')
    print(f"✅ Chart PNG saved to {png_file}")

    # Save HTML with embedded PNG
    with open(png_file, "rb") as img_f:
        img_b64 = base64.b64encode(img_f.read()).decode('utf-8')

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{symbol} ({timeframe}) Trend Analysis</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .container {{ max-width: 1000px; margin: auto; }}
        h1 {{ color: #2c3e50; }}
        .summary {{ background: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
        .score {{ font-weight: bold; font-size: 1.2em; }}
        .bullish {{ color: green; }}
        .bearish {{ color: red; }}
        .neutral {{ color: gray; }}
        .detail-list {{ list-style: none; padding: 0; }}
        .detail-list li {{ padding: 4px 0; border-bottom: 1px solid #eee; }}
        img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
        .zone {{ display: inline-block; margin: 2px 5px; padding: 2px 8px; border-radius: 3px; }}
        .support {{ background: #d4edda; }}
        .resistance {{ background: #f8d7da; }}
    </style>
</head>
<body>
<div class="container">
    <h1>{symbol} ({timeframe}) – Adaptive Trend Analysis</h1>
    <div class="summary">
        <p><strong>Classification:</strong> <span class="{'bullish' if 'BULLISH' in result['classification'] else 'bearish' if 'BEARISH' in result['classification'] else 'neutral'}">{result['classification']}</span></p>
        <p><strong>Score:</strong> {result['score']:.1f}/10 &nbsp;|&nbsp; <strong>Confidence:</strong> {result['confidence']:.0f}%</p>
        <p><strong>Market Structure:</strong> {result['structure']['structure']} ({result['structure']['detail']})</p>
        <p><strong>Latest Pattern:</strong> {result['patterns']['pattern']} (conf. {result['patterns']['confidence']}%)</p>
        <p><strong>Support Zones:</strong> {', '.join(f'<span class="zone support">{z:.2f}</span>' for z in result['support_zones'])}</p>
        <p><strong>Resistance Zones:</strong> {', '.join(f'<span class="zone resistance">{z:.2f}</span>' for z in result['resistance_zones'])}</p>
        <p><strong>Candles used:</strong> {result.get('num_candles', 0)}</p>
        <p><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <h2>Detail Breakdown</h2>
    <ul class="detail-list">
        {''.join(f'<li>{line}</li>' for line in result['details'])}
    </ul>
    <h2>Summary</h2>
    <p>{result['summary']}</p>
    <h2>Chart</h2>
    <img src="data:image/png;base64,{img_b64}" alt="Chart">
</div>
</body>
</html>"""

    html_file = f"report_{symbol}_{timeframe}_{timestamp}.html"
    with open(html_file, "w") as f:
        f.write(html_content)
    print(f"✅ HTML report saved to {html_file}")

    plt.close(fig)
    return png_file, html_file

# ============================================================================
#  MAIN
# ============================================================================

def save_to_json(data, symbol, timeframe):
    filename = f"ohlc_{symbol}_{timeframe}_{int(time.time())}.json"
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Raw OHLC saved to {filename}")

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

    save_to_json(ohlc, symbol, timeframe)

    result = compute_trend_score(ohlc)
    result["num_candles"] = len(ohlc)  # add for metadata

    ts = int(time.time())
    export_metadata_json(result, symbol, timeframe, ts)

    if MATPLOTLIB_AVAILABLE:
        generate_chart(ohlc, symbol, timeframe, result, ts)
    else:
        print("ℹ️  Install matplotlib to generate chart images and HTML reports.")
        print("   pip install matplotlib")