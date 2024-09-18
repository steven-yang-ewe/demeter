import pandas as pd
import talib


def standard_deviation_over_last(prices: pd.Series, minutes: int) -> pd.Series:

    # rolling_std = prices.rolling(window=minutes).std()
    rolling_std = talib.STDDEV(prices, timeperiod=minutes)
    return rolling_std


def average_true_range(lows: pd.Series, highs: pd.Series, closes: pd.Series, minutes: int) -> pd.Series:
    # tr1 = highs - lows
    # tr2 = abs(highs - closes.shift(1))
    # tr3 = abs(lows - closes.shift(1))
    # tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    #
    # atr = tr.rolling(window=minutes, min_periods=1).mean()
    atr = talib.ATR(highs, lows, closes, timeperiod=minutes)
    return atr
