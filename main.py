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
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("⚠️  matplotlib not installed. Chart PNG/HTML will be skipped.")

# ============================================================================
#  FETCH (improved to handle dict responses)
# ============================================================================

def fetch_ohlc(symbol: str, timeframe: str, limit: int = 300):
    url = f"http://localhost:3001/api/ohlc/{symbol}/{timeframe}"
    params = {"limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # If the response is a dict with a 'data' key, extract it
        if isinstance(data, dict):
            if "data" in data:
                data = data["data"]
            elif "result" in data:
                data = data["result"]
            else:
                # Maybe it's a single candle? Convert to list
                if all(k in data for k in ("timestamp", "open", "high", "low", "close")):
                    data = [data]
                else:
                    print("⚠️  Unexpected JSON structure:", list(data.keys()))
                    return None

        if not isinstance(data, list):
            print("⚠️  Response is not a list of candles.")
            return None

        # Validate first candle has required keys
        if data and not all(k in data[0] for k in ("timestamp", "open", "high", "low", "close")):
            print("⚠️  Missing required keys in candle data.")
            return None

        return data
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return None

# ============================================================================
#  INDICATORS
# ============================================================================

def calculate_ema(data, period):
    if len(data) < period:
        return None
    close = [c["close"] for c in data]
    sma = sum(close[:period]) / period
    ema = [sma] * period
    multiplier = 2 / (period + 1)
    for price in close[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema[-1] if ema else None

def calculate_ema_full(data, period):
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
    return num / den  # raw slope (price per index)

# ============================================================================
#  PRICE ACTION DETECTORS (enhanced)
# ============================================================================

def find_swings(data, lookback=None):
    n = len(data)
    if lookback is None:
        lookback = max(2, n // 20)
        lookback = min(lookback, n//2)  # cap
    highs = []
    lows = []
    for i in range(lookback, n - lookback):
        is_high = True
        for j in range(1, lookback + 1):
            if data[i]["high"] <= data[i-j]["high"] or data[i]["high"] <= data[i+j]["high"]:
                is_high = False
                break
        if is_high:
            highs.append((i, data[i]["high"]))
        is_low = True
        for j in range(1, lookback + 1):
            if data[i]["low"] >= data[i-j]["low"] or data[i]["low"] >= data[i+j]["low"]:
                is_low = False
                break
        if is_low:
            lows.append((i, data[i]["low"]))
    return highs, lows

def detect_market_structure(data):
    n = len(data)
    if n < 5:
        return {"structure": "Insufficient", "detail": "", "swing_highs": [], "swing_lows": [], "breakout": None}

    highs, lows = find_swings(data)
    last_close = data[-1]["close"]
    last_idx = n - 1

    # Breakout detection (improved)
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
        # Confirm breakout: body > 60% of range and close near high
        last = data[-1]
        body = abs(last["close"] - last["open"])
        range_ = last["high"] - last["low"]
        if range_ > 0 and body / range_ > 0.6 and (last["high"] - last["close"]) < 0.3 * range_:
            breakout = "UP"
    elif lowest_prev and last_close < lowest_prev:
        last = data[-1]
        body = abs(last["close"] - last["open"])
        range_ = last["high"] - last["low"]
        if range_ > 0 and body / range_ > 0.6 and (last["close"] - last["low"]) < 0.3 * range_:
            breakout = "DOWN"

    # Structure from swings
    structure = "RANGE"
    detail = ""
    if len(highs) >= 2 and len(lows) >= 2:
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
            detail = "Mixed swings, likely pullback"
        else:
            structure = "RANGE"
            detail = "No clear direction"
    else:
        # Fallback: consecutive closes
        if n >= 5:
            recent_closes = [c["close"] for c in data[-5:]]
            if all(recent_closes[i] < recent_closes[i+1] for i in range(4)):
                structure = "UPTREND"
                detail = "Consecutive higher closes"
            elif all(recent_closes[i] > recent_closes[i+1] for i in range(4)):
                structure = "DOWNTREND"
                detail = "Consecutive lower closes"

    # Override with breakout if detected
    if breakout == "UP":
        structure = "BREAKOUT"
        detail = "Bullish breakout above resistance"
    elif breakout == "DOWN":
        structure = "BREAKOUT"
        detail = "Bearish breakout below support"

    return {
        "structure": structure,
        "detail": detail,
        "swing_highs": highs,
        "swing_lows": lows,
        "breakout": breakout,
    }

def detect_candle_patterns(data):
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

    # Marubozu (no wicks)
    if body_pct > 0.9:
        if body > 0:
            pattern = "Bullish Marubozu"
            conf = 80
        else:
            pattern = "Bearish Marubozu"
            conf = 80
    # Doji
    elif body_pct < 0.1:
        pattern = "Doji"
        conf = 70
    # Hammer (bullish)
    elif body > 0 and lower_wick > 2 * abs(body) and upper_wick < 0.3 * abs(body):
        pattern = "Hammer"
        conf = 75
    # Shooting Star (bearish)
    elif body < 0 and upper_wick > 2 * abs(body) and lower_wick < 0.3 * abs(body):
        pattern = "Shooting Star"
        conf = 75
    # Bullish Engulfing
    elif prev["close"] < prev["open"] and body > 0 and c > prev["open"] and o < prev["close"]:
        pattern = "Bullish Engulfing"
        conf = 85
    # Bearish Engulfing
    elif prev["close"] > prev["open"] and body < 0 and c < prev["open"] and o > prev["close"]:
        pattern = "Bearish Engulfing"
        conf = 85
    # Harami (bullish)
    elif prev["close"] > prev["open"] and body > 0 and o > prev["close"] and c < prev["open"]:
        pattern = "Bullish Harami"
        conf = 70
    # Harami (bearish)
    elif prev["close"] < prev["open"] and body < 0 and o < prev["close"] and c > prev["open"]:
        pattern = "Bearish Harami"
        conf = 70
    # Piercing Line (bullish)
    elif prev["close"] < prev["open"] and body > 0 and o < prev["close"] and c > (prev["open"] + prev["close"])/2:
        pattern = "Piercing Line"
        conf = 75
    # Dark Cloud Cover (bearish)
    elif prev["close"] > prev["open"] and body < 0 and o > prev["close"] and c < (prev["open"] + prev["close"])/2:
        pattern = "Dark Cloud Cover"
        conf = 75
    # Three White Soldiers – requires 3 candles; we'll check last 3
    if len(data) >= 3:
        c1, c2, c3 = data[-3], data[-2], data[-1]
        if c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"]:
            if (c2["close"]-c2["open"]) > (c1["close"]-c1["open"]) and (c3["close"]-c3["open"]) > (c2["close"]-c2["open"]):
                pattern = "Three White Soldiers"
                conf = 85
    # Three Black Crows
    if len(data) >= 3:
        c1, c2, c3 = data[-3], data[-2], data[-1]
        if c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"]:
            if (c1["open"]-c1["close"]) < (c2["open"]-c2["close"]) and (c2["open"]-c2["close"]) < (c3["open"]-c3["close"]):
                pattern = "Three Black Crows"
                conf = 85
    # Inside Bar (second candle inside previous)
    if len(data) >= 2:
        if data[-1]["high"] < data[-2]["high"] and data[-1]["low"] > data[-2]["low"]:
            pattern = "Inside Bar"
            conf = 60
    # Outside Bar (second candle engulfs previous) – already covered by engulfing

    return {"pattern": pattern, "confidence": conf}

def find_support_resistance_zones(data, lookback=20, zone_pct=0.005):
    if len(data) < lookback:
        return [], []
    highs, lows = find_swings(data)
    recent_highs = [p for idx, p in highs if idx >= len(data)-lookback]
    recent_lows = [p for idx, p in lows if idx >= len(data)-lookback]

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
                if len(current) >= 2:
                    clusters.append(sum(current)/len(current))
                current = [p]
        if len(current) >= 2:
            clusters.append(sum(current)/len(current))
        return clusters

    res_zones = cluster(recent_highs, zone_pct)
    sup_zones = cluster(recent_lows, zone_pct)
    return res_zones, sup_zones

# ============================================================================
#  TREND STAGE CLASSIFICATION
# ============================================================================

def classify_trend_stage(structure, rsi, atr_pct, n, breakout):
    stage = structure  # default

    if structure == "UPTREND":
        if rsi is not None and rsi > 70:
            stage = "MARKUP (Overextended)"
        elif rsi is not None and rsi > 50:
            stage = "MARKUP (Healthy)"
        else:
            stage = "MARKUP (Weak)"
    elif structure == "DOWNTREND":
        if rsi is not None and rsi < 30:
            stage = "MARKDOWN (Oversold)"
        elif rsi is not None and rsi < 50:
            stage = "MARKDOWN (Healthy)"
        else:
            stage = "MARKDOWN (Weak)"
    elif structure == "RANGE":
        if atr_pct is not None and atr_pct < 0.5:
            stage = "ACCUMULATION/DISTRIBUTION (Low Vol)"
        else:
            stage = "RANGE"
    elif structure == "BREAKOUT":
        if breakout == "UP":
            stage = "BREAKOUT (Bullish)"
        else:
            stage = "BREAKOUT (Bearish)"
    elif structure == "PULLBACK":
        stage = "PULLBACK"

    return stage

# ============================================================================
#  ADAPTIVE TREND SCORING ENGINE (revised)
# ============================================================================

def compute_trend_score(data):
    n = len(data)
    print(f"ℹ️  compute_trend_score received {n} candles.")

    if n < 5:
        return {
            "score": 0,
            "confidence": 0,
            "classification": "INSUFFICIENT DATA",
            "structure": {"structure": "Insufficient", "detail": ""},
            "patterns": {"pattern": "None", "confidence": 0},
            "support_zones": [],
            "resistance_zones": [],
            "stage": "Unknown",
            "summary": f"Not enough candles (min 5). Got {n}.",
            "details": [f"Only {n} candles received."],
            "indicators": {},
            "agreement": 0,
            "signal_count": 0,
            "num_candles": n
        }

    # Compute all indicators (some may be None)
    ema20 = calculate_ema(data, 20) if n >= 20 else None
    ema50 = calculate_ema(data, 50) if n >= 50 else None
    ema100 = calculate_ema(data, 100) if n >= 100 else None
    ema200 = calculate_ema(data, 200) if n >= 200 else None
    ema10 = calculate_ema(data, 10) if n >= 10 else None

    rsi14 = calculate_rsi(data, 14) if n >= 15 else None
    atr14 = calculate_atr(data, 14) if n >= 15 else None
    slope = linear_slope(data[-min(20, n):]) if n >= 5 else None

    # Normalize slope by ATR if available
    slope_norm = None
    if slope is not None and atr14 is not None and atr14 > 0:
        slope_norm = slope / atr14

    # Market structure
    structure = detect_market_structure(data)
    struct_dir = 0
    if structure["structure"] in ["UPTREND", "BREAKOUT"]:
        if structure.get("breakout") == "UP" or structure["structure"] == "UPTREND":
            struct_dir = 1
    elif structure["structure"] in ["DOWNTREND"]:
        struct_dir = -1
    elif structure.get("breakout") == "DOWN":
        struct_dir = -1
    else:
        struct_dir = 0  # RANGE, PULLBACK, etc.

    # EMA alignment direction
    ema_dir = 0
    ema_available = False
    if ema20 and ema50 and ema100 and ema200:
        ema_available = True
        if ema20 > ema50 > ema100 > ema200:
            ema_dir = 1
        elif ema20 < ema50 < ema100 < ema200:
            ema_dir = -1
        else:
            if ema20 > ema50:
                ema_dir += 1
            else:
                ema_dir -= 1
            if ema50 > ema100:
                ema_dir += 1
            else:
                ema_dir -= 1
            if ema100 > ema200:
                ema_dir += 1
            else:
                ema_dir -= 1
            ema_dir = 1 if ema_dir > 0 else (-1 if ema_dir < 0 else 0)
    elif ema20 and ema50:
        ema_available = True
        ema_dir = 1 if ema20 > ema50 else -1
    elif ema10:
        ema_available = True
        last_close = data[-1]["close"]
        ema_dir = 1 if last_close > ema10 else -1

    # Breakout direction from structure
    breakout_dir = 0
    if structure.get("breakout") == "UP":
        breakout_dir = 1
    elif structure.get("breakout") == "DOWN":
        breakout_dir = -1

    # RSI zone (confirmation, not direction)
    rsi_dir = 0
    if rsi14 is not None:
        if rsi14 > 70:
            rsi_dir = -1  # overbought – bearish signal
        elif rsi14 > 55:
            rsi_dir = 1   # bullish confirmation
        elif rsi14 < 30:
            rsi_dir = 1   # oversold – bullish reversal potential
        elif rsi14 < 45:
            rsi_dir = -1  # bearish confirmation
        else:
            rsi_dir = 0   # neutral

    # Pattern direction
    pat = detect_candle_patterns(data)
    pat_dir = 0
    if pat["pattern"] in ["Bullish Engulfing", "Hammer", "Piercing Line", "Three White Soldiers", "Bullish Harami", "Bullish Marubozu"]:
        pat_dir = 1
    elif pat["pattern"] in ["Bearish Engulfing", "Shooting Star", "Dark Cloud Cover", "Three Black Crows", "Bearish Harami", "Bearish Marubozu"]:
        pat_dir = -1

    # Slope direction
    slope_dir = 0
    if slope_norm is not None:
        if slope_norm > 0.01:
            slope_dir = 1
        elif slope_norm < -0.01:
            slope_dir = -1

    # Weights (sum to 100%)
    w_struct = 40
    w_ema = 20 if ema_available else 0
    w_breakout = 15 if breakout_dir != 0 else 0
    w_rsi = 10 if rsi14 is not None else 0
    w_pattern = 10 if pat["pattern"] != "None" else 0
    w_slope = 5 if slope_dir != 0 else 0

    total_weight = w_struct + w_ema + w_breakout + w_rsi + w_pattern + w_slope
    if total_weight == 0:
        total_weight = 100
        w_struct = 100

    # Compute weighted score (normalized to -10..10)
    score = (struct_dir * w_struct +
             ema_dir * w_ema +
             breakout_dir * w_breakout +
             rsi_dir * w_rsi +
             pat_dir * w_pattern +
             slope_dir * w_slope) / total_weight * 10

    # Determine agreement: count signals that agree with final direction
    final_dir = 1 if score > 1.5 else (-1 if score < -1.5 else 0)
    signals = []
    if struct_dir != 0:
        signals.append(struct_dir)
    if ema_available and ema_dir != 0:
        signals.append(ema_dir)
    if breakout_dir != 0:
        signals.append(breakout_dir)
    if rsi14 is not None and rsi_dir != 0:
        signals.append(rsi_dir)
    if pat["pattern"] != "None" and pat_dir != 0:
        signals.append(pat_dir)
    if slope_dir != 0:
        signals.append(slope_dir)

    if final_dir != 0 and len(signals) > 0:
        agreeing = sum(1 for s in signals if s == final_dir)
        confidence = agreeing / len(signals) * 100
    else:
        confidence = 50

    # Classification
    if score >= 7:
        classification = "STRONG BULLISH"
    elif score >= 4:
        classification = "BULLISH"
    elif score >= 1.5:
        classification = "WEAK BULLISH"
    elif score >= -1.5:
        classification = "SIDEWAYS"
    elif score >= -4:
        classification = "WEAK BEARISH"
    elif score >= -7:
        classification = "BEARISH"
    else:
        classification = "STRONG BEARISH"

    # Trend stage
    atr_pct = None
    if atr14 is not None:
        avg_price = sum(c["close"] for c in data) / len(data)
        atr_pct = atr14 / avg_price * 100 if avg_price != 0 else 0
    stage = classify_trend_stage(structure["structure"], rsi14, atr_pct, n, structure.get("breakout"))

    # Support/Resistance zones
    res_zones, sup_zones = find_support_resistance_zones(data, lookback=min(20, n-1))

    # Details
    details = []
    details.append(f"Market Structure: {structure['structure']} ({structure['detail']}) -> dir {struct_dir:+.1f}")
    if ema_available:
        details.append(f"EMA Alignment: dir {ema_dir:+.1f}")
    if breakout_dir != 0:
        details.append(f"Breakout: dir {breakout_dir:+.1f}")
    if rsi14 is not None:
        details.append(f"RSI: {rsi14:.1f} -> dir {rsi_dir:+.1f}")
    if pat["pattern"] != "None":
        details.append(f"Pattern: {pat['pattern']} (conf {pat['confidence']}%) -> dir {pat_dir:+.1f}")
    if slope_dir != 0:
        details.append(f"Slope (norm): {slope_norm:.4f} -> dir {slope_dir:+.1f}")

    # Summary
    summary = f"The market is {structure['structure'].lower()} with {classification.lower()}. "
    if pat["pattern"] != "None":
        summary += f"A {pat['pattern']} pattern was detected (confidence {pat['confidence']}%). "
    if atr_pct is not None:
        summary += f"Volatility is {'high' if atr_pct > 2 else 'low' if atr_pct < 0.5 else 'normal'}. "
    if "BULLISH" in classification:
        summary += "Consider buying on pullbacks."
    elif "BEARISH" in classification:
        summary += "Consider selling on rallies."
    else:
        summary += "Wait for a clearer trend."

    return {
        "score": score,
        "confidence": confidence,
        "classification": classification,
        "structure": structure,
        "patterns": pat,
        "stage": stage,
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
            "slope_norm": slope_norm,
        },
        "agreement": confidence,
        "signal_count": len(signals),
        "num_candles": n,
    }

# ============================================================================
#  EXPORT FUNCTIONS
# ============================================================================

def export_metadata_json(result, symbol, timeframe, timestamp):
    filename = f"analysis_{symbol}_{timeframe}_{timestamp}.json"
    data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "datetime": datetime.now().isoformat(),
        "classification": result["classification"],
        "score": result["score"],
        "confidence": result["confidence"],
        "structure": result["structure"]["structure"],
        "structure_detail": result["structure"]["detail"],
        "stage": result["stage"],
        "pattern": result["patterns"]["pattern"],
        "pattern_confidence": result["patterns"]["confidence"],
        "support_zones": result["support_zones"],
        "resistance_zones": result["resistance_zones"],
        "summary": result["summary"],
        "details": result["details"],
        "indicators": result["indicators"],
        "num_candles": result["num_candles"],
        "agreement": result["agreement"],
        "signal_count": result["signal_count"],
    }
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Metadata saved to {filename}")
    return filename

def generate_chart(data, symbol, timeframe, result, timestamp):
    if not MATPLOTLIB_AVAILABLE:
        print("⚠️  matplotlib not installed. Skipping chart generation.")
        return None, None

    dates = [datetime.fromtimestamp(c["timestamp"]/1000) for c in data]
    opens = [c["open"] for c in data]
    highs = [c["high"] for c in data]
    lows = [c["low"] for c in data]
    closes = [c["close"] for c in data]

    ema20_full = calculate_ema_full(data, 20) if len(data) >= 20 else None
    ema50_full = calculate_ema_full(data, 50) if len(data) >= 50 else None
    ema100_full = calculate_ema_full(data, 100) if len(data) >= 100 else None
    ema200_full = calculate_ema_full(data, 200) if len(data) >= 200 else None

    fig, ax = plt.subplots(figsize=(12, 6))

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

    # Support/Resistance zones
    for zone in result["support_zones"]:
        ax.axhspan(zone - 0.005*zone, zone + 0.005*zone, alpha=0.2, color='green', label='Support' if zone == result["support_zones"][0] else "")
    for zone in result["resistance_zones"]:
        ax.axhspan(zone - 0.005*zone, zone + 0.005*zone, alpha=0.2, color='red', label='Resistance' if zone == result["resistance_zones"][0] else "")

    # Pattern annotation
    pat = result["patterns"]["pattern"]
    if pat != "None":
        ax.annotate(pat, xy=(len(data)-1, closes[-1]), xytext=(len(data)-2, closes[-1]*1.02),
                    arrowprops=dict(facecolor='black', shrink=0.05), fontweight='bold')

    title = f"{symbol} ({timeframe}) - {result['classification']} (Score: {result['score']:.1f}/10, Conf: {result['confidence']:.0f}%)"
    ax.set_title(title)
    ax.set_xlabel("Candle Index")
    ax.set_ylabel("Price")

    # Only add legend if there are labelled artists
    if ax.get_legend_handles_labels()[1]:
        ax.legend(loc='upper left')

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

    png_file = f"chart_{symbol}_{timeframe}_{timestamp}.png"
    fig.savefig(png_file, dpi=150, bbox_inches='tight')
    print(f"✅ Chart PNG saved to {png_file}")

    # HTML report
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
        <p><strong>Trend Stage:</strong> {result['stage']}</p>
        <p><strong>Latest Pattern:</strong> {result['patterns']['pattern']} (conf. {result['patterns']['confidence']}%)</p>
        <p><strong>Support Zones:</strong> {', '.join(f'<span class="zone support">{z:.2f}</span>' for z in result['support_zones'])}</p>
        <p><strong>Resistance Zones:</strong> {', '.join(f'<span class="zone resistance">{z:.2f}</span>' for z in result['resistance_zones'])}</p>
        <p><strong>Candles used:</strong> {result['num_candles']}</p>
        <p><strong>Signal Agreement:</strong> {result['agreement']:.0f}% ({result['signal_count']} signals)</p>
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

def print_terminal_analysis(result, symbol, timeframe):
    print("\n" + "=" * 60)
    print(f"📊  {symbol} ({timeframe}) – Trend Analysis")
    print("=" * 60)
    print(f"Classification : {result['classification']}")
    print(f"Score          : {result['score']:.1f}/10")
    print(f"Confidence     : {result['confidence']:.0f}%  (based on {result['signal_count']} signals agreeing)")
    print(f"Market Structure : {result['structure']['structure']} ({result['structure']['detail']})")
    print(f"Trend Stage    : {result['stage']}")
    print(f"Latest Pattern : {result['patterns']['pattern']} (conf. {result['patterns']['confidence']}%)")
    support = ', '.join(f'{z:.2f}' for z in result['support_zones'][:3])
    resistance = ', '.join(f'{z:.2f}' for z in result['resistance_zones'][:3])
    print(f"Support Zones  : {support if support else 'None'}")
    print(f"Resistance Zones: {resistance if resistance else 'None'}")
    print("\n--- Details ---")
    for line in result['details']:
        print(f"  {line}")
    print("\n📝  Summary:")
    print(f"  {result['summary']}")
    print("=" * 60)

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

    print(f"✅ Received {len(ohlc)} candles.")
    save_to_json(ohlc, symbol, timeframe)

    result = compute_trend_score(ohlc)
    result["num_candles"] = len(ohlc)

    ts = int(time.time())
    export_metadata_json(result, symbol, timeframe, ts)

    if MATPLOTLIB_AVAILABLE:
        generate_chart(ohlc, symbol, timeframe, result, ts)
    else:
        print("ℹ️  Install matplotlib to generate chart images and HTML reports.")
        print("   pip install matplotlib")

    print_terminal_analysis(result, symbol, timeframe)