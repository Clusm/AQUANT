"""中线策略 V6-4:长线突破(60d 横盘 + 突破 + 放量)。

核心思路(长横盘 + 突破 + 强势确认):
- 60d 振幅 <= 20%(长横盘,波动小)
- 60d 涨幅 -5% ~ +10%(无方向,横盘)
- T 日突破 60d 高点
- T 日放量(today_vol >= 2.0,强放量)
- T 日阳线(body_ratio >= 0.5)
- 多头排列 MA5 > MA10 > MA20
- close > MA60

与 V5 boll_contraction_breakout 区别:
- boll_cb:布林带宽度 60d 低位(波动率维度)
- 本策略:60d 振幅 <= 20%(价格区间维度)
- 横盘标准更直观,且加上 60d 涨幅中性过滤

与 breakout_20high 区别:
- 20high:20d 新高 + 短期强势(短线)
- 本策略:60d 突破 + 长横盘(中线)
- 横盘时间更长,突破后趋势更可持续

中线逻辑:长横盘后突破是经典中线买点,持仓 15-20d 让突破后的趋势展开。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.strategy.base import BaseStrategy


class LongConsolidationBreakoutStrategy(BaseStrategy):
    """长线突破策略。"""
    name = "long_consolidation_breakout"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 consolidation_window: int = 60,
                 range_max: float = 0.20,
                 ret_60d_min: float = -0.05,
                 ret_60d_max: float = 0.10,
                 require_full_align: bool = True,
                 today_vol_min: float = 2.0,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 today_ret_min: float = 0.02,
                 today_ret_max: float = 0.07,
                 require_above_ma: str = "ma60"):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.consolidation_window = consolidation_window
        self.range_max = range_max
        self.ret_60d_min = ret_60d_min
        self.ret_60d_max = ret_60d_max
        self.require_full_align = require_full_align
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.today_ret_min = today_ret_min
        self.today_ret_max = today_ret_max
        self.require_above_ma = require_above_ma
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None

    def _get_universe(self, daily_df: pd.DataFrame, current_date: pd.Timestamp) -> list[str]:
        date_key = pd.Timestamp(current_date).strftime("%Y-%m-%d")
        if date_key in self._universe_cache:
            return self._universe_cache[date_key]
        codes = filter_universe_topk(daily_df, on_date=current_date, topk=None)
        self._universe_cache[date_key] = codes
        return codes

    def _precompute_features(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        if self._feature_cache is not None:
            return self._feature_cache
        print("  [长线突破] 预计算特征矩阵...", flush=True)
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        big = build_features_vectorized(sorted_df, min_rows=30)
        if len(big) == 0:
            self._feature_cache = pd.DataFrame()
            return self._feature_cache
        big["prev_close_raw"] = big.groupby("stock_code")["close"].shift(1)
        big["today_ret"] = (big["close"] - big["prev_close_raw"]) / big["prev_close_raw"].replace(0, np.nan)

        body = big["close"] - big["open"]
        hl = (big["high"] - big["low"]).replace(0, np.nan)
        big["body_ratio"] = (body / hl).fillna(0)
        big["is_bullish"] = (big["close"] > big["open"]).astype(float)

        big["full_align"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"]) &
            (big["ma20"] > big["ma60"])
        ).astype(float)
        big["align_3ma"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"])
        ).astype(float)

        # 60d 最高/最低(用 shift(1) 不含今天)
        big["high_60"] = big.groupby("stock_code")["high"].transform(
            lambda s: s.rolling(self.consolidation_window, min_periods=20).max().shift(1)
        )
        big["low_60"] = big.groupby("stock_code")["low"].transform(
            lambda s: s.rolling(self.consolidation_window, min_periods=20).min().shift(1)
        )
        # 60d 振幅 = (high_60 - low_60) / low_60
        big["range_60d"] = (big["high_60"] - big["low_60"]) / big["low_60"].replace(0, np.nan)
        # 横盘条件:振幅 <= 20%
        big["is_consolidating"] = (big["range_60d"] <= self.range_max).astype(float)

        # 60d 涨幅(从 60d 前收盘 -> 今天收盘)
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(self.consolidation_window)
        # 横盘期 60d 涨幅 -5% ~ +10%(无方向)
        big["neutral_trend"] = (
            (big["ret_60d"] >= self.ret_60d_min) &
            (big["ret_60d"] <= self.ret_60d_max)
        ).astype(float)

        # T 日突破 60d 高点
        big["breakout_60d"] = (big["close"] > big["high_60"]).astype(float)

        self._feature_cache = big
        print(f"  [长线突破] 特征矩阵: {big.shape}", flush=True)
        return big

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        universe = self._get_universe(daily_df, current_date)
        if not universe:
            return []

        feats_all = self._precompute_features(daily_df)
        if len(feats_all) == 0:
            return []

        feats = feats_all[(feats_all["trade_date"] == current_date) &
                          (feats_all["stock_code"].isin(universe))].copy()
        if len(feats) < 5:
            return []

        feats = self._filter_eligible(feats)
        if len(feats) == 0:
            return []

        scores = self._score(feats)
        if len(scores) == 0:
            return []

        eligible = scores.sort_values(ascending=False)
        signals: list[Signal] = []
        for code, score in eligible.head(top_k * 2).items():
            if code in portfolio.positions:
                continue
            signals.append(Signal(code=code, score=float(score),
                                  reason=f"lc_bo={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "full_align", "align_3ma",
                    "high_60", "low_60", "range_60d", "is_consolidating",
                    "ret_60d", "neutral_trend", "breakout_60d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "high_60", "low_60", "range_60d",
                               "ret_60d"])
        if len(df) == 0:
            return df

        # 1. 60d 振幅 <= 20%(长横盘)
        df = df[df["is_consolidating"] == 1]
        if len(df) == 0:
            return df

        # 2. 60d 涨幅 -5% ~ +10%(横盘期无方向)
        df = df[df["neutral_trend"] == 1]
        if len(df) == 0:
            return df

        # 3. T 日突破 60d 高点
        df = df[df["breakout_60d"] == 1]
        if len(df) == 0:
            return df

        # 4. 多头排列
        if self.require_full_align:
            df = df[df["full_align"] == 1]
        else:
            df = df[df["align_3ma"] == 1]
        if len(df) == 0:
            return df

        # 5. T 日涨幅区间(突破强度)
        df = df[(df["today_ret"] >= self.today_ret_min) &
                (df["today_ret"] <= self.today_ret_max)]
        if len(df) == 0:
            return df

        # 6. 强放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 7. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 8. 阳线
        if self.require_bullish:
            df = df[df["is_bullish"] == 1]
            if len(df) == 0:
                return df

        # 9. close > MA
        if self.require_above_ma == "ma20":
            df = df[df["close"] > df["ma20"]]
        elif self.require_above_ma == "ma60":
            df = df[df["close"] > df["ma60"]]

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        # 突破强度(T 日涨幅)
        strength = cross_sectional_zscore(col("today_ret", 0.03))
        # 量能(放量越大越好)
        vol = cross_sectional_zscore(col("volume_ratio_5", 2.0))
        # 实体
        body = cross_sectional_zscore(col("body_ratio", 0.5))
        # 横盘程度(振幅越小越好,意味着更紧的整理)
        tight = cross_sectional_zscore(-col("range_60d", 0.15))
        # 突破 60d 高点的幅度
        breakout_strength = cross_sectional_zscore(
            (col("close", 10) - col("high_60", 10)) / col("high_60", 10).replace(0, np.nan)
        )

        scores = (
            strength * 0.30 +
            vol * 0.25 +
            body * 0.15 +
            tight * 0.15 +
            breakout_strength * 0.15
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
