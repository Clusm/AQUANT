"""事件触发 + 池内因子 rank 取 top_k(不做 P% 预过滤)。

事件触发得到 eligible 池 -> 池内按因子 composite rank 直接取 top_k。
适合 eligible 数较多(>=20)的场景。无事件日返回 []。

自 stock_selector 迁移: 工程因子列(RANGE_POS3/RANGE_POS_20D 等)缺失时由 fc_factors 按原公式补齐。
"""
from __future__ import annotations

import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.strategy.base import BaseStrategy
from tradingagents.quant.strategy.event_templates import get_event_pool
from tradingagents.quant.strategy.fc_factors import ensure_fc_factor_columns


class FactorRankedEventStrategy(BaseStrategy):
    """事件触发 -> 池内因子 composite rank 取 top_k。"""

    def __init__(self, name: str = "factor_ranked_event",
                 event_type: str = "monthly_macd_golden_cross",
                 factor_weights: dict[str, float] | None = None,
                 event_params: dict | None = None,
                 universe_topk: int = 300,
                 top_k: int = 10,
                 direction: str = "top",
                 min_stocks_for_signal: int = 1,
                 filter_top_pct: float | None = None,
                 selection_mode: str = "ranked"):
        self.name = name
        self.event_type = event_type
        self.factor_weights = factor_weights or {}
        self.event_params = event_params or {}
        self.universe_topk = int(universe_topk)
        self.top_k = int(top_k)
        self.direction = direction
        self.min_stocks_for_signal = int(min_stocks_for_signal)
        # 库条目元数据字段: selection_mode="ranked" 时 filter_top_pct 不参与
        # (本类就是 ranked 模式直接取 top_k);保留参数仅为兼容库条目 params 全量传入
        self.filter_top_pct = float(filter_top_pct) if filter_top_pct else None
        self.selection_mode = selection_mode
        self._init_universe_caches()
        self._pool: dict[pd.Timestamp, pd.DataFrame] | None = None
        self._factor_lookup: pd.DataFrame | None = None
        self._fc_cache_key: tuple | None = None
        self._fc_factor_df: pd.DataFrame | None = None

    def _ensure_factor_columns(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """因子权重引用的工程因子列不在 daily_df 时, 用 fc_factors 按原公式补齐(结果缓存)。"""
        missing = [f for f in self.factor_weights if f not in daily_df.columns]
        if not missing:
            return daily_df
        key = (id(daily_df), len(daily_df))
        if self._fc_cache_key == key and self._fc_factor_df is not None:
            return self._fc_factor_df
        enriched = ensure_fc_factor_columns(daily_df, missing)
        self._fc_cache_key = key
        self._fc_factor_df = enriched
        return enriched

    def _ensure_pool(self, daily_df: pd.DataFrame):
        if self._pool is None:
            self._pool = get_event_pool(self.event_type, daily_df, self.event_params)
        # 基建修复: 事件池只含事件自带 score_cols; 因子权重引用的列 (如 RANGE_POS3/20D)
        # 建一次 MultiIndex 查表, 按日合并, 否则排名退化为全零分任意取票
        if self._factor_lookup is None and self._pool:
            sample = next(iter(self._pool.values()))
            missing = [f for f in self.factor_weights if f not in sample.columns]
            cols = ["stock_code", "trade_date"] + [f for f in missing if f in daily_df.columns]
            if len(cols) > 2:
                self._factor_lookup = daily_df[cols].set_index(["stock_code", "trade_date"])
            else:
                self._factor_lookup = pd.DataFrame()

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int | None = None) -> list[Signal]:
        daily_df = self._ensure_factor_columns(daily_df)
        n_sel = self.top_k
        self._ensure_pool(daily_df)
        eligible = self._pool.get(current_date)
        if eligible is None or len(eligible) == 0:
            return []

        universe = self._resolve_universe(daily_df, current_date)
        if not universe:
            return []
        eligible = eligible[eligible["stock_code"].isin(universe)]
        if len(eligible) < self.min_stocks_for_signal:
            return []

        if len(self._factor_lookup) > 0:
            try:
                sub = self._factor_lookup.xs(current_date, level="trade_date").reset_index()
                if len(sub) > 0:
                    eligible = eligible.merge(sub, on="stock_code", how="left")
            except KeyError:
                pass

        score = pd.Series(0.0, index=eligible.index)
        for f, w in self.factor_weights.items():
            if f not in eligible.columns:
                continue
            score = score + eligible[f].fillna(0).rank(pct=True) * w
        score = score.sort_values(ascending=(self.direction == "bottom"))
        selected = score.head(n_sel)

        code_series = eligible["stock_code"]
        codes = [code_series.loc[i] for i in selected.index]
        scores = selected.round(4).tolist()
        return [
            Signal(code=str(c), score=float(s), direction="buy", window="morning",
                   reason=f"{self.name}:{float(s):.4f}")
            for c, s in zip(codes, scores, strict=True)
        ]
