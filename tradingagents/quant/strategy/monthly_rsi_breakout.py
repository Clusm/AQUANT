"""中线策略 V28-2:月线 RSI 突破中线(月线 RSI 上穿中性区 + 多重多头确认)。

V28 新方向(基于 V25 w_macd_gc + V21 m_hi_bo 的月线级别经验深化):
- 与 V21-1 monthly_high_breakout(月线新高突破)不同
- 与 V25-2 weekly_macd_golden_cross(周线 MACD 金叉)不同
- 本策略:月线 RSI(14) 上穿 50(从中性区进入强势区)+ 月线多头 + 周线多头 + 日线放量
- 与 V21-1 区别:V21-1 是月线新高,本策略是月线 RSI 突破(动量指标层面确认)
- 与 V25-2 区别:V25-2 是周线 MACD 金叉,本策略是月线 RSI 突破(更纯粹的动量确认)

核心思路(月线 RSI 突破 + 月线多头 + 周线多头 + 日线放量):
- 月线 RSI(14) 上穿 50(从中性区进入强势区)发生在最近 2 个月内
- 月线 RSI 当前值在 50-70 之间(强势但不过热)
- 月线多头:月线 close > 月线 MA3 > 月线 MA6
- 周线多头:周线 close > 周线 MA5
- 日线:close > MA20
- T 日放量阳线 volume_ratio_5 >= 1.5,body_ratio >= 0.5

中线逻辑:月线 RSI 上穿 50 是月线级别动量由弱转强的关键信号,叠加多重多头确认,持仓 10-15d 让趋势延续。
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


class MonthlyRsiBreakoutStrategy(BaseStrategy):
    """月线 RSI 突破中线策略(月线 RSI 上穿 50 + 月/周线多头 + 日线放量)。"""
    name = "monthly_rsi_breakout"

    def __init__(self, lookback: int = 260, universe_topk: int = 500,
                 rsi_window: int = 14,
                 cross_recent_months: int = 2,
                 rsi_min: float = 50.0,
                 rsi_max: float = 70.0,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 near_high_ratio: float = 0.95):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.rsi_window = rsi_window
        self.cross_recent_months = cross_recent_months
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
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
        print("  [月线 RSI 突破] 预计算特征矩阵...", flush=True)
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

        # 月线指标(从共享缓存获取,再追加策略专属的 mrsi_cross_recent,无前视合并)
        monthly = get_monthly_bars(sorted_df)
        # mrsi_14 -> mrsi(策略字段名)
        monthly["mrsi"] = monthly["mrsi_14"]
        # 月线 RSI 上穿 rsi_min(前一月 RSI <= rsi_min,本月 RSI > rsi_min)
        monthly["mrsi_prev"] = monthly.groupby("stock_code")["mrsi"].shift(1)
        monthly["mrsi_cross_up"] = (
            (monthly["mrsi_prev"] <= self.rsi_min) &
            (monthly["mrsi"] > self.rsi_min)
        ).astype(float)
        # 最近 N 月内是否发生过 RSI 上穿
        monthly["mrsi_cross_recent"] = monthly.groupby("stock_code")["mrsi_cross_up"].transform(
            lambda s: s.rolling(self.cross_recent_months, min_periods=1).max()
        )
        big = merge_asof_monthly(
            big,
            monthly[["stock_code", "month_key", "month_date",
                     "mrsi", "mrsi_cross_recent", "monthly_bullish",
                     "mma3", "mma6"]]
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        print(f"  [月线 RSI 突破] 特征矩阵: {big.shape}", flush=True)
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
                                  reason=f"m_rsi_bo={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "mrsi", "mrsi_cross_recent", "monthly_bullish",
                    "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "mrsi", "mrsi_cross_recent", "monthly_bullish",
                               "weekly_above_ma5",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 月线 RSI 上穿 rsi_min(最近 N 月内)
        df = df[df["mrsi_cross_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 月线 RSI 当前值在 rsi_min-rsi_max 之间(强势但不过热)
        df = df[(df["mrsi"] >= self.rsi_min) & (df["mrsi"] <= self.rsi_max)]
        if len(df) == 0:
            return df

        # 3. 月线多头:close > MA3 > MA6
        df = df[df["monthly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 4. 周线多头:close > MA5
        df = df[df["weekly_above_ma5"] == 1]
        if len(df) == 0:
            return df

        # 5. close > MA20
        df = df[df["close"] > df["ma20"]]
        if len(df) == 0:
            return df

        # 6. 放量
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

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        # 月线 RSI(主因子 1:RSI 越接近 rsi_max 越强,但不过热)
        mrsi = cross_sectional_zscore(col("mrsi", 55.0))
        # 60d 涨幅(主因子 2:中期趋势强度)
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            mrsi * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
