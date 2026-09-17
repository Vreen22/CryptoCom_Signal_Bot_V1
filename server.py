from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests
import time

app = FastAPI(title="Crypto.com Signal Bot V2")

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

    changes = [
        b - a
        for a, b in zip(
            values[-period-1:-1],
            values[-period:]
        )
    ]

    gains = sum(max(x, 0) for x in changes) / period
    losses = sum(max(-x, 0) for x in changes) / period

    if losses == 0:
        return 100

    return 100 - 100 / (1 + gains / losses)


def analyze(symbol, duration):

    rows = get_candles(
        symbol,
        tf="1m",
        count=220
    )

    closes = [
        float(x["c"])
        for x in rows
    ]

    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes)

    if (
        ema50 is None
        or ema200 is None
        or rsi14 is None
    ):
        raise RuntimeError(
            "Not enough candle data"
        )

    price = closes[-1]

    momentum = (
        (closes[-1] / closes[-6]) - 1
    ) * 100

    buy_score = 50.0
    sell_score = 50.0

    buy_reasons = []
    sell_reasons = []

    # EMA TREND
    if ema50 > ema200:

        buy_score += 18
        sell_score -= 18

        buy_reasons.append(
            "EMA50 > EMA200"
        )

    else:

        sell_score += 18
        buy_score -= 18

        sell_reasons.append(
            "EMA50 < EMA200"
        )

    # RSI
    if rsi14 >= 55:

        buy_score += 12

        buy_reasons.append(
            f"RSI14 {rsi14:.1f}"
        )

    elif rsi14 <= 45:

        sell_score += 12

        sell_reasons.append(
            f"RSI14 {rsi14:.1f}"
        )

    # MOMENTUM
    if momentum > 0:

        buy_score += min(
            10,
            abs(momentum) * 250
        )

        buy_reasons.append(
            "positive momentum"
        )

    elif momentum < 0:

        sell_score += min(
            10,
            abs(momentum) * 250
        )

        sell_reasons.append(
            "negative momentum"
        )

    # SUPPORT / RESISTANCE
    recent = closes[-30:]

    if price <= min(recent) * 1.003:

        buy_score += 8

        buy_reasons.append(
            "near support"
        )

    if price >= max(recent) * 0.997:

        sell_score += 8

        sell_reasons.append(
            "near resistance"
        )

    # FINAL SIGNAL
    if buy_score >= sell_score:

        direction = "BUY"

        confidence = round(
            min(buy_score, 100),
            1
        )

        reason = " + ".join(
            buy_reasons
        ) or "technical conditions"

    else:

        direction = "SELL"

        confidence = round(
            min(sell_score, 100),
            1
        )

        reason = " + ".join(
            sell_reasons
        ) or "technical conditions"

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
        "price": price,
        "ema50": ema50,
        "ema200": ema200,
        "rsi14": rsi14,
        "momentum_pct": momentum,
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
        "bot": "Crypto.com Signal Bot V2",
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
        symbol + ":" + duration
    )

    now = time.time()

    # Generate a new signal when:
    # 1. No previous signal exists
    # 2. Previous signal has expired
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
