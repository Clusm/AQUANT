from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import (build_features_vectorized, cross_sectional_zscore,
                              get_weekly_bars, get_monthly_bars,
                              merge_asof_weekly, merge_asof_monthly)
from tradingagents.quant.strategy.base import BaseStrategy


class MonthlyCmfBreakoutStrategy(BaseStrategy):
    name = "monthly_cmf_breakout"

    def __init__(self, lookback: int = 260, universe_topk: int = 500,
                 cmf_period: int = 21,
                 cross_recent_months: int = 2,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.cmf_period = cmf_period
        self.cross_recent_months = cross_recent_months
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] | None = None

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
        import time as _time
        _t0 = _time.time()
        print("  [月线 CMF 资金流突破] 预计算特征矩阵...", flush=True)
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 120].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=30)

        _t2 = _time.time()
        print(f"    build_features_vectorized: {_t2-_t1:.1f}s ({len(big)} 行)", flush=True)
        if len(big) == 0:
            self._feature_cache = pd.DataFrame()
            return self._feature_cache
        big = big.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        big["prev_close_raw"] = big.groupby("stock_code")["close"].shift(1)
        big["today_ret"] = (big["close"] - big["prev_close_raw"]) / big["prev_close_raw"].replace(0, np.nan)

        body = big["close"] - big["open"]
        hl = (big["high"] - big["low"]).replace(0, np.nan)
        big["body_ratio"] = (body / hl).fillna(0)
        big["is_bullish"] = (big["close"] > big["open"]).astype(float)

        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        # 月线 bars(从共享缓存获取,无前视合并,再追加策略专属的 CMF 指标)
        monthly = get_monthly_bars(sorted_df)
        # CMF 资金流指标(策略专属,基于 cache 提供的 month_close/high/low/volume)
        mfm_hl = (monthly["month_high"] - monthly["month_low"]).replace(0, np.nan)
        monthly["mfm"] = ((monthly["month_close"] - monthly["month_low"]) - (monthly["month_high"] - monthly["month_close"])) / mfm_hl
        monthly["mfv"] = monthly["mfm"] * monthly["month_volume"]
        monthly["cmf"] = (monthly.groupby("stock_code")["mfv"].transform(
            lambda s: s.rolling(self.cmf_period, min_periods=self.cmf_period).sum()) /
                         monthly.groupby("stock_code")["month_volume"].transform(
            lambda s: s.rolling(self.cmf_period, min_periods=self.cmf_period).sum()))

        monthly["cmf_prev"] = monthly.groupby("stock_code")["cmf"].shift(1)
        monthly["cmf_cross"] = ((monthly["cmf_prev"] <= 0) & (monthly["cmf"] > 0)).astype(float)
        monthly["cmf_cross_recent"] = monthly.groupby("stock_code")["cmf_cross"].transform(
            lambda s: s.rolling(self.cross_recent_months, min_periods=1).max())

        # mma3_above_mma6 / month_above_mma3(策略字段名,基于 cache 的 mma3/mma6/month_close)
        monthly["mma3_above_mma6"] = (monthly["mma3"] > monthly["mma6"]).astype(float)
        monthly["month_above_mma3"] = (monthly["month_close"] > monthly["mma3"]).astype(float)

        big = merge_asof_monthly(
            big,
            monthly[["stock_code", "month_key", "month_date",
                     "cmf", "cmf_cross_recent",
                     "mma3_above_mma6", "month_above_mma3"]]
        )

        # 周线 bars(从共享缓存获取,无前视合并,weekly_above_ma5 -> weekly_above_wma5 策略字段名)
        weekly = get_weekly_bars(sorted_df)
        weekly = weekly.rename(columns={"weekly_above_ma5": "weekly_above_wma5"})
        big = merge_asof_weekly(
            big,
            weekly[["stock_code", "week_key", "week_date", "weekly_above_wma5"]]
        )

        big["close_above_ma20"] = (big["close"] > big["ma20"]).astype(float)
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        _t3 = _time.time()
        print(f"  [月线 CMF 资金流突破] 特征矩阵: {big.shape}", flush=True)
        print(f"  [月线 CMF 资金流突破] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s 聚合={_t3-_t2:.1f}s", flush=True)

        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        print("  [月线 CMF 资金流突破] 预计算信号矩阵...", flush=True)

        required = ["close", "ma20",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "cmf", "cmf_cross_recent",
                    "mma3_above_mma6", "month_above_mma3",
                    "weekly_above_wma5", "close_above_ma20",
                    "ret_60d"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        mask = (
            big[required].notna().all(axis=1) &
            (big["cmf"] > 0) &
            big["cmf_cross_recent"].eq(1) &
            big["mma3_above_mma6"].eq(1) &
            big["month_above_mma3"].eq(1) &
            big["weekly_above_wma5"].eq(1) &
            big["close_above_ma20"].eq(1) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min)
        )
        if self.require_bullish:
            mask &= big["is_bullish"].eq(1)

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        score_cols = ["stock_code", "cmf", "ret_60d", "volume_ratio_5", "today_ret"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        print(f"  [月线 CMF 资金流突破] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        if self._eligible_by_date is None:
            self._precompute_features(daily_df)

        eligible = self._eligible_by_date.get(current_date)
        if eligible is None or len(eligible) == 0:
            return []

        universe = self._get_universe(daily_df, current_date)
        if not universe or len(universe) < 5:
            return []
        universe_set = set(universe)

        eligible = eligible[eligible["stock_code"].isin(universe_set)]
        if len(eligible) == 0:
            return []

        scores = self._score_from_eligible(eligible)
        if len(scores) == 0:
            return []

        scores = scores.sort_values(ascending=False)

        signals: list[Signal] = []
        for code, score in scores.head(top_k * 2).items():
            if code in portfolio.positions:
                continue
            signals.append(Signal(code=code, score=float(score),
                                  reason=f"m_cmf_bo={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["cmf", "ret_60d", "volume_ratio_5", "today_ret"]
        weights = [0.30, 0.25, 0.25, 0.20]
        signs = [1.0, 1.0, 1.0, 1.0]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight, sign in zip(factors, weights, signs):
            if factor not in eligible.columns:
                continue
            s = eligible[factor].to_numpy(dtype=np.float64) * sign
            mask = ~np.isnan(s)
            if mask.sum() < 2:
                continue
            mean = s[mask].mean()
            std = s[mask].std()
            if std == 0 or np.isnan(std):
                continue
            z = np.where(mask, (s - mean) / std, 0.0)
            z = np.clip(z, -3.0, 3.0)
            scores += z * weight

        return pd.Series(scores, index=eligible["stock_code"].to_numpy())

