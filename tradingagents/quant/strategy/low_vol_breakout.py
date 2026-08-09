"""方向 KKK:低位长期横盘 + 放量突破。

60日波动率低 + 60日内涨幅小 + T 日突破20日新高 + 放量
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.strategy.base import BaseStrategy


class LowVolBreakoutStrategy(BaseStrategy):
    """低波动横盘 + 突破 20 日新高。"""
    name = "low_vol_breakout"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 vol_window: int = 60,
                 vol_max: float = 0.05,
                 ret_60d_max: float = 0.20,
                 today_ret_min: float = 0.03,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_above_ma: str = "ma20"):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.vol_window = vol_window
        self.vol_max = vol_max
        self.ret_60d_max = ret_60d_max
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
        print("  [低波动突破] 预计算特征矩阵...", flush=True)
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

        N = self.vol_window
        big["close_std_N"] = big.groupby("stock_code")["close"].transform(
            lambda s: s.rolling(N, min_periods=N // 2).std()
        )
        big["close_mean_N"] = big.groupby("stock_code")["close"].transform(
            lambda s: s.rolling(N, min_periods=N // 2).mean()
        )
        big["volatility_N"] = (big["close_std_N"] / big["close_mean_N"].replace(0, np.nan)).fillna(1.0)

        big["ret_60d_calc"] = big.groupby("stock_code")["close"].pct_change(N)

        big["high_20"] = big.groupby("stock_code")["high"].transform(
            lambda s: s.rolling(20, min_periods=10).max().shift(1)
        )
        big["breaks_20high"] = (big["close"] > big["high_20"]).astype(float)

        self._feature_cache = big
        print(f"  [低波动突破] 特征矩阵: {big.shape}", flush=True)
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
                                  reason=f"lvb={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "open", "high", "low", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "volatility_N", "ret_60d_calc", "breaks_20high"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=[c for c in required if c != "is_bullish"])
        if len(df) == 0:
            return df

        df = df[df["volatility_N"] <= self.vol_max]
        df = df[df["ret_60d_calc"] <= self.ret_60d_max]
        df = df[df["breaks_20high"] == 1]
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
        low_vol = cross_sectional_zscore(-col("volatility_N", 0.05))
        breakout = cross_sectional_zscore(col("today_ret"))
        volume = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        body = cross_sectional_zscore(col("body_ratio", 0.5))
        scores = low_vol * 0.20 + breakout * 0.35 + volume * 0.30 + body * 0.15
        return pd.Series(scores.values, index=feats["stock_code"].values)
