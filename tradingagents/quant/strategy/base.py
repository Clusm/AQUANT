"""策略抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk


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

    def _init_universe_caches(self):
        """初始化 universe 相关缓存(在子类 __init__ 中调用)。"""
        self._universe_cache: dict[str, list[str]] = {}
        self._universe_set_cache: dict[str, set[str]] = {}
        self._universe_feats_cache: dict[str, dict] = {}

    def _resolve_universe(self, daily_df: pd.DataFrame, current_date: pd.Timestamp) -> set[str]:
        """获取当月 universe set(月度缓存)。"""
        month_key = f"{current_date.year}-{current_date.month:02d}"
        if month_key in self._universe_set_cache:
            return self._universe_set_cache[month_key]
        if month_key not in self._universe_cache:
            codes = filter_universe_topk(daily_df, on_date=current_date, topk=self.universe_topk)
            self._universe_cache[month_key] = codes
        s = set(self._universe_cache[month_key])
        self._universe_set_cache[month_key] = s
        return s

    def _get_universe_feats_today(self, daily_df: pd.DataFrame,
                                  current_date: pd.Timestamp) -> pd.DataFrame:
        """获取当日 universe 过滤后的特征切片。

        缓存策略:
        - universe set 按月缓存(每月 filter_universe_topk 一次)
        - 当日特征切片通过 O(1) dict 取出后,用 isin 按需过滤
        - 不预构建月度过滤缓存(实测 13 月 × 4 策略 = 52 次构建反而更慢)
        """
        universe_set = self._resolve_universe(daily_df, current_date)
        if not universe_set:
            return pd.DataFrame()

        feats_all = self._precompute_features(daily_df)
        if len(feats_all) == 0 or self._feats_by_date is None:
            return pd.DataFrame()

        feats_today = self._feats_by_date.get(current_date)
        if feats_today is None or len(feats_today) < 5:
            return pd.DataFrame()
        return feats_today[feats_today["stock_code"].isin(universe_set)]
