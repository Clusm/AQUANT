"""中线策略 V28-1:月线 MACD 金叉中线(月线级别 MACD 金叉 + 多重多头确认)。

V28 新方向(基于 V25 w_macd_gc 主回测 comp 7.20 / M_S 的成功经验深化):
- 与 V25-2 weekly_macd_golden_cross(周线 MACD 金叉)不同
- 与 V21-3 macd_zero_axis_cross(日线 MACD 零轴上方金叉)不同
- 本策略:月线 MACD 金叉(月线 EMA12 上穿 EMA26 的 Signal)+ 月线多头 + 周线多头 + 日线放量
- 与 V25-2 区别:V25-2 是周线 MACD 金叉,本策略是月线 MACD 金叉(更长周期,更稳定的中线信号)
- 与 V21-3 区别:V21-3 是日线 MACD 金叉,本策略是月线 MACD 金叉(过滤短期噪音)

核心思路(月线 MACD 金叉 + 月线多头 + 周线多头 + 日线放量):
- 月线 MACD 金叉(MACD 上穿 Signal)发生在最近 2 个月内
- 月线多头:月线 close > 月线 MA3 > 月线 MA6
- 周线多头:周线 close > 周线 MA5
- 日线:close > MA20
- T 日放量阳线 volume_ratio_5 >= 1.5,body_ratio >= 0.5

中线逻辑:月线 MACD 金叉是中线趋势启动的最稳定信号,叠加多重多头确认,持仓 10-15d 让趋势延续。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import (build_features_vectorized, cross_sectional_zscore,
                              get_weekly_bars, get_monthly_bars,
                              merge_asof_weekly, merge_asof_monthly)
from tradingagents.quant.strategy.base import BaseStrategy


class MonthlyMacdGoldenCrossStrategy(BaseStrategy):
    """月线 MACD 金叉中线策略(月线 MACD 金叉 + 月/周线多头 + 日线放量)。"""
    name = "monthly_macd_golden_cross"

    def __init__(self, lookback: int = 260, universe_topk: int = 500,
                 monthly_macd_fast: int = 12,
                 monthly_macd_slow: int = 26,
                 monthly_macd_signal: int = 9,
                 golden_cross_recent_months: int = 2,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 near_high_ratio: float = 0.95):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.monthly_macd_fast = monthly_macd_fast
        self.monthly_macd_slow = monthly_macd_slow
        self.monthly_macd_signal = monthly_macd_signal
        self.golden_cross_recent_months = golden_cross_recent_months
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.near_high_ratio = near_high_ratio
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
        print("  [月线 MACD 金叉] 预计算特征矩阵...", flush=True)
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

        # 周线指标(从共享缓存获取,无前视合并)
        weekly = get_weekly_bars(sorted_df)
        big = merge_asof_weekly(
            big,
            weekly[["stock_code", "week_key", "week_date",
                    "weekly_above_ma5", "wma5"]]
        )

        # 月线指标(从共享缓存获取,无前视合并)
        monthly = get_monthly_bars(sorted_df)
        big = merge_asof_monthly(
            big,
            monthly[["stock_code", "month_key", "month_date",
                     "mmacd_gc_recent", "monthly_bullish",
                     "mmacd", "msignal", "mmacd_hist",
                     "mma3", "mma6"]]
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        print(f"  [月线 MACD 金叉] 特征矩阵: {big.shape}", flush=True)
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
                                  reason=f"m_macd_gc={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "mmacd_gc_recent", "monthly_bullish",
                    "mmacd", "msignal", "mmacd_hist",
                    "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "mmacd_gc_recent", "monthly_bullish",
                               "mmacd", "msignal",
                               "weekly_above_ma5",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 月线 MACD 金叉(最近 N 月内)
        df = df[df["mmacd_gc_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 月线多头:close > MA3 > MA6
        df = df[df["monthly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 3. 周线多头:close > MA5
        df = df[df["weekly_above_ma5"] == 1]
        if len(df) == 0:
            return df

        # 4. close > MA20
        df = df[df["close"] > df["ma20"]]
        if len(df) == 0:
            return df

        # 5. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 6. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 7. 阳线
        if self.require_bullish:
            df = df[df["is_bullish"] == 1]
            if len(df) == 0:
                return df

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        # 月线 MACD 柱状图(主因子 1:hist 越大越好,金叉后动能强)
        mmacd_hist = cross_sectional_zscore(col("mmacd_hist", 0.0))
        # 60d 涨幅(主因子 2:中期趋势强度)
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            mmacd_hist * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
