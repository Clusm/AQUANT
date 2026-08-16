"""中线策略 V27-2:周线 RSI 突破中线(周线 RSI 上穿 50 + 周/月线多头 + 日线放量)。

V27 新方向(基于 V25 weekly_macd_golden_cross 的核心经验深化):
- V25 w_macd_gc 主回测 comp 7.20 (M_S 级,有史以来最高分)
- 证明"周线技术指标 + 月线趋势过滤"是中线最强 alpha 方向
- 本策略:周线 RSI(14) 上穿 50 + 周线多头 + 月线多头 + 日线放量
- 与 V25-2 区别:V25-2 是周线 MACD 金叉,本策略是周线 RSI 突破中线(RSI 捕捉强弱转换)

核心思路(周线 RSI 上穿中线 + 周线多头 + 月线多头 + 日线放量):
- 周线 RSI(14) 上穿 50 发生在最近 2 周内
- 当前 RSI 在 50-70 之间(刚进入强势区,未超买)
- 周线多头:周线 close > 周线 MA5 > 周线 MA10
- 月线多头:月线 close > 月线 MA3
- 日线:close > MA10
- T 日放量阳线 volume_ratio_5 >= 1.5,body_ratio >= 0.5

中线逻辑:周线 RSI 上穿 50 是中线趋势由弱转强的可靠信号,叠加多重多头确认,持仓 10-15d 让趋势延续。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import (
    build_features_vectorized,
    cross_sectional_zscore,
    get_monthly_bars,
    get_weekly_bars,
    merge_asof_monthly,
    merge_asof_weekly,
)
from tradingagents.quant.strategy.base import BaseStrategy


class WeeklyRsiBreakoutStrategy(BaseStrategy):
    """周线 RSI 突破中线策略(周线 RSI 上穿中线 + 周/月线多头 + 日线放量)。"""
    name = "weekly_rsi_breakout"

    def __init__(self, lookback: int = 200, universe_topk: int = 500,
                 rsi_period: int = 14,
                 cross_recent_weeks: int = 2,
                 cross_level: float = 50.0,
                 rsi_min: float = 50.0,
                 rsi_max: float = 70.0,
                 weekly_ma_mid: int = 10,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.rsi_period = rsi_period
        self.cross_recent_weeks = cross_recent_weeks
        self.cross_level = cross_level
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
        self.weekly_ma_mid = weekly_ma_mid
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
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
        print("  [周线 RSI 突破] 预计算特征矩阵...", flush=True)
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

        # 周线指标(从共享缓存获取,无前视合并,再追加策略专属的 wrsi_cross_recent + 覆盖 weekly_bullish)
        weekly = get_weekly_bars(sorted_df)
        # wrsi_14 -> wrsi(策略字段名)
        weekly["wrsi"] = weekly["wrsi_14"]
        # 周线 RSI 上穿 cross_level
        weekly["wrsi_prev"] = weekly.groupby("stock_code")["wrsi"].shift(1)
        weekly["wrsi_cross"] = (
            (weekly["wrsi_prev"] <= self.cross_level) &
            (weekly["wrsi"] > self.cross_level)
        ).astype(float)
        # 最近 N 周内是否发生过上穿
        weekly["wrsi_cross_recent"] = weekly.groupby("stock_code")["wrsi_cross"].transform(
            lambda s: s.rolling(self.cross_recent_weeks, min_periods=1).max()
        )
        # 策略自定义 weekly_bullish:close > wma5 > wma10(与 cache 默认 wma5>wma10>wma20 不同)
        weekly["weekly_bullish"] = (
            (weekly["week_close"] > weekly["wma5"]) &
            (weekly["wma5"] > weekly["wma10"])
        ).astype(float)
        big = merge_asof_weekly(
            big,
            weekly[["stock_code", "week_key", "week_date",
                    "wrsi_cross_recent", "weekly_bullish", "wrsi"]]
        )

        # 月线指标(从共享缓存获取,无前视合并)
        monthly = get_monthly_bars(sorted_df)
        big = merge_asof_monthly(
            big,
            monthly[["stock_code", "month_key", "month_date",
                     "monthly_above_ma3", "mma3"]]
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        print(f"  [周线 RSI 突破] 特征矩阵: {big.shape}", flush=True)
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
                                  reason=f"w_rsi_bo={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "wrsi_cross_recent", "weekly_bullish",
                    "wrsi",
                    "monthly_above_ma3",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "wrsi_cross_recent", "weekly_bullish",
                               "wrsi",
                               "monthly_above_ma3",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 周线 RSI 上穿(最近 N 周内)
        df = df[df["wrsi_cross_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 当前 RSI 在 [rsi_min, rsi_max] 之间(刚进入强势区,未超买)
        df = df[(df["wrsi"] >= self.rsi_min) & (df["wrsi"] <= self.rsi_max)]
        if len(df) == 0:
            return df

        # 3. 周线多头:close > MA5 > MA10
        df = df[df["weekly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 4. 月线多头:close > MA3
        df = df[df["monthly_above_ma3"] == 1]
        if len(df) == 0:
            return df

        # 5. close > MA10
        df = df[df["close"] > df["ma10"]]
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

        # 周线 RSI(主因子 1:RSI 越接近 rsi_max 越强,但未超买)
        wrsi = cross_sectional_zscore(col("wrsi", 50.0))
        # 60d 涨幅(主因子 2:中期趋势强度)
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            wrsi * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
