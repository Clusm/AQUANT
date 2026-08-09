"""技术指标:MA、MACD、RSI、Bollinger、ATR。

所有函数接受 pd.Series 或 pd.DataFrame(按 group 计算)。
返回值与输入等长,NaN 填充前期。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ma_alignment(close: pd.Series) -> pd.Series:
    """MA5 > MA10 > MA20 多头排列 -> 1,否则 0。"""
    m5 = ma(close, 5)
    m10 = ma(close, 10)
    m20 = ma(close, 20)
    return ((m5 > m10) & (m10 > m20)).astype(float)


def price_above_ma(close: pd.Series, n: int = 20) -> pd.Series:
    return (close > ma(close, n)).astype(float)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (dif, dea, hist)。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def macd_golden_cross(close: pd.Series) -> pd.Series:
    """MACD 金叉(DIF 上穿 DEA)。"""
    dif, dea, _ = macd(close)
    return ((dif > dea) & (dif.shift(1) <= dea.shift(1))).astype(float)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    """返回 (upper, middle, lower, %b)。"""
    mid = close.rolling(n, min_periods=n).mean()
    std = close.rolling(n, min_periods=n).std()
    upper = mid + k * std
    lower = mid - k * std
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return upper, mid, lower, pct_b


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """ATR / close,用于波动率排序。"""
    return atr(high, low, close, n) / close


def returns(close: pd.Series, n: int) -> pd.Series:
    return close.pct_change(n)


def n_day_high(close: pd.Series, n: int = 20) -> pd.Series:
    """当日 close 是否为 N 日新高(不含今日)。"""
    return (close > close.rolling(n, min_periods=n).max().shift(1)).astype(float)


def pullback_to_ma(close: pd.Series, n: int = 20, tol: float = 0.02) -> pd.Series:
    """回踩 MA20(收盘价距 MA20 在 tol 范围内)。"""
    m = ma(close, n)
    return ((close - m).abs() / m < tol).astype(float)


def gap(open_: pd.Series, prev_close: pd.Series) -> pd.Series:
    """竞价缺口(今日开盘 / 昨收 - 1)。"""
    return open_ / prev_close - 1


def volume_ratio(volume: pd.Series, n: int = 5) -> pd.Series:
    """量比:今日量 / 过去 n 日均量。"""
    avg = volume.rolling(n, min_periods=n).mean().shift(1)
    return volume / avg.replace(0, np.nan)


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票的 DataFrame 添加全部指标列。

    要求列:open, high, low, close, volume, amount(可选)。
    """
    out = df.copy()
    c = out["close"]
    out["ma5"] = ma(c, 5)
    out["ma10"] = ma(c, 10)
    out["ma20"] = ma(c, 20)
    out["ma60"] = ma(c, 60)
    out["ma_alignment"] = ma_alignment(c)
    out["above_ma20"] = price_above_ma(c, 20)
    out["above_ma5"] = price_above_ma(c, 5)

    dif, dea, hist = macd(c)
    out["macd_dif"] = dif
    out["macd_dea"] = dea
    out["macd_hist"] = hist
    out["macd_golden_cross"] = macd_golden_cross(c)

    out["rsi_14"] = rsi(c, 14)
    out["rsi_6"] = rsi(c, 6)

    upper, mid, lower, pct_b = bollinger(c)
    out["boll_upper"] = upper
    out["boll_mid"] = mid
    out["boll_lower"] = lower
    out["boll_pct_b"] = pct_b

    out["atr_14"] = atr(out["high"], out["low"], c, 14)
    out["atr_pct"] = atr_pct(out["high"], out["low"], c, 14)

    out["ret_1d"] = returns(c, 1)
    out["ret_5d"] = returns(c, 5)
    out["ret_10d"] = returns(c, 10)
    out["ret_20d"] = returns(c, 20)

    out["new_high_20"] = n_day_high(c, 20)
    out["new_high_60"] = n_day_high(c, 60)
    out["pullback_ma20"] = pullback_to_ma(c, 20)

    out["gap"] = gap(out["open"], c.shift(1))
    out["volume_ratio_5"] = volume_ratio(out["volume"], 5)
    out["volume_ratio_10"] = volume_ratio(out["volume"], 10)
    return out
