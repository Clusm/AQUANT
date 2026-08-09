"""策略抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from tradingagents.quant.backtest.engine import Signal


class BaseStrategy(ABC):
    """所有策略必须实现 generate_signals。"""

    name: str = "base"

    @abstractmethod
    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        """在 current_date 收盘后生成 T+1 买入信号。

        返回最多 top_k 个买入信号。
        """
        ...

    def should_exit(self, position, today_row: pd.Series,
                    today: pd.Timestamp) -> bool:
        """策略自定义退出(默认 False,由引擎管止损止盈到期)。"""
        return False
