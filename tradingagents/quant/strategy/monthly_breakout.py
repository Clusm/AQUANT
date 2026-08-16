"""中线策略 V19-2:长期月线突破(close > 240d 最高 + 多头 + 量能)。

V19 新方向(基于 V13 ma120_breakout 长期突破的成功经验):
- V13 ma120_bo 系列全期验证通过(虽然主回测 comp 4.00)
- 本策略:更长期的突破 - 240d(约 12 个月)新高
- 与 V13 区别:V13 是 MA120 突破,本策略是 240d 最高价突破

核心思路(长期突破 + 多头 + 量能):
- T 日 close > 240d 最高(12 个月新高)
- 完整多头排列 MA5 > MA10 > MA20 > MA60
- close > MA20
- T 日放量阳线
- T 日涨幅 1-7%
- 20d 涨幅 0-30%
- 60d 涨幅 >= 0
- 120d 涨幅 >= 0
- 接近 240d 高点(本身就是新高)

中线逻辑:12 个月新高是长期趋势确立的信号,持仓 15-20d 让趋势延续。

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


class MonthlyBreakoutStrategy(BaseStrategy):
    """长期月线突破策略。"""
    name = "monthly_breakout"

    def __init__(self, lookback: int = 260, universe_topk: int = 500,
                 breakout_window: int = 240,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.4,
                 require_bullish: bool = True,
                 require_full_align: bool = True,
                 today_ret_min: float = 0.01,
                 today_ret_max: float = 0.07,
                 ret_20d_min: float = 0.0,
                 ret_20d_max: float = 0.30,
                 ret_60d_min: float = 0.0,
                 ret_120d_min: float = 0.0,
                 near_high_ratio: float = 0.95,
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 5):
        self.lookback = lookback
        self.universe_topk = universe_topk
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
        self.ret_120d_min = ret_120d_min
        self.near_high_ratio = near_high_ratio
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
        print("  [长期月线突破] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 60].index
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

        # 240d 最高
        big["high_240d"] = big.groupby("stock_code")["high"].transform(
            lambda s: s.rolling(self.breakout_window, min_periods=120).max().shift(1)
        )
        big["breakout_240d"] = (big["close"] > big["high_240d"]).astype(float)
        big["near_high_240d"] = (big["close"] >= big["high_240d"] * self.near_high_ratio).astype(float)

        # 120d 和 60d 涨幅
        big["ret_120d"] = big.groupby("stock_code")["close"].pct_change(120)
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

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
        print(f"  [长期月线突破] 特征矩阵: {big.shape}", flush=True)

        # V2 出场查表
        self._build_exit_lookup(big)
        return big

    def _build_exit_lookup(self, big: pd.DataFrame):
        """构建 _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询。

        只存出场判断需要的字段,避免存全特征矩阵爆内存。
        """
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
        print(f"  [长期月线突破] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  reason=f"m_bo={score:.2f}"))
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
                    "high_240d", "breakout_240d", "near_high_240d",
                    "ret_60d", "ret_120d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "high_240d", "ret_60d", "ret_120d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 突破 240d 最高(或接近)
        df = df[df["near_high_240d"] == 1]
        if len(df) == 0:
            return df

        # 2. 完整多头排列
        if self.require_full_align:
            df = df[df["full_align"] == 1]
        else:
            df = df[df["align_3ma"] == 1]
        if len(df) == 0:
            return df

        # 3. 20d 涨幅
        df = df[(df["ret_20d"] >= self.ret_20d_min) &
                (df["ret_20d"] <= self.ret_20d_max)]
        if len(df) == 0:
            return df

        # 4. 60d 涨幅
        df = df[df["ret_60d"] >= self.ret_60d_min]
        if len(df) == 0:
            return df

        # 5. 120d 涨幅
        df = df[df["ret_120d"] >= self.ret_120d_min]
        if len(df) == 0:
            return df

        # 6. T 日涨幅
        df = df[(df["today_ret"] >= self.today_ret_min) &
                (df["today_ret"] <= self.today_ret_max)]
        if len(df) == 0:
            return df

        # 7. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 8. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 9. 阳线
        if self.require_bullish:
            df = df[df["is_bullish"] == 1]
            if len(df) == 0:
                return df

        # 10. close > MA20
        df = df[df["close"] > df["ma20"]]

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        # 120d 涨幅(主因子:长期涨幅越强越好)
        ret_120d = cross_sectional_zscore(col("ret_120d", 0.10))
        # 60d 涨幅
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            ret_120d * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
