from collections.abc import Iterable


def moving_average(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        result.append(round(running / period, 4) if index >= period - 1 else None)
    return result


def ema(values: Iterable[float], period: int) -> list[float]:
    items = list(values)
    if not items:
        return []
    multiplier = 2 / (period + 1)
    result = [items[0]]
    for value in items[1:]:
        result.append(value * multiplier + result[-1] * (1 - multiplier))
    return result


def calculate_macd(closes: list[float]) -> list[dict[str, float | None]]:
    fast = ema(closes, 12)
    slow = ema(closes, 26)
    dif = [left - right for left, right in zip(fast, slow, strict=True)]
    signal = ema(dif, 9)
    return [
        {
            "dif": round(dif[index], 4),
            "signal": round(signal[index], 4),
            "histogram": round(dif[index] - signal[index], 4),
        }
        for index in range(len(closes))
    ]


def generate_macd_signals(macd: list[dict[str, float | None]]) -> list[str | None]:
    result: list[str | None] = [None] * len(macd)
    for index in range(1, len(macd)):
        previous = macd[index - 1]["histogram"]
        current = macd[index]["histogram"]
        dif = macd[index].get("dif")
        signal = macd[index].get("signal")
        if previous is None or current is None or dif is None or signal is None:
            continue
        if dif <= 0 or signal <= 0:
            continue
        if previous < 0 <= current:
            result[index] = "entry"
        elif previous >= 0 > current:
            result[index] = "exit"
    return result


def calculate_indicators(candles: list[dict]) -> list[dict]:
    closes = [float(item["close"]) for item in candles]
    averages = {period: moving_average(closes, period) for period in (5, 10, 20, 30, 60, 120, 240)}
    macd = calculate_macd(closes)
    signals = generate_macd_signals(macd)
    return [
        {
            "date": candle["date"],
            **{f"ma{period}": averages[period][index] for period in averages},
            **macd[index],
            "macdSignal": signals[index],
        }
        for index, candle in enumerate(candles)
    ]
