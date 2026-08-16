"""方向 KKK:低位长期横盘 + 放量突破。

60日波动率低 + 60日内涨幅小 + T 日突破20日新高 + 放量
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
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
                 require_above_ma: str = "ma20",
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 2,
                 exit_vol_floor: float = 0.15,
                 exit_stop_loss_pct: float = 0.05,
                 exit_trail_pct: float = 0.05,
                 exit_vol_threshold: float = 2.0,
                 exit_ma_breach_days: int = 3):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.vol_window = vol_window
        self.vol_max = vol_max
        self.ret_60d_max = ret_60d_max
        self.today_ret_min = today_ret_min
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_above_ma = require_above_ma
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self.exit_vol_floor = exit_vol_floor
        self.exit_stop_loss_pct = exit_stop_loss_pct
        self.exit_trail_pct = exit_trail_pct
        self.exit_vol_threshold = exit_vol_threshold
        self.exit_ma_breach_days = exit_ma_breach_days
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
        print("  [低波动突破] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        # 过滤 < 30 行的股票(与原逻辑一致),然后用向量化函数算全部特征
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 30].index
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
        _t3 = _time.time()
        print(f"  [低波动突破] 特征矩阵: {big.shape}", flush=True)
        print(f"  [低波动突破] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s 聚合={_t3-_t2:.1f}s", flush=True)

        # V34: 向量化预计算所有日期的 eligible mask + 信号
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """V34: 向量化预计算所有日期的 eligible 候选。

        一次性算出:
        1. eligible mask(8 重布尔条件,向量化 AND)
        2. _eligible_by_date = {date: DataFrame[stock_code, 4 因子原值]}

        generate_signals 在 lookup 时:
        - O(1) 取当日 eligible
        - O(K) 过滤 universe
        - O(K) 计算 zscore(与原 _score 完全一致,在 universe ∩ eligible 上标准化)
        - O(K log K) 排序取 top_k

        关键:zscore 在 universe 过滤后计算(与原 _score 完全一致),
        不在预计算阶段做(否则改变标准化总体,导致交易差异)。
        """
        print("  [低波动突破] 预计算信号矩阵...", flush=True)

        required = ["close", "open", "high", "low", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "volatility_N", "ret_60d_calc", "breaks_20high"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # 1. eligible mask(向量化 AND,替代 _filter_eligible 的顺序过滤)
        mask = (
            big[required].notna().all(axis=1) &
            (big["volatility_N"] <= self.vol_max) &
            (big["ret_60d_calc"] <= self.ret_60d_max) &
            big["breaks_20high"].eq(1) &
            (big["today_ret"] >= self.today_ret_min) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min) &
            big["is_bullish"].eq(1)
        )
        if self.require_above_ma == "ma20":
            mask &= (big["close"] > big["ma20"])
        elif self.require_above_ma == "ma60":
            mask &= (big["close"] > big["ma60"])

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # 2. _eligible_by_date: {date: DataFrame[stock_code, 4 因子原值]}
        # 只存评分需要的列(省内存),zscore 留到 lookup 时算(保证与原 _score 总体一致)
        score_cols = ["stock_code", "volatility_N", "today_ret",
                      "volume_ratio_5", "body_ratio"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # 3. _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        # V3 快信号:close, ma5, volume_ratio_5, is_bullish, volatility_N
        exit_cols = ["stock_code", "trade_date", "close", "ma5", "ma20",
                     "volume_ratio_5", "is_bullish", "volatility_N"]
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
                "volatility_N": getattr(row, "volatility_N", None),
            }

        print(f"  [低波动突破] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [低波动突破] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  reason=f"lvb={score:.2f}"))
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
        """与原 _score 完全等价:4 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 4 次 cross_sectional_zscore 函数调用开销)。
        volatility_N 取负(波动率越低越加分)。
        """
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["volatility_N", "today_ret", "volume_ratio_5", "body_ratio"]
        weights = [0.20, 0.35, 0.30, 0.15]
        signs = [-1.0, 1.0, 1.0, 1.0]  # volatility_N 取负

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
