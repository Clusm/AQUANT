"""龙头股回调到 MA 反弹。

龙头股(wave1 >= 50%)+ 首次回调到 MA20/MA10 ±2% + T 日反弹确认
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.strategy.base import BaseStrategy


class LeaderPullbackBounceStrategy(BaseStrategy):
    """龙头股回调到 MA 反弹。

    龙头股(wave1 >= 50%)+ 首次回调到 MA20/MA10 ±2% + T 日反弹确认
    """
    name = "leader_pullback_bounce"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 wave1_min: float = 0.50,
                 wave1_lookback: int = 30,
                 ma_target: str = "ma20",
                 ma_band: float = 0.02,
                 today_ret_min: float = 0.02,
                 today_vol_min: float = 1.3,
                 body_ratio_min: float = 0.4,
                 require_above_ma: str = "ma20"):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.wave1_min = wave1_min
        self.wave1_lookback = wave1_lookback
        self.ma_target = ma_target
        self.ma_band = ma_band
        self.today_ret_min = today_ret_min
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
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
        print("  [龙头回调反弹] 预计算特征矩阵...", flush=True)
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

        N = self.wave1_lookback
        big["close_max_N"] = big.groupby("stock_code")["close"].transform(
            lambda s: s.rolling(N, min_periods=N // 2).max()
        )
        big["close_min_N"] = big.groupby("stock_code")["close"].transform(
            lambda s: s.rolling(N, min_periods=N // 2).min()
        )
        big["wave1_gain"] = (big["close_max_N"] / big["close_min_N"].replace(0, np.nan)) - 1.0

        big["prev_close"] = big.groupby("stock_code")["close"].shift(1)
        big[f"prev_{self.ma_target}"] = big.groupby("stock_code")[self.ma_target].shift(1)
        big["prev_close_to_ma"] = (
            big["prev_close"] - big[f"prev_{self.ma_target}"]
        ) / big[f"prev_{self.ma_target}"].replace(0, np.nan)
        big["close_above_ma"] = (big["close"] > big[self.ma_target]).astype(float)

        self._feature_cache = big
        print(f"  [龙头回调反弹] 特征矩阵: {big.shape}", flush=True)
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
                                  reason=f"lpb={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "open", "high", "low", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "wave1_gain", "prev_close_to_ma", "close_above_ma"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=[c for c in required if c not in ("is_bullish", "close_above_ma")])
        if len(df) == 0:
            return df

        df = df[df["wave1_gain"] >= self.wave1_min]
        df = df[(df["prev_close_to_ma"] >= -self.ma_band) &
                (df["prev_close_to_ma"] <= self.ma_band)]
        df = df[df["close_above_ma"] == 1]
        df = df[df["today_ret"] >= self.today_ret_min]
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        df = df[df["body_ratio"] >= self.body_ratio_min]
        df = df[df["is_bullish"] == 1]

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

        wave1 = cross_sectional_zscore(col("wave1_gain", 0.5))
        bounce = cross_sectional_zscore(col("today_ret"))
        volume = cross_sectional_zscore(col("volume_ratio_5", 1.3))
        body = cross_sectional_zscore(col("body_ratio", 0.4))
        if "close_to_ma20" in feats.columns:
            trend = cross_sectional_zscore(col("close_to_ma20", 0.0))
        else:
            trend = pd.Series(0.0, index=feats.index)

        scores = (
            wave1 * 0.25 +
            bounce * 0.30 +
            volume * 0.20 +
            body * 0.10 +
            trend * 0.15
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
