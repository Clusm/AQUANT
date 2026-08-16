"""中线策略 V25-2:周线 MACD 金叉中线(周线级别 MACD 金叉 + 多重多头确认)。

V25 新方向(基于 V18 mwd_res_loose 月周日共振 + V21 MACD 零轴金叉的双重经验深化):
- 与 V21-3 macd_zero_axis_cross(日线 MACD 零轴上方金叉)不同
- 与 V18-1 monthly_weekly_daily_resonance(月周日三均线多头共振)不同
- 本策略:周线 MACD 金叉(周线 EMA12 上穿 EMA26 的 Signal)+ 周线多头 + 月线多头 + 日线放量
- 与 V21-3 区别:V21-3 是日线 MACD 金叉,本策略是周线 MACD 金叉(更稳定的中线信号)
- 与 V18-1 区别:V18-1 是均线多头共振,本策略增加周线 MACD 金叉作为时点触发信号

核心思路(周线 MACD 金叉 + 周线多头 + 月线多头 + 日线放量):
- 周线 MACD 金叉(MACD 上穿 Signal)发生在最近 2 周内
- 周线多头:周线 close > 周线 MA5 > 周线 MA10
- 月线多头:月线 close > 月线 MA3
- 日线:close > MA10
- T 日放量阳线 volume_ratio_5 >= 1.5,body_ratio >= 0.5

中线逻辑:周线 MACD 金叉是中线趋势启动的可靠信号,叠加多重多头确认,持仓 10-15d 让趋势延续。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class WeeklyMacdGoldenCrossStrategy(BaseStrategy):
    """周线 MACD 金叉中线策略(周线 MACD 金叉 + 周/月线多头 + 日线放量)。"""
    name = "weekly_macd_golden_cross"

    def __init__(self, lookback: int = 200, universe_topk: int = 500,
                 weekly_macd_fast: int = 12,
                 weekly_macd_slow: int = 26,
                 weekly_macd_signal: int = 9,
                 golden_cross_recent_weeks: int = 2,
                 weekly_ma_short: int = 5,
                 weekly_ma_mid: int = 10,
                 monthly_ma_short: int = 3,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 dif_above_zero: bool = False,
                 enable_signal_exit: bool = True,
                 exit_min_holding_days: int = 5):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.weekly_macd_fast = weekly_macd_fast
        self.weekly_macd_slow = weekly_macd_slow
        self.weekly_macd_signal = weekly_macd_signal
        self.golden_cross_recent_weeks = golden_cross_recent_weeks
        self.weekly_ma_short = weekly_ma_short
        self.weekly_ma_mid = weekly_ma_mid
        self.monthly_ma_short = monthly_ma_short
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.dif_above_zero = dif_above_zero
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self._calendar = None
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
        print("  [周线 MACD 金叉] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        # 过滤 < 90 行的股票(与原逻辑一致),然后用向量化函数算全部特征
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 90].index
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

        # 周线数据:close=周末close
        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)

        # 周线 EMA12, EMA26
        weekly["wema12"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.ewm(span=self.weekly_macd_fast, adjust=False).mean())
        weekly["wema26"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.ewm(span=self.weekly_macd_slow, adjust=False).mean())
        # 周线 MACD = EMA12 - EMA26
        weekly["wmacd"] = weekly["wema12"] - weekly["wema26"]
        # 周线 Signal = MACD 的 EMA9
        weekly["wsignal"] = weekly.groupby("stock_code")["wmacd"].transform(
            lambda s: s.ewm(span=self.weekly_macd_signal, adjust=False).mean())
        # 周线 MACD 柱状图
        weekly["wmacd_hist"] = weekly["wmacd"] - weekly["wsignal"]

        # 周线 MACD 金叉:MACD 上穿 Signal(前一周 MACD <= Signal,本周 MACD > Signal)
        weekly["wmacd_prev"] = weekly.groupby("stock_code")["wmacd"].shift(1)
        weekly["wsignal_prev"] = weekly.groupby("stock_code")["wsignal"].shift(1)
        weekly["wmacd_golden_cross"] = (
            (weekly["wmacd_prev"] <= weekly["wsignal_prev"]) &
            (weekly["wmacd"] > weekly["wsignal"])
        ).astype(float)

        # 最近 N 周内是否发生过金叉
        weekly["wmacd_gc_recent"] = weekly.groupby("stock_code")["wmacd_golden_cross"].transform(
            lambda s: s.rolling(self.golden_cross_recent_weeks, min_periods=1).max()
        )

        # 周线 MA5, MA10
        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_short, min_periods=3).mean())
        weekly["wma10"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_mid, min_periods=5).mean())

        # 周线多头:close > MA5 > MA10
        weekly["weekly_bullish"] = (
            (weekly["week_close"] > weekly["wma5"]) &
            (weekly["wma5"] > weekly["wma10"])
        ).astype(float)

        # 合并周线指标到日线(同一周的所有日共用同一组周线值)
        weekly_last = weekly.groupby(["stock_code", "week_key"]).last().reset_index()
        big = big.merge(
            weekly_last[["stock_code", "week_key",
                         "wmacd_gc_recent", "weekly_bullish",
                         "wmacd", "wsignal", "wmacd_hist"]],
            on=["stock_code", "week_key"], how="left"
        )

        # 月线数据:close=月末close
        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        monthly = big.groupby(["stock_code", "month_key"]).agg(
            month_close=("close", "last"),
            month_date=("trade_date", "last")
        ).reset_index()
        monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)
        monthly["mma3"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_short, min_periods=2).mean())
        monthly["monthly_above_ma3"] = (monthly["month_close"] > monthly["mma3"]).astype(float)
        monthly_last = monthly.groupby(["stock_code", "month_key"]).last().reset_index()
        big = big.merge(
            monthly_last[["stock_code", "month_key", "monthly_above_ma3", "mma3"]],
            on=["stock_code", "month_key"], how="left"
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        _t3 = _time.time()
        print(f"  [周线 MACD 金叉] 特征矩阵: {big.shape}", flush=True)
        print(f"  [周线 MACD 金叉] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s 聚合={_t3-_t2:.1f}s", flush=True)

        # V34: 向量化预计算所有日期的 eligible mask + 信号
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """V34: 向量化预计算所有日期的 eligible 候选。

        一次性算出:
        1. eligible mask(7 重布尔条件,向量化 AND)
        2. _eligible_by_date = {date: DataFrame[stock_code, 5 因子原值]}

        generate_signals 在 lookup 时:
        - O(1) 取当日 eligible
        - O(K) 过滤 universe
        - O(K) 计算 zscore(与原 _score 完全一致,在 universe ∩ eligible 上标准化)
        - O(K log K) 排序取 top_k

        关键:zscore 在 universe 过滤后计算(与原 _score 完全一致),
        不在预计算阶段做(否则改变标准化总体,导致交易差异)。
        """
        print("  [周线 MACD 金叉] 预计算信号矩阵...", flush=True)

        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "wmacd_gc_recent", "weekly_bullish",
                    "wmacd", "wsignal", "wmacd_hist",
                    "monthly_above_ma3",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # 1. eligible mask(向量化 AND,替代 _filter_eligible 的顺序过滤)
        mask = (
            big[required].notna().all(axis=1) &
            big["wmacd_gc_recent"].eq(1) &
            big["weekly_bullish"].eq(1) &
            big["monthly_above_ma3"].eq(1) &
            (big["close"] > big["ma10"]) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min)
        )
        if self.require_bullish:
            mask &= big["is_bullish"].eq(1)

        # P-B-015: DIF>0 零轴上方金叉 (零轴下方金叉多为下跌中继/弱反弹)
        if self.dif_above_zero:
            mask &= big["wmacd"].gt(0)

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # 2. _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        score_cols = ["stock_code", "wmacd_hist", "ret_60d", "ret_20d",
                      "volume_ratio_5", "today_ret"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # 3. _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        exit_cols = ["stock_code", "trade_date", "close", "ma10",
                     "weekly_bullish", "monthly_above_ma3"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma10"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma10": row.ma10,
                "weekly_bullish": getattr(row, "weekly_bullish", 1.0),
                "monthly_above_ma3": getattr(row, "monthly_above_ma3", 1.0),
            }

        print(f"  [周线 MACD 金叉] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [周线 MACD 金叉] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        # V34: O(1) 查表替代 O(N) 全表过滤
        if self._eligible_by_date is None:
            self._precompute_features(daily_df)

        eligible = self._eligible_by_date.get(current_date)
        if eligible is None or len(eligible) == 0:
            return []

        universe = self._get_universe(daily_df, current_date)
        if not universe or len(universe) < 5:
            return []
        universe_set = set(universe)

        # O(K) 过滤 universe
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
        for code, score in scores.head(top_k * 2).items():
            if code in portfolio.positions:
                continue
            signals.append(Signal(code=code, score=float(score),
                                  direction="buy", window="morning",
                                  reason=f"w_macd_gc={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """信号级出场(保守版):周线多头破位 / 月线 close 跌破 mma3。

        复用 M_mwd_res V2 模式:持仓 >= exit_min_holding_days 后,
        只看慢信号(周/月级别),不看日线 MA10(日噪声触发太频繁)。

        引擎 A2: today_row 实际是 yesterday 数据,today 参数是 yesterday 日期,
        用 yesterday 特征判断,避免前视。
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

        if feats["weekly_bullish"] == 0.0:
            return True
        if feats["monthly_above_ma3"] == 0.0:
            return True
        return False

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """与原 _score 完全等价:5 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 5 次 cross_sectional_zscore 函数调用开销)。
        无取负因子。
        """
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["wmacd_hist", "ret_60d", "ret_20d", "volume_ratio_5", "today_ret"]
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]
        signs = [1.0, 1.0, 1.0, 1.0, 1.0]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight, sign in zip(factors, weights, signs, strict=True):
            if factor not in eligible.columns:
                continue
            s = eligible[factor].to_numpy(dtype=np.float64) * sign
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
                    "wmacd_gc_recent", "weekly_bullish",
                    "wmacd", "wsignal", "wmacd_hist",
                    "monthly_above_ma3",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "wmacd_gc_recent", "weekly_bullish",
                               "wmacd", "wsignal",
                               "monthly_above_ma3",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 周线 MACD 金叉(最近 N 周内)
        df = df[df["wmacd_gc_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 周线多头:close > MA5 > MA10
        df = df[df["weekly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 3. 月线多头:close > MA3
        df = df[df["monthly_above_ma3"] == 1]
        if len(df) == 0:
            return df

        # 4. close > MA10
        df = df[df["close"] > df["ma10"]]
        if len(df) == 0:
            return df

        # 5. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 6. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 7. 阳线
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

        # 周线 MACD 柱状图(主因子 1:hist 越大越好,金叉后动能强)
        wmacd_hist = cross_sectional_zscore(col("wmacd_hist", 0.0))
        # 60d 涨幅(主因子 2:中期趋势强度)
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            wmacd_hist * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
