"""中线策略 V28-1:月线 MACD 金叉中线(月线级别 MACD 金叉 + 多重多头确认)。

V28 新方向(基于 V25 w_macd_gc 主回测 comp 7.20 / M_S 的成功经验深化):
- 与 V25-2 weekly_macd_golden_cross(周线 MACD 金叉)不同
- 与 V21-3 macd_zero_axis_cross(日线 MACD 零轴上方金叉)不同
- 本策略:月线 MACD 金叉(月线 EMA12 上穿 EMA26 的 Signal)+ 月线多头 + 周线多头 + 日线放量
- 与 V25-2 区别:V25-2 是周线 MACD 金叉,本策略是月线 MACD 金叉(更长周期,更稳定的中线信号)
- 与 V21-3 区别:V21-3 是日线 MACD 金叉,本策略是月线 MACD 金叉(过滤短期噪音)

核心思路(月线 MACD 金叉 + 月线多头 + 周线多头 + 日线放量):
- 月线 MACD 金叉(MACD 上穿 Signal)发生在最近 2 个月内
- 月线多头:月线 close > 月线 MA3 > 月线 MA6
- 周线多头:周线 close > 周线 MA5
- 日线:close > MA20
- T 日放量阳线 volume_ratio_5 >= 1.5,body_ratio >= 0.5

中线逻辑:月线 MACD 金叉是中线趋势启动的最稳定信号,叠加多重多头确认,持仓 10-15d 让趋势延续。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class MonthlyMacdGoldenCrossStrategy(BaseStrategy):
    """月线 MACD 金叉中线策略(月线 MACD 金叉 + 月/周线多头 + 日线放量)。"""
    name = "monthly_macd_golden_cross"

    def __init__(self, lookback: int = 260, universe_topk: int = 500,
                 monthly_macd_fast: int = 12,
                 monthly_macd_slow: int = 26,
                 monthly_macd_signal: int = 9,
                 golden_cross_recent_months: int = 2,
                 monthly_ma_short: int = 3,
                 monthly_ma_mid: int = 6,
                 weekly_ma_short: int = 5,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 near_high_ratio: float = 0.95,
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 2,
                 exit_stop_loss_pct: float = 0.05,
                 exit_trail_pct: float = 0.05,
                 exit_vol_threshold: float = 2.0,
                 exit_ma_breach_days: int = 3):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.monthly_macd_fast = monthly_macd_fast
        self.monthly_macd_slow = monthly_macd_slow
        self.monthly_macd_signal = monthly_macd_signal
        self.golden_cross_recent_months = golden_cross_recent_months
        self.monthly_ma_short = monthly_ma_short
        self.monthly_ma_mid = monthly_ma_mid
        self.weekly_ma_short = weekly_ma_short
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.near_high_ratio = near_high_ratio
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self.exit_stop_loss_pct = exit_stop_loss_pct
        self.exit_trail_pct = exit_trail_pct
        self.exit_vol_threshold = exit_vol_threshold
        self.exit_ma_breach_days = exit_ma_breach_days
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
        print("  [月线 MACD 金叉] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        # 过滤 < 120 行的股票(与原逻辑一致),然后用向量化函数算全部特征
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 120].index
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

        # 周线数据:close > 周线 MA5
        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)
        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_short, min_periods=3).mean())
        weekly["weekly_above_ma5"] = (weekly["week_close"] > weekly["wma5"]).astype(float)
        weekly_last = weekly.groupby(["stock_code", "week_key"]).last().reset_index()
        big = big.merge(
            weekly_last[["stock_code", "week_key", "weekly_above_ma5", "wma5"]],
            on=["stock_code", "week_key"], how="left"
        )

        # 月线数据:close=月末close, high=月最高, low=月最低, volume=月总量
        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        monthly = big.groupby(["stock_code", "month_key"]).agg(
            month_close=("close", "last"),
            month_high=("high", "max"),
            month_low=("low", "min"),
            month_volume=("volume", "sum"),
            month_date=("trade_date", "last")
        ).reset_index()
        monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)

        # 月线 EMA12, EMA26
        monthly["mema12"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.ewm(span=self.monthly_macd_fast, adjust=False).mean())
        monthly["mema26"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.ewm(span=self.monthly_macd_slow, adjust=False).mean())
        # 月线 MACD = EMA12 - EMA26
        monthly["mmacd"] = monthly["mema12"] - monthly["mema26"]
        # 月线 Signal = MACD 的 EMA9
        monthly["msignal"] = monthly.groupby("stock_code")["mmacd"].transform(
            lambda s: s.ewm(span=self.monthly_macd_signal, adjust=False).mean())
        # 月线 MACD 柱状图
        monthly["mmacd_hist"] = monthly["mmacd"] - monthly["msignal"]

        # 月线 MACD 金叉:MACD 上穿 Signal(前一月 MACD <= Signal,本月 MACD > Signal)
        monthly["mmacd_prev"] = monthly.groupby("stock_code")["mmacd"].shift(1)
        monthly["msignal_prev"] = monthly.groupby("stock_code")["msignal"].shift(1)
        monthly["mmacd_golden_cross"] = (
            (monthly["mmacd_prev"] <= monthly["msignal_prev"]) &
            (monthly["mmacd"] > monthly["msignal"])
        ).astype(float)

        # 最近 N 月内是否发生过金叉
        monthly["mmacd_gc_recent"] = monthly.groupby("stock_code")["mmacd_golden_cross"].transform(
            lambda s: s.rolling(self.golden_cross_recent_months, min_periods=1).max()
        )

        # 月线 MA3, MA6
        monthly["mma3"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_short, min_periods=2).mean())
        monthly["mma6"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_mid, min_periods=3).mean())

        # 月线多头:close > MA3 > MA6
        monthly["monthly_bullish"] = (
            (monthly["month_close"] > monthly["mma3"]) &
            (monthly["mma3"] > monthly["mma6"])
        ).astype(float)

        # 合并月线指标到日线(同一月的所有日共用同一组月线值)
        monthly_last = monthly.groupby(["stock_code", "month_key"]).last().reset_index()
        big = big.merge(
            monthly_last[["stock_code", "month_key",
                          "mmacd_gc_recent", "monthly_bullish",
                          "mmacd", "msignal", "mmacd_hist",
                          "mma3", "mma6"]],
            on=["stock_code", "month_key"], how="left"
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        _t3 = _time.time()
        print(f"  [月线 MACD 金叉] 特征矩阵: {big.shape}", flush=True)
        print(f"  [月线 MACD 金叉] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s 聚合={_t3-_t2:.1f}s", flush=True)

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
        print("  [月线 MACD 金叉] 预计算信号矩阵...", flush=True)

        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "mmacd_gc_recent", "monthly_bullish",
                    "mmacd", "msignal", "mmacd_hist",
                    "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # 1. eligible mask(向量化 AND,替代 _filter_eligible 的顺序过滤)
        mask = (
            big[required].notna().all(axis=1) &
            big["mmacd_gc_recent"].eq(1) &
            big["monthly_bullish"].eq(1) &
            big["weekly_above_ma5"].eq(1) &
            (big["close"] > big["ma20"]) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min)
        )
        if self.require_bullish:
            mask &= big["is_bullish"].eq(1)

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # 2. _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        score_cols = ["stock_code", "mmacd_hist", "ret_60d", "ret_20d",
                      "volume_ratio_5", "today_ret"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # 3. _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        # V3 快信号:close, ma5, volume_ratio_5, is_bullish
        exit_cols = ["stock_code", "trade_date", "close", "ma5", "ma20",
                     "volume_ratio_5", "is_bullish",
                     "weekly_above_ma5", "monthly_bullish"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma5"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma5": row.ma5,
                "ma20": getattr(row, "ma20", None),
                "volume_ratio_5": getattr(row, "volume_ratio_5", None),
                "is_bullish": getattr(row, "is_bullish", 1.0),
                "weekly_above_ma5": getattr(row, "weekly_above_ma5", 1.0),
                "monthly_bullish": getattr(row, "monthly_bullish", 1.0),
            }

        print(f"  [月线 MACD 金叉] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [月线 MACD 金叉] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  reason=f"m_macd_gc={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """V3 快信号出场:保护性止损 + 涨幅回吐 + MA5 破位 + 放量阴线。

        取消 max_holding,完全用日线级别快信号出场。
        引擎 ATR 止损/移动止盈作为基础保护(use_atr_exit=True 时 max_close_since_entry 自动维护)。

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

        close = feats["close"]
        entry_price = position.entry_price
        max_close = position.max_close_since_entry

        # 1. 保护性止损:close < entry * (1 - stop_loss_pct)
        if pd.notna(close) and entry_price > 0:
            if close < entry_price * (1 - self.exit_stop_loss_pct):
                return True

        # 2. 涨幅回吐:从 max_close 回吐 > trail_pct(只在盈利时有效)
        if pd.notna(max_close) and max_close > entry_price and pd.notna(close):
            drawdown = (max_close - close) / max_close
            if drawdown > self.exit_trail_pct:
                return True

        # 3. 短期破位:close < MA5(持仓 >= exit_ma_breach_days 后检查)
        if holding_days >= self.exit_ma_breach_days:
            ma5 = feats.get("ma5")
            if pd.notna(ma5) and pd.notna(close) and close < ma5:
                return True

        # 4. 放量阴线:volume_ratio_5 > vol_threshold AND is_bullish==0
        vol_ratio = feats.get("volume_ratio_5")
        is_bullish = feats.get("is_bullish", 1.0)
        if pd.notna(vol_ratio) and vol_ratio > self.exit_vol_threshold and is_bullish == 0.0:
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

        factors = ["mmacd_hist", "ret_60d", "ret_20d", "volume_ratio_5", "today_ret"]
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
                    "mmacd_gc_recent", "monthly_bullish",
                    "mmacd", "msignal", "mmacd_hist",
                    "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "mmacd_gc_recent", "monthly_bullish",
                               "mmacd", "msignal",
                               "weekly_above_ma5",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 月线 MACD 金叉(最近 N 月内)
        df = df[df["mmacd_gc_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 月线多头:close > MA3 > MA6
        df = df[df["monthly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 3. 周线多头:close > MA5
        df = df[df["weekly_above_ma5"] == 1]
        if len(df) == 0:
            return df

        # 4. close > MA20
        df = df[df["close"] > df["ma20"]]
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

        # 月线 MACD 柱状图(主因子 1:hist 越大越好,金叉后动能强)
        mmacd_hist = cross_sectional_zscore(col("mmacd_hist", 0.0))
        # 60d 涨幅(主因子 2:中期趋势强度)
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            mmacd_hist * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
