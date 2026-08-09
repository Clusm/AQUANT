"""中线策略 V18-1:月周日三重共振(V16 wd_res 升级版)。

V18 新方向(基于 V16 wd_res 3 个变体全过的成功经验,继续深挖):
- 与 V16-4 weekly_daily_resonance(周线日线共振)不同
- 本策略:月线 + 周线 + 日线三重共振
- 与 V16-4 区别:增加月线级别确认,过滤假突破

核心思路(月线多头 + 周线多头 + 日线突破 + 放量):
- 月线:close > 月线 MA3(3 个月均线),近 3 月涨幅 >= 0
- 周线:MA5 > MA10 > MA20(周线多头排列),近 4 周涨幅 >= 0
- 日线:T 日 close > 20d 最高(日线突破)
- 日线:完整多头排列 MA5 > MA10 > MA20 > MA60
- 日线:close > MA20
- T 日放量阳线
- T 日涨幅 1-7%
- 20d 涨幅 0-30%
- 60d 涨幅 >= 0

中线逻辑:月周日三重共振是强确认信号,持仓 10-15d 让大趋势延续。
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


class MonthlyWeeklyDailyResonanceStrategy(BaseStrategy):
    """月周日三重共振策略。"""
    name = "monthly_weekly_daily_resonance"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 monthly_ret_months: int = 3,
                 weekly_ma_mid: int = 10,
                 weekly_ma_long: int = 20,
                 weekly_ret_weeks: int = 4,
                 breakout_window: int = 20,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.4,
                 require_bullish: bool = True,
                 require_full_align: bool = True,
                 today_ret_min: float = 0.01,
                 today_ret_max: float = 0.07,
                 ret_20d_min: float = 0.0,
                 ret_20d_max: float = 0.30,
                 ret_60d_min: float = 0.0,
                 require_above_ma: str = "ma20",
                 near_high_ratio: float = 0.92):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.monthly_ret_months = monthly_ret_months
        self.weekly_ma_mid = weekly_ma_mid
        self.weekly_ma_long = weekly_ma_long
        self.weekly_ret_weeks = weekly_ret_weeks
        self.breakout_window = breakout_window
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.require_full_align = require_full_align
        self.today_ret_min = today_ret_min
        self.today_ret_max = today_ret_max
        self.ret_20d_min = ret_20d_min
        self.ret_20d_max = ret_20d_max
        self.ret_60d_min = ret_60d_min
        self.require_above_ma = require_above_ma
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
        print("  [月周日三重共振] 预计算特征矩阵...", flush=True)
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

        # 周线指标(从共享缓存获取,无前视合并)
        weekly = get_weekly_bars(sorted_df)
        big = merge_asof_weekly(
            big,
            weekly[["stock_code", "week_key", "week_date",
                    "weekly_bullish", "weekly_up", "weekly_ret_N"]]
        )

        # 月线指标(从共享缓存获取,无前视合并)
        monthly = get_monthly_bars(sorted_df)
        big = merge_asof_monthly(
            big,
            monthly[["stock_code", "month_key", "month_date",
                     "monthly_above_ma3", "monthly_up", "monthly_ret_N"]]
        )

        # 20d 高点
        big["high_N"] = big.groupby("stock_code")["high"].transform(
            lambda s: s.rolling(self.breakout_window, min_periods=10).max().shift(1)
        )
        big["breakout_N"] = (big["close"] > big["high_N"]).astype(float)
        big["near_high_N"] = (big["close"] >= big["high_N"] * self.near_high_ratio).astype(float)

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        print(f"  [月周日三重共振] 特征矩阵: {big.shape}", flush=True)
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
                                  reason=f"mwd_res={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "full_align", "align_3ma",
                    "high_N", "breakout_N", "near_high_N",
                    "ret_60d", "ret_20d",
                    "weekly_bullish", "weekly_up", "weekly_ret_N",
                    "monthly_above_ma3", "monthly_up", "monthly_ret_N"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "high_N", "ret_60d", "ret_20d",
                               "weekly_bullish", "weekly_up", "weekly_ret_N",
                               "monthly_above_ma3", "monthly_up", "monthly_ret_N"])
        if len(df) == 0:
            return df

        # 1. 月线多头
        df = df[df["monthly_above_ma3"] == 1]
        if len(df) == 0:
            return df

        # 2. 月线上行
        df = df[df["monthly_up"] == 1]
        if len(df) == 0:
            return df

        # 3. 周线多头
        df = df[df["weekly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 4. 周线上行
        df = df[df["weekly_up"] == 1]
        if len(df) == 0:
            return df

        # 5. 日线多头排列
        if self.require_full_align:
            df = df[df["full_align"] == 1]
        else:
            df = df[df["align_3ma"] == 1]
        if len(df) == 0:
            return df

        # 6. 日线突破
        df = df[df["breakout_N"] == 1]
        if len(df) == 0:
            return df

        # 7. 接近 20d 高点
        df = df[df["near_high_N"] == 1]
        if len(df) == 0:
            return df

        # 8. 20d 涨幅
        df = df[(df["ret_20d"] >= self.ret_20d_min) &
                (df["ret_20d"] <= self.ret_20d_max)]
        if len(df) == 0:
            return df

        # 9. 60d 涨幅
        df = df[df["ret_60d"] >= self.ret_60d_min]
        if len(df) == 0:
            return df

        # 10. T 日涨幅
        df = df[(df["today_ret"] >= self.today_ret_min) &
                (df["today_ret"] <= self.today_ret_max)]
        if len(df) == 0:
            return df

        # 11. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 12. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 13. 阳线
        if self.require_bullish:
            df = df[df["is_bullish"] == 1]
            if len(df) == 0:
                return df

        # 14. close > MA
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

        # 月线涨幅(主因子)
        monthly_ret = cross_sectional_zscore(col("monthly_ret_N", 0.05))
        # 周线涨幅
        weekly_ret = cross_sectional_zscore(col("weekly_ret_N", 0.05))
        # 60d 涨幅
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))

        scores = (
            monthly_ret * 0.30 +
            weekly_ret * 0.25 +
            ret_60d * 0.20 +
            ret_20d * 0.15 +
            vol * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
