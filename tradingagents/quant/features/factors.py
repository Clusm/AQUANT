"""A 股特有因子:连板、涨跌停距离、换手率 z-score、成交额 z-score。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def consecutive_limit_up(close: pd.Series, threshold: float = 0.097) -> pd.Series:
    """连板数:连续涨停天数,非涨停日重置为 0。

    M7: 向量化实现(原 O(n) Python 循环)。
    用 (is_lu==0).cumsum() 作为 reset 分组键,is_lu.groupby(reset).cumsum() 算连板数。
    """
    pct = close.pct_change()
    is_lu = (pct >= threshold).astype(int)
    reset = (is_lu == 0).cumsum()
    return is_lu.groupby(reset).cumsum().astype(float)


def limit_distance(close: pd.Series, threshold: float = 0.097) -> pd.Series:
    """距涨停距离:1 - (close / prev_close*1.1),>=0 表示未涨停,<0 表示已涨停。"""
    prev = close.shift(1)
    upper = prev * (1 + threshold)
    return (upper - close) / upper.replace(0, np.nan)


def turnover_zscore(turnover_rate: pd.Series, n: int = 20) -> pd.Series:
    """换手率相对过去 n 日的 z-score。"""
    mean = turnover_rate.rolling(n, min_periods=n // 2).mean()
    std = turnover_rate.rolling(n, min_periods=n // 2).std()
    return (turnover_rate - mean) / std.replace(0, np.nan)


def amount_zscore(amount: pd.Series, n: int = 20) -> pd.Series:
    """成交额相对过去 n 日的 z-score。"""
    mean = amount.rolling(n, min_periods=n // 2).mean()
    std = amount.rolling(n, min_periods=n // 2).std()
    return (amount - mean) / std.replace(0, np.nan)
