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
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class MonthlyWeeklyDailyResonanceStrategy(BaseStrategy):
    """月周日三重共振策略。"""
    name = "monthly_weekly_daily_resonance"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 monthly_ma_short: int = 3,
                 monthly_ret_months: int = 3,
                 weekly_ma_short: int = 5,
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
                 near_high_ratio: float = 0.92,
                 enable_signal_exit: bool = True,
                 exit_min_holding_days: int = 5):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.monthly_ma_short = monthly_ma_short
        self.monthly_ret_months = monthly_ret_months
        self.weekly_ma_short = weekly_ma_short
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
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self._calendar = None  # lazy load in should_exit
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] | None = None
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
        import time as _time
        _t0 = _time.time()
        print("  [月周日三重共振] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        # 过滤 < 60 行的股票(与原逻辑一致),然后用向量化函数算全部特征
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 60].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=30, columns=required_feature_columns(self))

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

        big["full_align"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"]) &
            (big["ma20"] > big["ma60"])
        ).astype(float)
        big["align_3ma"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"])
        ).astype(float)

        # 周线数据
        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_high=("high", "max"),
            week_low=("low", "min"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)
        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_short, min_periods=3).mean())
        weekly["wma10"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_mid, min_periods=5).mean())
        weekly["wma20"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_long, min_periods=10).mean())
        weekly["weekly_bullish"] = (
            (weekly["wma5"] > weekly["wma10"]) &
            (weekly["wma10"] > weekly["wma20"])
        ).astype(float)
        weekly["weekly_ret_N"] = weekly.groupby("stock_code")["week_close"].pct_change(self.weekly_ret_weeks)
        weekly["weekly_up"] = (weekly["weekly_ret_N"] >= 0).astype(float)
        weekly_last = weekly.groupby(["stock_code", "week_key"]).last().reset_index()
        big = big.merge(
            weekly_last[["stock_code", "week_key", "weekly_bullish", "weekly_up", "weekly_ret_N"]],
            on=["stock_code", "week_key"], how="left"
        )

        # 月线数据:用 year-month 作为月键
        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        monthly = big.groupby(["stock_code", "month_key"]).agg(
            month_close=("close", "last"),
            month_high=("high", "max"),
            month_low=("low", "min"),
            month_date=("trade_date", "last")
        ).reset_index()
        monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)
        monthly["mma3"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_short, min_periods=2).mean())
        monthly["monthly_above_ma3"] = (monthly["month_close"] > monthly["mma3"]).astype(float)
        monthly["monthly_ret_N"] = monthly.groupby("stock_code")["month_close"].pct_change(self.monthly_ret_months)
        monthly["monthly_up"] = (monthly["monthly_ret_N"] >= 0).astype(float)
        monthly_last = monthly.groupby(["stock_code", "month_key"]).last().reset_index()
        big = big.merge(
            monthly_last[["stock_code", "month_key", "monthly_above_ma3", "monthly_up", "monthly_ret_N"]],
            on=["stock_code", "month_key"], how="left"
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
        _t3 = _time.time()
        print(f"  [月周日三重共振] 特征矩阵: {big.shape}", flush=True)
        print(f"  [月周日三重共振] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s concat+聚合={_t3-_t2:.1f}s", flush=True)

        # V34: 向量化预计算所有日期的 eligible mask + 信号
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """V34: 向量化预计算所有日期的 eligible 候选。

        一次性算出:
        1. eligible mask(14 重布尔条件,向量化 AND,含参数化分支)
        2. _eligible_by_date = {date: DataFrame[stock_code, 5 因子原值]}

        generate_signals 在 lookup 时:
        - O(1) 取当日 eligible
        - O(K) 过滤 universe
        - O(K) 计算 zscore(与原 _score 完全一致,在 universe ∩ eligible 上标准化)
        - O(K log K) 排序取 top_k

        关键:zscore 在 universe 过滤后计算(与原 _score 完全一致),
        不在预计算阶段做(否则改变标准化总体,导致交易差异)。
        """
        print("  [月周日三重共振] 预计算信号矩阵...", flush=True)

        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "full_align", "align_3ma",
                    "high_N", "breakout_N", "near_high_N",
                    "ret_60d", "ret_20d",
                    "weekly_bullish", "weekly_up", "weekly_ret_N",
                    "monthly_above_ma3", "monthly_up", "monthly_ret_N"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # 1. eligible mask(向量化 AND,替代 _filter_eligible 的 14 重顺序过滤)
        notna_cols = ["close", "ma5", "ma10", "ma20", "ma60",
                      "today_ret", "volume_ratio_5", "body_ratio",
                      "high_N", "ret_60d", "ret_20d",
                      "weekly_bullish", "weekly_up", "weekly_ret_N",
                      "monthly_above_ma3", "monthly_up", "monthly_ret_N"]
        mask = (
            big[notna_cols].notna().all(axis=1) &
            big["monthly_above_ma3"].eq(1) &
            big["monthly_up"].eq(1) &
            big["weekly_bullish"].eq(1) &
            big["weekly_up"].eq(1) &
            (big["full_align"].eq(1) if self.require_full_align else big["align_3ma"].eq(1)) &
            big["breakout_N"].eq(1) &
            big["near_high_N"].eq(1) &
            (big["ret_20d"] >= self.ret_20d_min) &
            (big["ret_20d"] <= self.ret_20d_max) &
            (big["ret_60d"] >= self.ret_60d_min) &
            (big["today_ret"] >= self.today_ret_min) &
            (big["today_ret"] <= self.today_ret_max) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min)
        )
        if self.require_bullish:
            mask &= big["is_bullish"].eq(1)
        if self.require_above_ma == "ma20":
            mask &= big["close"] > big["ma20"]
        elif self.require_above_ma == "ma60":
            mask &= big["close"] > big["ma60"]

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # 2. _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        # 只存评分需要的列(省内存),zscore 留到 lookup 时算(保证与原 _score 总体一致)
        score_cols = ["stock_code", "monthly_ret_N", "weekly_ret_N",
                      "ret_60d", "ret_20d", "volume_ratio_5"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # 3. _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        # 只存出场判断需要的 4 个字段,避免存全特征矩阵爆内存
        exit_cols = ["stock_code", "trade_date", "close", "ma20",
                     "weekly_bullish", "monthly_above_ma3"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma20": row.ma20,
                "weekly_bullish": getattr(row, "weekly_bullish", 1.0),
                "monthly_above_ma3": getattr(row, "monthly_above_ma3", 1.0),
            }

        print(f"  [月周日三重共振] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [月周日三重共振] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        # V34: O(1) 查表替代 O(N) 全表过滤
        if self._eligible_by_date is None:
            self._precompute_features(daily_df)

        eligible = self._eligible_by_date.get(current_date)
        if eligible is None or len(eligible) == 0:
            return []

        universe = self._get_universe(daily_df, current_date)
        if not universe:
            return []
        universe_set = set(universe)

        # O(K) 过滤 universe(K = eligible 数,通常 50-300,远小于 N=3000)
        eligible = eligible[eligible["stock_code"].isin(universe_set)]
        if len(eligible) == 0:
            return []

        # O(K) 计算 zscore(在 universe ∩ eligible 上,与原 _score 完全一致)
        scores = self._score_from_eligible(eligible)
        if len(scores) == 0:
            return []

        # O(K log K) 排序取 top_k
        scores = scores.sort_values(ascending=False)

        signals: list[Signal] = []
        for code, score in scores.items():
            if code in portfolio.positions:
                continue
            signals.append(Signal(code=code, score=float(score),
                                  direction="buy", window="morning",
                                  reason=f"mwd_res={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """信号级出场(保守版):周线破位 / 月线走坏。

        V2 改进(基于 V1 实验反馈):
        - 去掉 "close < MA20" 短期信号(日噪声触发太频繁,V1 胜率掉 12pp)
        - 加持仓时间门槛 exit_min_holding_days(避免刚买入就出场)
        - 只保留 2 个慢信号:weekly_bullish 翻负 / monthly_above_ma3 翻负

        引擎 A2: today_row 实际是 yesterday 数据,today 参数是 yesterday 日期,
        用 yesterday 特征判断,避免前视。

        Returns
        -------
        bool
            True 表示触发出场信号。引擎在 ATR 未触发时才会调用此方法。
        """
        if self._exit_lookup is None or not self.enable_signal_exit:
            return False
        # 持仓时间门槛:未达阈值不检查信号(避免刚买入就出场)
        if self._calendar is None:
            from tradingagents.quant.utils.trading_calendar import get_calendar
            self._calendar = get_calendar()
        holding_days = position.holding_days(today, self._calendar)
        if holding_days < self.exit_min_holding_days:
            return False
        feats = self._exit_lookup.get((position.code, today))
        if feats is None:
            return False

        # 1. 周线多头排列破位(强趋势反转):wma5 <= wma10 或 wma10 <= wma20
        if feats["weekly_bullish"] == 0.0:
            return True
        # 2. 月线收盘跌破月线 MA3(月级别趋势走坏)
        if feats["monthly_above_ma3"] == 0.0:
            return True
        return False

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """与原 _score 完全等价:5 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 5 次 cross_sectional_zscore 函数调用开销)。
        """
        import numpy as np
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["monthly_ret_N", "weekly_ret_N", "ret_60d",
                   "ret_20d", "volume_ratio_5"]
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight in zip(factors, weights, strict=True):
            if factor not in eligible.columns:
                continue
            s = eligible[factor].to_numpy(dtype=np.float64)
            mask = ~np.isnan(s)
            if mask.sum() < 2:
                continue  # zscore = 0 for all
            mean = s[mask].mean()
            std = s[mask].std()
            if std == 0 or np.isnan(std):
                continue  # zscore = 0 for all
            z = np.where(mask, (s - mean) / std, 0.0)
            z = np.clip(z, -3.0, 3.0)
            scores += z * weight

        return pd.Series(scores, index=eligible["stock_code"].to_numpy())

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
