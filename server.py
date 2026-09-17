from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests
import time

app = FastAPI(title="Crypto.com Signal Bot V3")

API = "https://api.crypto.com/exchange/v1"

DURATIONS = [
    "1m", "2m", "3m", "5m", "10m",
    "15m", "30m", "1h", "2h", "3h"
]

DURATION_SECONDS = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "3h": 10800
}

# Crypto.com-supported analysis timeframes
ANALYSIS_TF = {
    "1m": "1m",
    "2m": "1m",
    "3m": "1m",
    "5m": "5m",
    "10m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "1h",
    "3h": "1h"
}

cache = {}

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static"
)


def get_candles(symbol, tf="1m", count=220):

    r = requests.get(
        API + "/public/get-candlestick",
        params={
            "instrument_name": symbol,
            "timeframe": tf,
            "count": count
        },
        timeout=12
    )

    r.raise_for_status()

    j = r.json()

    if j.get("code") != 0:
        raise RuntimeError(
            j.get("message", "Crypto.com API error")
        )

    data = j["result"]["data"]

    data.sort(key=lambda x: x["t"])

    return data


def ema(values, period):

    if len(values) < period:
        return None

    k = 2 / (period + 1)

    value = sum(values[:period]) / period

    for number in values[period:]:
        value = number * k + value * (1 - k)

    return value


def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def analyze(symbol, duration):

    tf = ANALYSIS_TF.get(
        duration,
        "1m"
    )

    rows = get_candles(
        symbol,
        tf=tf,
        count=220
    )

    closes = [
        float(x["c"])
        for x in rows
    ]

    highs = [
        float(x["h"])
        for x in rows
    ]

    lows = [
        float(x["l"])
        for x in rows
    ]

    if len(closes) < 205:
        raise RuntimeError(
            "Not enough candle data"
        )

    price = closes[-1]

    ema50 = ema(
        closes,
        50
    )

    ema200 = ema(
        closes,
        200
    )

    rsi14 = rsi(
        closes,
        14
    )

    if (
        ema50 is None
        or ema200 is None
        or rsi14 is None
    ):
        raise RuntimeError(
            "Indicator calculation failed"
        )

    # ------------------------------------------------
    # MOMENTUM
    # ------------------------------------------------

    lookback = 5

    momentum = (
        (closes[-1] / closes[-1 - lookback]) - 1
    ) * 100

    # ------------------------------------------------
    # SUPPORT / RESISTANCE
    # ------------------------------------------------

    recent_closes = closes[-30:]

    support = min(
        recent_closes
    )

    resistance = max(
        recent_closes
    )

    near_support = (
        price <= support * 1.003
    )

    near_resistance = (
        price >= resistance * 0.997
    )

    # ------------------------------------------------
    # SCORE SYSTEM
    # ------------------------------------------------

    buy_score = 0
    sell_score = 0

    buy_reasons = []
    sell_reasons = []

    # ------------------------------------------------
    # EMA TREND
    # ------------------------------------------------

    if ema50 > ema200:

        buy_score += 2

        buy_reasons.append(
            "EMA50 > EMA200"
        )

    elif ema50 < ema200:

        sell_score += 2

        sell_reasons.append(
            "EMA50 < EMA200"
        )

    # ------------------------------------------------
    # RSI
    # ------------------------------------------------

    if rsi14 >= 55:

        buy_score += 1

        buy_reasons.append(
            f"RSI14 {rsi14:.1f}"
        )

    elif rsi14 <= 45:

        sell_score += 1

        sell_reasons.append(
            f"RSI14 {rsi14:.1f}"
        )

    # ------------------------------------------------
    # MOMENTUM
    # ------------------------------------------------

    if momentum > 0.03:

        buy_score += 1

        buy_reasons.append(
            f"positive momentum {momentum:.3f}%"
        )

    elif momentum < -0.03:

        sell_score += 1

        sell_reasons.append(
            f"negative momentum {momentum:.3f}%"
        )

    # ------------------------------------------------
    # SUPPORT
    # ------------------------------------------------

    if near_support:

        buy_score += 1

        buy_reasons.append(
            "near support"
        )

    # ------------------------------------------------
    # RESISTANCE
    # ------------------------------------------------

    if near_resistance:

        sell_score += 1

        sell_reasons.append(
            "near resistance"
        )

    # ------------------------------------------------
    # FINAL SIGNAL
    # ------------------------------------------------

    difference = (
        buy_score - sell_score
    )

    # Strong BUY
    if buy_score >= 4 and difference >= 2:

        direction = "BUY"

        confidence = min(
            95,
            60 + (buy_score * 7)
        )

        reason = " + ".join(
            buy_reasons
        )

    # Strong SELL
    elif sell_score >= 4 and difference <= -2:

        direction = "SELL"

        confidence = min(
            95,
            60 + (sell_score * 7)
        )

        reason = " + ".join(
            sell_reasons
        )

    # Everything else = WAIT
    else:

        direction = "WAIT"

        confidence = 50

        all_reasons = []

        if buy_reasons:
            all_reasons.append(
                "BUY conditions: "
                + ", ".join(buy_reasons)
            )

        if sell_reasons:
            all_reasons.append(
                "SELL conditions: "
                + ", ".join(sell_reasons)
            )

        reason = (
            "Market conditions not strong enough"
        )

        if all_reasons:
            reason += " | " + " | ".join(
                all_reasons
            )

    # ------------------------------------------------
    # EXPIRY
    # ------------------------------------------------

    now = time.time()

    duration_seconds = DURATION_SECONDS.get(
        duration,
        900
    )

    expires_at = (
        now + duration_seconds
    )

    return {

        "symbol": symbol,

        "direction": direction,

        "confidence": confidence,

        "duration": duration,

        "analysis_timeframe": tf,

        "price": price,

        "ema50": ema50,

        "ema200": ema200,

        "rsi14": rsi14,

        "momentum_pct": momentum,

        "support": support,

        "resistance": resistance,

        "buy_score": buy_score,

        "sell_score": sell_score,

        "reason": reason,

        "created_at": now,

        "updated_at": now,

        "expires_at": expires_at,

        "auto_trade": False
    }


@app.get("/")
def home():

    return FileResponse(
        "app/static/index.html"
    )


@app.get("/health")
def health():

    return {

        "status": "online",

        "bot": "Crypto.com Signal Bot V3",

        "auto_trade": False
    }


@app.get("/api/signal")
def signal(
    symbol="BTC_USDT",
    duration="15m"
):

    symbol = symbol.upper()

    if duration not in DURATIONS:
        duration = "15m"

    key = (
        symbol
        + ":"
        + duration
    )

    now = time.time()

    # Create a new signal
    # when there is no signal
    # or previous signal expired

    if (
        key not in cache
        or now >= cache[key]["expires_at"]
    ):

        try:

            cache[key] = analyze(
                symbol,
                duration
            )

        except Exception as e:

            if key in cache:

                return {

                    "ok": True,

                    "stale": True,

                    **cache[key],

                    "error": str(e)
                }

            return {

                "ok": False,

                "error": str(e),

                "symbol": symbol,

                "duration": duration,

                "auto_trade": False
            }

    result = cache[key]

    remaining = max(
        0,
        int(
            result["expires_at"] - now
        )
    )

    return {

        "ok": True,

        **result,

        "remaining_seconds": remaining
    }
