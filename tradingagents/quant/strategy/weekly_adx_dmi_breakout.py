"""中线策略 V36-1:周线 ADX/DMI 趋势强度突破(周线 ADX 上穿 25 + +DI > -DI + 多重多头确认 + 日线放量)。

V36 新方向:ADX/DMI 是经典的趋势强度/方向指标系统。
- ADX 上穿 25 代表趋势确立进入"有趋势"阶段(非盘整)
- +DI > -DI 确认方向为多头
- 周线 + 月线多头过滤下降趋势
- 日线 MA20 确认短线趋势
- T 日放量阳线确认启动
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class WeeklyAdxDmiBreakoutStrategy(BaseStrategy):
    """周线 ADX/DMI 趋势强度突破策略(周线 ADX 上穿 25 + +DI > -DI + 多重多头确认 + 日线放量)。"""
    name = "weekly_adx_dmi_breakout"

    def __init__(self, lookback: int = 200, universe_topk: int = 500,
                 adx_period: int = 14,
                 adx_threshold: float = 25.0,
                 cross_recent_weeks: int = 2,
                 weekly_ma_short: int = 5,
                 weekly_ma_mid: int = 10,
                 monthly_ma_short: int = 3,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 2,
                 exit_stop_loss_pct: float = 0.05,
                 exit_trail_pct: float = 0.05,
                 exit_vol_threshold: float = 2.0,
                 exit_ma_breach_days: int = 3):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.cross_recent_weeks = cross_recent_weeks
        self.weekly_ma_short = weekly_ma_short
        self.weekly_ma_mid = weekly_ma_mid
        self.monthly_ma_short = monthly_ma_short
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
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
        print("  [周线 ADX/DMI 突破] 预计算特征矩阵...", flush=True)
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 90].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=30, columns=required_feature_columns(self))

        _t2 = _time.time()
        print(f"    build_features_vectorized: {_t2-_t1:.1f}s ({len(big)} 行)", flush=True)
        if len(big) == 0:
            self._feature_cache = pd.DataFrame()
            return self._feature_cache
        big["stock_code"] = big["stock_code"].astype(str)
        big = big.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        big["prev_close_raw"] = big.groupby("stock_code")["close"].shift(1)
        big["today_ret"] = (big["close"] - big["prev_close_raw"]) / big["prev_close_raw"].replace(0, np.nan)

        body = big["close"] - big["open"]
        hl = (big["high"] - big["low"]).replace(0, np.nan)
        big["body_ratio"] = (body / hl).fillna(0)
        big["is_bullish"] = (big["close"] > big["open"]).astype(float)

        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_high=("high", "max"),
            week_low=("low", "min"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)

        adx_p = self.adx_period

        weekly["week_high_prev"] = weekly.groupby("stock_code")["week_high"].shift(1)
        weekly["week_low_prev"] = weekly.groupby("stock_code")["week_low"].shift(1)
        weekly["week_close_prev"] = weekly.groupby("stock_code")["week_close"].shift(1)

        up_move = weekly["week_high"] - weekly["week_high_prev"]
        down_move = weekly["week_low_prev"] - weekly["week_low"]

        weekly["pos_dm"] = np.where(
            (up_move > down_move) & (up_move > 0),
            up_move, 0.0
        )
        weekly["neg_dm"] = np.where(
            (down_move > up_move) & (down_move > 0),
            down_move, 0.0
        )

        tr_parts = pd.concat([
            weekly["week_high"] - weekly["week_low"],
            (weekly["week_high"] - weekly["week_close_prev"]).abs(),
            (weekly["week_low"] - weekly["week_close_prev"]).abs()
        ], axis=1)
        weekly["tr"] = tr_parts.max(axis=1)

        weekly["sma_tr"] = weekly.groupby("stock_code")["tr"].transform(
            lambda s: s.rolling(adx_p, min_periods=adx_p).mean())
        weekly["sma_pos_dm"] = weekly.groupby("stock_code")["pos_dm"].transform(
            lambda s: s.rolling(adx_p, min_periods=adx_p).mean())
        weekly["sma_neg_dm"] = weekly.groupby("stock_code")["neg_dm"].transform(
            lambda s: s.rolling(adx_p, min_periods=adx_p).mean())

        sma_tr_safe = weekly["sma_tr"].replace(0, np.nan)
        weekly["pos_di"] = weekly["sma_pos_dm"] / sma_tr_safe * 100
        weekly["neg_di"] = weekly["sma_neg_dm"] / sma_tr_safe * 100

        di_sum = weekly["pos_di"] + weekly["neg_di"]
        di_diff = (weekly["pos_di"] - weekly["neg_di"]).abs()
        weekly["dx"] = di_diff / di_sum.replace(0, np.nan) * 100

        weekly["adx"] = weekly.groupby("stock_code")["dx"].transform(
            lambda s: s.rolling(adx_p, min_periods=adx_p).mean())

        weekly["adx_prev"] = weekly.groupby("stock_code")["adx"].shift(1)
        weekly["adx_cross"] = (
            (weekly["adx_prev"] <= self.adx_threshold) &
            (weekly["adx"] > self.adx_threshold)
        ).astype(float)
        weekly["adx_cross_recent"] = weekly.groupby("stock_code")["adx_cross"].transform(
            lambda s: s.rolling(self.cross_recent_weeks, min_periods=1).max()
        )

        weekly["di_bullish"] = (weekly["pos_di"] > weekly["neg_di"]).astype(float)

        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_short, min_periods=3).mean())
        weekly["wma10"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_mid, min_periods=5).mean())
        weekly["weekly_bullish"] = (
            (weekly["week_close"] > weekly["wma5"]) &
            (weekly["wma5"] > weekly["wma10"])
        ).astype(float)

        weekly_last = weekly.groupby(["stock_code", "week_key"]).last().reset_index()
        weekly_to_merge = weekly_last[["stock_code", "week_date",
                         "adx", "pos_di", "neg_di",
                         "adx_cross_recent", "di_bullish",
                         "weekly_bullish"]].sort_values("week_date").dropna(subset=["stock_code"])
        big = pd.merge_asof(
            big.sort_values("trade_date").dropna(subset=["stock_code"]),
            weekly_to_merge,
            left_on="trade_date", right_on="week_date",
            by="stock_code", direction="backward",
        )

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
        monthly_to_merge = monthly_last[["stock_code", "month_date", "monthly_above_ma3", "mma3"]].sort_values("month_date").dropna(subset=["stock_code"])
        big = pd.merge_asof(
            big.sort_values("trade_date").dropna(subset=["stock_code"]),
            monthly_to_merge,
            left_on="trade_date", right_on="month_date",
            by="stock_code", direction="backward",
        )

        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        _t3 = _time.time()
        print(f"  [周线 ADX/DMI 突破] 特征矩阵: {big.shape}", flush=True)
        print(f"  [周线 ADX/DMI 突破] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s 聚合={_t3-_t2:.1f}s", flush=True)

        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        print("  [周线 ADX/DMI 突破] 预计算信号矩阵...", flush=True)

        required = ["close", "ma20",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "adx_cross_recent", "di_bullish", "weekly_bullish",
                    "monthly_above_ma3",
                    "ret_60d"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        mask = (
            big[required].notna().all(axis=1) &
            big["adx_cross_recent"].eq(1) &
            big["di_bullish"].eq(1) &
            big["weekly_bullish"].eq(1) &
            big["monthly_above_ma3"].eq(1) &
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

        score_cols = ["stock_code", "adx", "pos_di", "neg_di",
                      "ret_60d", "volume_ratio_5", "today_ret"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        exit_cols = ["stock_code", "trade_date", "close", "ma5", "ma20",
                     "volume_ratio_5", "is_bullish",
                     "weekly_bullish", "monthly_above_ma3"]
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
                "weekly_bullish": getattr(row, "weekly_bullish", 1.0),
                "monthly_above_ma3": getattr(row, "monthly_above_ma3", 1.0),
            }

        print(f"  [周线 ADX/DMI 突破] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [周线 ADX/DMI 突破] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  direction="buy", window="morning",
                                  reason=f"w_adx_dmi_bo={score:.2f}"))
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
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        eligible = eligible.copy()
        if "pos_di" in eligible.columns and "neg_di" in eligible.columns:
            eligible["pos_di_spread"] = eligible["pos_di"] - eligible["neg_di"]

        factors = ["adx", "pos_di_spread", "ret_60d", "volume_ratio_5", "today_ret"]
        weights = [0.30, 0.20, 0.20, 0.15, 0.15]
        signs = [1.0, 1.0, 1.0, 1.0, 1.0]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight, sign in zip(factors, weights, signs, strict=True):
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

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma20",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "adx_cross_recent", "di_bullish", "weekly_bullish",
                    "monthly_above_ma3",
                    "ret_60d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma20",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "adx_cross_recent", "di_bullish", "weekly_bullish",
                               "monthly_above_ma3",
                               "ret_60d"])
        if len(df) == 0:
            return df

        df = df[df["adx_cross_recent"] == 1]
        if len(df) == 0:
            return df

        df = df[df["di_bullish"] == 1]
        if len(df) == 0:
            return df

        df = df[df["weekly_bullish"] == 1]
        if len(df) == 0:
            return df

        df = df[df["monthly_above_ma3"] == 1]
        if len(df) == 0:
            return df

        df = df[df["close"] > df["ma20"]]
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

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        adx = cross_sectional_zscore(col("adx", 25.0))
        pos_di_spread = cross_sectional_zscore(col("pos_di", 0) - col("neg_di", 0))
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            adx * 0.30 +
            pos_di_spread * 0.20 +
            ret_60d * 0.20 +
            vol * 0.15 +
            strength * 0.15
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
