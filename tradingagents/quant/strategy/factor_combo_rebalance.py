"""参数化因子组合再平衡策略: 多因子 rank 等权复合 → 中大盘 universe → 每日 top-K 多头。

由 strategy_loop 生成/固化。持有期由引擎 max_holding_days(=库条目 holding_days) 强制到期轮换;
本策略每日刷新 top-K, 掉出 top-K 但未到期的持仓自然持有到期, 形成 ~holding_days 天的滚动轮换。
方向恒为 top(只做多); 卖出交给引擎(ATR 止损/止盈 + 到期)。

自 stock_selector 迁移: 工程因子列(GAP_AVOLC60_PVC10 等)缺失时由 fc_factors 按原公式补齐。
"""
from __future__ import annotations

import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.strategy.base import BaseStrategy
from tradingagents.quant.strategy.fc_factors import ensure_fc_factor_columns


class FactorComboRebalanceStrategy(BaseStrategy):
    """因子组合 + 中大盘 universe + 每日 top-K 多头再平衡。

    factor_weights: {因子列名: 权重}。各因子先做当日截面 pct-rank, 再按权重加权求和为
    composite; 只从 universe_topk(按 20d 均 amount 排序的中大盘池)里取 composite 最高 top_k 只。
    rebalance_every: 目标持有天数(引擎 max_holding 强制到期, 语义上就是再平衡周期)。
    """

    def __init__(self, name: str = "factor_combo",
                 factor_weights: dict[str, float] | None = None,
                 rebalance_every: int = 5,
                 universe_topk: int = 500,
                 top_k: int = 30,
                 direction: str = "top",
                 min_stocks_for_signal: int = 50,
                 hold_priority_tie_break: bool = False,
                 rank_lookback: int = 0,
                 rank_smoothing: str = "mean",
                 score_method: str = "pct_rank",
                 combo_mode: str = "linear",
                 gate_col: str | None = None,
                 rank_col: str | None = None,
                 gate_quantile: float = 0.5):
        self.name = name
        self.factor_weights = factor_weights or {}
        self.rebalance_every = int(rebalance_every)
        self.universe_topk = int(universe_topk)
        self.top_k = int(top_k)
        self.direction = direction
        self.min_stocks_for_signal = int(min_stocks_for_signal)
        self.hold_priority_tie_break = hold_priority_tie_break
        self.rank_lookback = int(rank_lookback)
        self.rank_smoothing = rank_smoothing
        self.score_method = score_method
        self.combo_mode = combo_mode
        self.gate_col = gate_col
        self.rank_col = rank_col
        self.gate_quantile = float(gate_quantile)
        self._smoothed_cache: pd.DataFrame | None = None
        self._fc_cache_key: tuple | None = None
        self._fc_factor_df: pd.DataFrame | None = None
        self._init_universe_caches()

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

    def _factor_cols(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """只取需要的列, 缩小每日过滤成本。"""
        cols = ["trade_date", "stock_code"] + list(self.factor_weights)
        return daily_df[cols].copy()

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int | None = None) -> list[Signal]:
        daily_df = self._ensure_factor_columns(daily_df)
        n_sel = self.top_k  # 用配置的 top_k(引擎传入的 top_k 参数无效)
        universe = self._resolve_universe(daily_df, current_date)
        if not universe:
            return []

        factor_df = self._factor_cols(daily_df)
        today = factor_df[factor_df["trade_date"] == current_date]
        if len(today) < self.min_stocks_for_signal:
            return []
        today = today[today["stock_code"].isin(universe)]
        if len(today) < self.min_stocks_for_signal:
            return []

        # P-F-025: 排名平滑 — 因子列先做 N 日滚动均值 (按股票), 再做截面 rank
        if self.rank_lookback > 0:
            if self._smoothed_cache is None:
                sm = factor_df.copy()
                for f in self.factor_weights:
                    if f in sm.columns:
                        sm[f] = sm.groupby("stock_code")[f].transform(
                            lambda s: s.rolling(self.rank_lookback, min_periods=1).mean())
                self._smoothed_cache = sm
            today = self._smoothed_cache[self._smoothed_cache["trade_date"] == current_date]
            today = today[today["stock_code"].isin(universe)]

        score = pd.Series(0.0, index=today.index)
        # P-F-029: clipped z-score 替代 pct-rank (保留幅度去极值)
        if self.score_method == "clipped_zscore":
            for f, w in self.factor_weights.items():
                if f not in today.columns:
                    continue
                s = today[f].astype(float)
                mu, sd = s.mean(), s.std()
                if sd == 0 or pd.isna(sd):
                    z = pd.Series(0.0, index=today.index)
                else:
                    z = ((s - mu) / sd).clip(-3.0, 3.0)
                score = score + z * w
        else:
            for f, w in self.factor_weights.items():
                if f not in today.columns:
                    continue
                score = score + today[f].fillna(0).rank(pct=True) * w
        today = today.assign(_score=score.values)

        # P-F-026: AND 门 — gate_col 先取 top 50%, 再按 rank_col 排序取 top_k
        if self.combo_mode == "and_gate":
            gate_col = self.gate_col
            rank_col = self.rank_col
            gate_q = self.gate_quantile
            if gate_col in today.columns and rank_col in today.columns:
                thr = today[gate_col].quantile(1.0 - gate_q)
                gated = today[today[gate_col] >= thr]
                if len(gated) >= self.min_stocks_for_signal:
                    today = gated
                    today = today.assign(_score=today[rank_col].fillna(0).rank(pct=True).values)

        today = today.sort_values("_score", ascending=(self.direction == "bottom"))
        selected = today.head(n_sel)

        # P-C-007: 持仓优先 tie-break — 分数差 <0.002 时优先保留已持有标的
        if self.hold_priority_tie_break and portfolio is not None:
            held_codes = set(portfolio.positions.keys())
            min_score = float(selected["_score"].min()) if len(selected) else 0.0
            rest = today[~today.index.isin(selected.index)]
            rest_held = rest[rest["stock_code"].isin(held_codes) &
                             (rest["_score"] >= min_score - 0.002)]
            if len(rest_held) > 0:
                rest_held = rest_held.sort_values("_score", ascending=False)
                sel_nonheld = selected[~selected["stock_code"].isin(held_codes)].sort_values("_score")
                for _, rrow in rest_held.iterrows():
                    if len(sel_nonheld) == 0:
                        break
                    drop_idx = sel_nonheld.index[-1]
                    selected = selected.drop(index=drop_idx)
                    selected = pd.concat([selected, today.loc[[rrow.name]]])
                    sel_nonheld = sel_nonheld.drop(index=drop_idx)

        # 不用 itertuples: 下划线开头的列名会被 itertuples 改名为 _0 等
        codes = selected["stock_code"].tolist()
        scores = selected["_score"].tolist()
        return [
            Signal(code=c, score=float(s), direction="buy", window="morning",
                   reason=f"{self.name}:{float(s):.4f}")
            for c, s in zip(codes, scores, strict=True)
        ]
