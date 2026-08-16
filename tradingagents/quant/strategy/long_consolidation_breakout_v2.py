"""中线策略 V7-1:90 日长横盘突破(更长横盘 + 突破 + 放量)。

核心思路(超长横盘 + 突破 + 强势确认):
- 90d 振幅 <= 25%(超长横盘,90 天波动小)
- 90d 涨幅 -10% ~ +15%(无方向,长期横盘)
- T 日突破 90d 高点
- T 日放量(today_vol >= 1.8)
- T 日阳线(body_ratio >= 0.5)
- 多头排列 MA5 > MA10 > MA20
- close > MA60

与 V6 lc_bo 区别:
- lc_bo:60d 振幅 <= 20-25%(60 天横盘)
- 本策略:90d 振幅 <= 25%(90 天横盘,时间更长)
- 长横盘后突破能量更大,信号更可靠但更少

中线逻辑:超长横盘后突破是经典中线买点,信号少但可靠性高,持仓 15-20d。

V2 信号出场(2026-07-22 加):月线多头破位 / 周线多头破位,enable_signal_exit 开关。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class LongConsolidationBreakoutV2Strategy(BaseStrategy):
    """90 日长横盘突破策略。"""
    name = "long_consolidation_breakout_v2"

    def __init__(self, lookback: int = 150, universe_topk: int = 500,
                 consolidation_window: int = 90,
                 range_max: float = 0.25,
                 ret_long_min: float = -0.10,
                 ret_long_max: float = 0.15,
                 require_full_align: bool = True,
                 today_vol_min: float = 1.8,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 today_ret_min: float = 0.02,
                 today_ret_max: float = 0.07,
                 require_above_ma: str = "ma60",
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 5):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.consolidation_window = consolidation_window
        self.range_max = range_max
        self.ret_long_min = ret_long_min
        self.ret_long_max = ret_long_max
        self.require_full_align = require_full_align
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.today_ret_min = today_ret_min
        self.today_ret_max = today_ret_max
        self.require_above_ma = require_above_ma
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self._calendar = None  # lazy load in should_exit
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None
        self._exit_lookup: dict[tuple, dict] | None = None

    def _get_universe(self, daily_df: pd.DataFrame, current_date: pd.Timestamp) -> list[str]:
        month_key = f"{current_date.year}-{current_date.month:02d}"
        if month_key in self._universe_cache:
            return self._universe_cache[month_key]
        codes = filter_universe_topk(daily_df, on_date=current_date, topk=self.universe_topk)
        self._universe_cache[month_key] = codes
        return codes

    def _precompute_features(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        if self._feature_cache is not None:
            return self._feature_cache
        print("  [90d 长横盘突破] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 100].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=30, columns=required_feature_columns(self))
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

        big["full_align"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"]) &
            (big["ma20"] > big["ma60"])
        ).astype(float)
        big["align_3ma"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"])
        ).astype(float)

        # 90d 最高/最低
        big["high_long"] = big.groupby("stock_code")["high"].transform(
            lambda s: s.rolling(self.consolidation_window, min_periods=30).max().shift(1)
        )
        big["low_long"] = big.groupby("stock_code")["low"].transform(
            lambda s: s.rolling(self.consolidation_window, min_periods=30).min().shift(1)
        )
        # 90d 振幅
        big["range_long"] = (big["high_long"] - big["low_long"]) / big["low_long"].replace(0, np.nan)
        big["is_consolidating"] = (big["range_long"] <= self.range_max).astype(float)

        # 90d 涨幅
        big["ret_long"] = big.groupby("stock_code")["close"].pct_change(self.consolidation_window)
        big["neutral_trend"] = (
            (big["ret_long"] >= self.ret_long_min) &
            (big["ret_long"] <= self.ret_long_max)
        ).astype(float)

        # T 日突破 90d 高点
        big["breakout_long"] = (big["close"] > big["high_long"]).astype(float)

        # 周线数据(用于 V2 出场):week_close > wma5
        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)
        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(5, min_periods=3).mean())
        weekly["weekly_above_ma5"] = (weekly["week_close"] > weekly["wma5"]).astype(float)
        big = big.merge(
            weekly[["stock_code", "week_key", "weekly_above_ma5"]],
            on=["stock_code", "week_key"], how="left"
        )

        # 月线数据(用于 V2 出场):month_close > mma3
        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        monthly = big.groupby(["stock_code", "month_key"]).agg(
            month_close=("close", "last"),
            month_date=("trade_date", "last")
        ).reset_index()
        monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)
        monthly["mma3"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(3, min_periods=2).mean())
        monthly["monthly_above_ma3"] = (monthly["month_close"] > monthly["mma3"]).astype(float)
        big = big.merge(
            monthly[["stock_code", "month_key", "monthly_above_ma3"]],
            on=["stock_code", "month_key"], how="left"
        )

        self._feature_cache = big
        print(f"  [90d 长横盘突破] 特征矩阵: {big.shape}", flush=True)

        # V2 出场查表
        self._build_exit_lookup(big)
        return big

    def _build_exit_lookup(self, big: pd.DataFrame):
        """构建 _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询。"""
        exit_cols = ["stock_code", "trade_date", "close", "ma20",
                     "monthly_above_ma3", "weekly_above_ma5", "full_align"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma20": row.ma20,
                "monthly_above_ma3": getattr(row, "monthly_above_ma3", 1.0),
                "weekly_above_ma5": getattr(row, "weekly_above_ma5", 1.0),
                "full_align": getattr(row, "full_align", 1.0),
            }
        print(f"  [90d 长横盘突破] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  direction="buy", window="morning",
                                  reason=f"lc_bo_v2={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """V2 信号出场:月线多头破位 / 周线多头破位。

        引擎 A2: today_row 实际是 yesterday 数据,today 参数是 yesterday 日期,
        用 yesterday 特征判断,避免前视。

        Returns
        -------
        bool
            True 表示触发出场信号。引擎在 ATR 未触发时才会调用此方法。
        """
        if self._exit_lookup is None or not self.enable_signal_exit:
            return False
        if self._calendar is None:
            from tradingagents.quant.utils.trading_calendar import get_calendar
            self._calendar = get_calendar()
        holding_days = position.holding_days(today, self._calendar)
        if holding_days < self.exit_min_holding_days:
            return False
        feats = self._exit_lookup.get((position.code, today))
        if feats is None:
            return False

        # 1. 月线多头破位(month_close <= mma3)
        if feats.get("monthly_above_ma3", 1.0) == 0.0:
            return True
        # 2. 周线多头破位(week_close <= wma5)
        if feats.get("weekly_above_ma5", 1.0) == 0.0:
            return True
        return False

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "full_align", "align_3ma",
                    "high_long", "low_long", "range_long", "is_consolidating",
                    "ret_long", "neutral_trend", "breakout_long"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "high_long", "low_long", "range_long", "ret_long"])
        if len(df) == 0:
            return df

        df = df[df["is_consolidating"] == 1]
        if len(df) == 0:
            return df

        df = df[df["neutral_trend"] == 1]
        if len(df) == 0:
            return df

        df = df[df["breakout_long"] == 1]
        if len(df) == 0:
            return df

        if self.require_full_align:
            df = df[df["full_align"] == 1]
        else:
            df = df[df["align_3ma"] == 1]
        if len(df) == 0:
            return df

        df = df[(df["today_ret"] >= self.today_ret_min) &
                (df["today_ret"] <= self.today_ret_max)]
        if len(df) == 0:
            return df

        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        if self.require_bullish:
            df = df[df["is_bullish"] == 1]
            if len(df) == 0:
                return df

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

        strength = cross_sectional_zscore(col("today_ret", 0.03))
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.8))
        body = cross_sectional_zscore(col("body_ratio", 0.5))
        tight = cross_sectional_zscore(-col("range_long", 0.20))
        breakout_strength = cross_sectional_zscore(
            (col("close", 10) - col("high_long", 10)) / col("high_long", 10).replace(0, np.nan)
        )

        scores = (
            strength * 0.30 +
            vol * 0.25 +
            body * 0.15 +
            tight * 0.15 +
            breakout_strength * 0.15
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
