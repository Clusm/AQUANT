"""中线策略 V14-2:连续强势收盘(5d close 在 day range 高位 + 趋势)。

V14 新方向(基于 V4-V13 经验,继续深度挖掘):
- 与 V12-3 continuous_positive_strength(5d 连阳)不同
- 本策略:5d close 在 day range 高位(强势收盘,不必阳线)
- 强势收盘 = 收盘价接近当日最高,主力资金推动
- 与 V12-3 区别:V12-3 是阳线天数,本策略是 close 在 range 中的位置

核心思路(5d 强势收盘 + 趋势 + 量能):
- 近 5d 中至少 4d close_position >= 0.7(close 接近 high)
- 5d 累计涨幅 >= 3%
- 完整多头排列 MA5 > MA10 > MA20 > MA60
- MA5 上行
- close 距 MA5 偏离 <= 5%
- 20d 涨幅 0-30%
- 60d 涨幅 >= 0
- T 日量比 0.8-2.5(温和放量)
- T 日涨幅 0-5%(稳态)

中线逻辑:连续强势收盘是主力持续推动,持仓 10-15d 让行情延续。

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


class ContinuousStrongCloseStrategy(BaseStrategy):
    """连续强势收盘策略。"""
    name = "continuous_strong_close"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 close_position_min: float = 0.7,
                 strong_close_days_min: int = 4,
                 window: int = 5,
                 cumulative_ret_min: float = 0.03,
                 cumulative_ret_max: float = 0.15,
                 ma5_uptrend_days: int = 5,
                 close_to_ma5_max: float = 0.05,
                 today_vol_min: float = 0.8,
                 today_vol_max: float = 2.5,
                 body_ratio_min: float = 0.3,
                 require_full_align: bool = True,
                 today_ret_min: float = 0.0,
                 today_ret_max: float = 0.05,
                 ret_20d_min: float = 0.0,
                 ret_20d_max: float = 0.30,
                 ret_60d_min: float = 0.0,
                 require_above_ma: str = "ma20",
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 5,
                 profit_guard_gain: float | None = None,
                 profit_guard_dd: float = 0.3):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.close_position_min = close_position_min
        self.strong_close_days_min = strong_close_days_min
        self.window = window
        self.cumulative_ret_min = cumulative_ret_min
        self.cumulative_ret_max = cumulative_ret_max
        self.ma5_uptrend_days = ma5_uptrend_days
        self.close_to_ma5_max = close_to_ma5_max
        self.today_vol_min = today_vol_min
        self.today_vol_max = today_vol_max
        self.body_ratio_min = body_ratio_min
        self.require_full_align = require_full_align
        self.today_ret_min = today_ret_min
        self.today_ret_max = today_ret_max
        self.ret_20d_min = ret_20d_min
        self.ret_20d_max = ret_20d_max
        self.ret_60d_min = ret_60d_min
        self.require_above_ma = require_above_ma
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self.profit_guard_gain = profit_guard_gain
        self.profit_guard_dd = profit_guard_dd
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
        print("  [连续强势收盘] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 30].index
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

        # close_position
        big["close_position"] = ((big["close"] - big["low"]) / hl).fillna(0)
        big["is_strong_close"] = (big["close_position"] >= self.close_position_min).astype(float)

        big["full_align"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"]) &
            (big["ma20"] > big["ma60"])
        ).astype(float)
        big["align_3ma"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"])
        ).astype(float)

        # 近 N 日强势收盘天数
        big["strong_close_days_N"] = big.groupby("stock_code")["is_strong_close"].transform(
            lambda s: s.rolling(self.window, min_periods=self.window).sum()
        )

        # 近 N 日累计涨幅
        big["close_N_ago"] = big.groupby("stock_code")["close"].shift(self.window)
        big["cum_ret_N"] = (big["close"] - big["close_N_ago"]) / big["close_N_ago"].replace(0, np.nan)

        # MA5 上行
        big["ma5_N_ago"] = big.groupby("stock_code")["ma5"].shift(self.ma5_uptrend_days)
        big["ma5_up"] = (big["ma5"] > big["ma5_N_ago"]).astype(float)

        # close 距 MA5 偏离
        big["close_to_ma5_dev"] = (big["close"] - big["ma5"]) / big["ma5"].replace(0, np.nan)

        # 60d 涨幅
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
        print(f"  [连续强势收盘] 特征矩阵: {big.shape}", flush=True)

        # V2 出场查表
        self._build_exit_lookup(big)
        return big

    def _build_exit_lookup(self, big: pd.DataFrame):
        """构建 _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询。"""
        exit_cols = ["stock_code", "trade_date", "close", "ma5", "ma20",
                     "monthly_above_ma3", "weekly_above_ma5", "full_align"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma5": getattr(row, "ma5", None),
                "ma20": row.ma20,
                "monthly_above_ma3": getattr(row, "monthly_above_ma3", 1.0),
                "weekly_above_ma5": getattr(row, "weekly_above_ma5", 1.0),
                "full_align": getattr(row, "full_align", 1.0),
            }
        print(f"  [连续强势收盘] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  reason=f"cs_close={score:.2f}"))
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

        # P-C-013: 盈利保护出场 — 浮盈达 profit_guard_gain 后, 峰值回撤 >=profit_guard_dd
        # 或收盘跌破 MA5 提前兑现 (不设亏损侧固定止损)
        if self.profit_guard_gain is not None:
            close = feats["close"]
            entry_price = position.entry_price
            max_close = position.max_close_since_entry
            if entry_price and entry_price > 0:
                peak_pnl = (max_close - entry_price) / entry_price if max_close > 0 else 0.0
                if peak_pnl >= self.profit_guard_gain:
                    if pd.notna(max_close) and max_close > 0 and pd.notna(close):
                        dd = (max_close - close) / max_close
                        if dd >= self.profit_guard_dd:
                            return True
                    ma5 = feats.get("ma5")
                    if pd.notna(ma5) and pd.notna(close) and close < ma5:
                        return True

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
                    "today_ret", "volume_ratio_5", "body_ratio",
                    "full_align", "align_3ma",
                    "strong_close_days_N", "cum_ret_N", "ma5_up", "close_to_ma5_dev",
                    "close_position",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "strong_close_days_N", "cum_ret_N",
                               "ma5_up", "close_to_ma5_dev",
                               "close_position",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 近 5d 强势收盘天数 >= 4
        df = df[df["strong_close_days_N"] >= self.strong_close_days_min]
        if len(df) == 0:
            return df

        # 2. 累计涨幅 3-15%
        df = df[(df["cum_ret_N"] >= self.cumulative_ret_min) &
                (df["cum_ret_N"] <= self.cumulative_ret_max)]
        if len(df) == 0:
            return df

        # 3. MA5 上行
        df = df[df["ma5_up"] == 1]
        if len(df) == 0:
            return df

        # 4. 多头排列
        if self.require_full_align:
            df = df[df["full_align"] == 1]
        else:
            df = df[df["align_3ma"] == 1]
        if len(df) == 0:
            return df

        # 5. close 距 MA5
        df = df[df["close_to_ma5_dev"] <= self.close_to_ma5_max]
        if len(df) == 0:
            return df

        # 6. 20d 涨幅
        df = df[(df["ret_20d"] >= self.ret_20d_min) &
                (df["ret_20d"] <= self.ret_20d_max)]
        if len(df) == 0:
            return df

        # 7. 60d 涨幅 >= 0
        df = df[df["ret_60d"] >= self.ret_60d_min]
        if len(df) == 0:
            return df

        # 8. T 日涨幅
        df = df[(df["today_ret"] >= self.today_ret_min) &
                (df["today_ret"] <= self.today_ret_max)]
        if len(df) == 0:
            return df

        # 9. 量能温和
        df = df[(df["volume_ratio_5"] >= self.today_vol_min) &
                (df["volume_ratio_5"] <= self.today_vol_max)]
        if len(df) == 0:
            return df

        # 10. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 11. close > MA
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

        # 强势收盘天数(主因子)
        strong_days = cross_sectional_zscore(col("strong_close_days_N", 4.0))
        # 当日 close 位置
        close_pos = cross_sectional_zscore(col("close_position", 0.75))
        # 累计涨幅
        cum_ret = cross_sectional_zscore(col("cum_ret_N", 0.05))
        # 60d 涨幅
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.05))
        # T 日量比
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.2))

        scores = (
            strong_days * 0.30 +
            close_pos * 0.25 +
            cum_ret * 0.20 +
            ret_60d * 0.15 +
            vol * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
