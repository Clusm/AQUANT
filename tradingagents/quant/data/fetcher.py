"""统一数据获取层:sina 源为主(akshare),eastmoney 不可用作为备用。

数据源策略:
- ak.stock_zh_a_spot (sina): 全市场当前快照,一次性返回 5500+ 股票,用于流动性筛选
- ak.stock_zh_a_daily (sina): 单只历史日线(qfq),~7s/只,稳定可靠
- adata.get_market (eastmoney): 不可用(被限流),保留作 fallback
- ak.stock_zh_a_st_em: 当前 ST 列表
- ak.stock_lhb_detail_em: 龙虎榜
- ak.stock_zh_index_daily_em: 指数基准

为控制下载时间,默认仅取流动性 top N(默认 500)的主板股票。
"""
from __future__ import annotations

import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from tradingagents.quant.data import cache as cache_mod
from tradingagents.quant.sina_fetcher import symbol_with_prefix

warnings.filterwarnings("ignore")


def _ak():
    try:
        import akshare as ak
        return ak
    except ImportError as e:
        raise RuntimeError(
            "缺少可选依赖 akshare。日线/增量主链路已直连 HTTP 无需 akshare,"
            "但全市场代码/上市日期、ST 列表、全量建库(download_all)仍需。"
            "请先 pip install akshare adata"
        ) from e


def _adata():
    try:
        import adata
        return adata
    except ImportError as e:
        raise RuntimeError(
            "缺少可选依赖 adata(全市场代码/上市日期优先用 adata)。"
            "请先 pip install akshare adata"
        ) from e


SH_MAIN_PREFIX = "60"
SZ_MAIN_PREFIX = "00"


def is_main_board(code: str) -> bool:
    return code.startswith(SH_MAIN_PREFIX) or code.startswith(SZ_MAIN_PREFIX)


def fetch_all_stock_codes() -> pd.DataFrame:
    """获取全部 A 股代码列表。

    优先 adata.all_code(含上市日期),失败回退 ak.stock_zh_a_spot。
    返回列:stock_code, short_name, exchange, list_date
    """
    if cache_mod.exists("all_codes"):
        return cache_mod.load("all_codes")

    try:
        adata = _adata()
        df = adata.stock.info.all_code()
        if df is not None and len(df) > 0:
            df = df.copy()
            df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
            cache_mod.save("all_codes", df,
                           meta={"source": "adata.all_code",
                                 "fetched_at": datetime.now().isoformat()})
            return df
    except Exception as e:
        print(f"  adata.all_code 失败: {e}")

    print("  回退到 ak.stock_zh_a_spot...")
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot()
    except Exception as e:
        # 无 akshare/adata 且无 all_codes 缓存:运行时策略不得崩溃。
        # filter_universe_topk 对 list_dates 仅是回退(优先用日线缓存推断上市日期),
        # 返回空列表即可让策略退化为纯缓存推断。
        print(f"  警告: 全市场代码列表不可用({e});返回空列表,策略将退化为基于日线缓存推断")
        return pd.DataFrame(columns=["stock_code", "short_name", "exchange", "list_date"])
    df = df.rename(columns={"代码": "stock_code", "名称": "short_name"})
    df["stock_code"] = df["stock_code"].str.replace("sh", "").str.replace("sz", "").str.replace("bj", "")
    df["exchange"] = df["stock_code"].apply(lambda c: "SH" if c.startswith("6") else "SZ")
    df["list_date"] = pd.NaT
    cache_mod.save("all_codes", df[["stock_code", "short_name", "exchange", "list_date"]],
                   meta={"source": "ak.stock_zh_a_spot",
                         "fetched_at": datetime.now().isoformat()})
    return df


def fetch_stock_history_sina(code: str, start_date: str, end_date: str,
                              adjust: str = "qfq") -> pd.DataFrame:
    """sina 源单只历史日线(akshare)。

    返回统一列名:stock_code, trade_date, open, high, low, close, volume,
                amount, turnover(换手率,小数), outstanding_share
    """
    ak = _ak()
    try:
        symbol = symbol_with_prefix(code)
        df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust=adjust)
    except Exception:
        return pd.DataFrame()

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()
    df = df.rename(columns={"date": "trade_date", "turnover": "turnover_ratio"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    df["stock_code"] = code
    df["pre_close"] = df["close"].shift(1)
    df["change_pct"] = df["close"].pct_change() * 100
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def fetch_stock_history_adata(code: str, start_date: str, end_date: str,
                               k_type: int = 1, adjust_type: int = 1) -> pd.DataFrame:
    """adata 源(eastmoney)备用,通常被限流时不可用。"""
    adata = _adata()
    try:
        df = adata.stock.market.get_market(
            stock_code=code, start_date=start_date, end_date=end_date,
            k_type=k_type, adjust_type=adjust_type,
        )
    except Exception:
        return pd.DataFrame()

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def fetch_stock_history(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单只股票历史 K 线,优先 sina,失败回退 adata。"""
    df = fetch_stock_history_sina(code, start_date, end_date)
    if len(df) > 0:
        return df
    time.sleep(0.3)
    df = fetch_stock_history_adata(code, start_date, end_date)
    return df


def fetch_history_bulk(codes: list[str], start_date: str, end_date: str,
                       max_workers: int = 4, retry: int = 1,
                       progress_every: int = 50) -> pd.DataFrame:
    """批量拉取多只股票历史数据。"""
    results: list[pd.DataFrame] = []
    failed: list[str] = []

    def _one(code: str) -> pd.DataFrame:
        for attempt in range(retry + 1):
            df = fetch_stock_history(code, start_date, end_date)
            if len(df) > 0:
                return df
            time.sleep(0.5 * (attempt + 1))
        return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_one, c): c for c in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                df = fut.result()
                if len(df) > 0:
                    results.append(df)
                else:
                    failed.append(code)
            except Exception:
                failed.append(code)
            if i % progress_every == 0 or i == len(codes):
                print(f"  进度: {i}/{len(codes)}, 成功 {len(results)}, 失败 {len(failed)}", flush=True)

    if not results:
        return pd.DataFrame()
    big = pd.concat(results, ignore_index=True)
    print(f"  完成: 共 {len(big)} 行, {big['stock_code'].nunique()} 只股票, 失败 {len(failed)} 只", flush=True)
    return big


def fetch_st_stocks() -> pd.DataFrame:
    """当前 ST/*ST 股票列表(快照)。失败时缓存空结果避免反复重试。

    注意:这是当前时点的 ST 列表,不能直接用于回测(有前视偏差)。
    回测应使用 fetch_st_history 获取历史 ST 状态。
    """
    if cache_mod.exists("st_stocks_today"):
        meta = cache_mod.load_meta("st_stocks_today") or {}
        if meta.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return cache_mod.load("st_stocks_today")

    try:
        ak = _ak()
        df = ak.stock_zh_a_st_em()
    except Exception as e:
        df = pd.DataFrame()
        if not cache_mod.exists("st_stocks_today"):
            print(f"  警告: 拉 ST 列表失败: {e}; 缓存空结果避免重试")

    if df is None:
        df = pd.DataFrame()

    if len(df) > 0 and "代码" in df.columns:
        df = df.rename(columns={"代码": "stock_code", "名称": "name"})

    cache_mod.save("st_stocks_today", df,
                   meta={"source": "ak.stock_zh_a_st_em",
                         "date": datetime.now().strftime("%Y-%m-%d")})
    return df


ST_EXCLUDE_THRESHOLD = 5.5  # 超过此阈值必然不是 ST


def fetch_st_history(daily_df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """用日线涨跌幅推断历史 ST 状态。

    策略(单向判断,只排除必然不是 ST 的日子):
    1. 拉 ak.stock_zh_a_st_em() 获取当前 ST 列表
    2. 对当前 ST 的股票,从当前向前推:某日 |change_pct| > 5.5%,该日不是 ST
    3. 对非当前 ST 的股票:某日 |change_pct| > 5.5%,该日不是 ST(确认)
       某日 |change_pct| <= 5.5%,可能是 ST(但根据当前不是 ST,推断该日也不是 ST)
    4. 输出长表:stock_code, trade_date, is_st (0/1)

    局限:无法识别回测期内已摘帽的股票(当前不是 ST,但历史上是)。
    对这类股票,我们会把整个回测期标记为非 ST,有轻微前视(少剔除)。
    但比原方案(全期用当前快照)严格得多。
    """
    cache_name = f"st_history_{start_date}_{end_date}"
    if cache_mod.exists(cache_name):
        return cache_mod.load(cache_name)

    print("  推断历史 ST 状态...")
    st_today_df = fetch_st_stocks()
    st_today_codes = set(st_today_df["stock_code"].tolist()) if len(st_today_df) > 0 else set()

    if "change_pct" not in daily_df.columns:
        print("  警告: daily_df 无 change_pct,无法推断 ST 历史,用当前快照")
        rows = []
        for code in st_today_codes:
            for d in daily_df["trade_date"].unique():
                rows.append({"stock_code": code, "trade_date": d, "is_st": 1})
        result = pd.DataFrame(rows)
        cache_mod.save(cache_name, result)
        return result

    df = daily_df[["stock_code", "trade_date", "change_pct"]].copy()
    df["abs_chg"] = df["change_pct"].abs()
    df["is_st"] = 0.0

    # 对当前 ST 的股票:从最新日期向前推,直到 |change_pct| > 5.5% 的日子,之前不是 ST
    for code in st_today_codes:
        grp = df[df["stock_code"] == code].sort_values("trade_date", ascending=False)
        if len(grp) == 0:
            continue
        is_st_flag = True
        idx_map = grp.index.tolist()
        for idx in idx_map:
            abs_chg = df.at[idx, "abs_chg"]
            if pd.notna(abs_chg) and abs_chg > ST_EXCLUDE_THRESHOLD:
                is_st_flag = False
            if is_st_flag:
                df.at[idx, "is_st"] = 1.0

    result = df[["stock_code", "trade_date", "is_st"]].copy()
    cache_mod.save(cache_name, result, meta={"source": "inferred",
                                              "st_today_count": len(st_today_codes)})
    n_st_rows = int(result["is_st"].sum())
    n_st_codes = result.loc[result["is_st"] == 1, "stock_code"].nunique()
    print(f"  ST 历史推断完成: {n_st_codes} 只股票曾为 ST,共 {n_st_rows} 个股票日")
    return result


def fetch_lhb_detail(start_date: str, end_date: str) -> pd.DataFrame:
    """龙虎榜明细。"""
    name = f"lhb_{start_date}_{end_date}"
    if cache_mod.exists(name):
        return cache_mod.load(name)

    ak = _ak()
    try:
        df = ak.stock_lhb_detail_em(start_date=start_date.replace("-", ""),
                                    end_date=end_date.replace("-", ""))
    except Exception as e:
        print(f"  警告: 拉龙虎榜失败: {e}")
        return pd.DataFrame()

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()
    cache_mod.save(name, df, meta={"source": "ak.stock_lhb_detail_em"})
    return df


def fetch_index_daily(index_code: str = "000001", start_date: str = "2020-01-01",
                      end_date: str | None = None) -> pd.DataFrame:
    """指数日线(基准)。"""
    name = f"index_{index_code}_{start_date}_{end_date or 'now'}"
    if cache_mod.exists(name):
        return cache_mod.load(name)

    ak = _ak()
    symbol = f"sh{index_code}" if index_code.startswith("000") else f"sz{index_code}"
    try:
        df = ak.stock_zh_index_daily_em(symbol=symbol)
    except Exception as e:
        print(f"  警告: 拉指数失败: {e},尝试 sina")
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
        except Exception:
            return pd.DataFrame()

    df = df.copy()
    df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    if end_date:
        df = df[df["trade_date"] <= pd.Timestamp(end_date)]
    df = df[df["trade_date"] >= pd.Timestamp(start_date)]
    df = df.reset_index(drop=True)
    cache_mod.save(name, df, meta={"symbol": symbol})
    return df


def fetch_top_liquid_stocks(percentile: float = 0.8) -> list[str]:
    """从当前快照中选取流动性 top N 的主板股票(按 percentile 截断)。

    percentile=0.8 表示保留主板股票中流动性前 80%(~2433 股)。
    这里只用于构造 daily_main_board_liquid 数据采集层缓存。

    v0.4.0 生产选股路径默认使用 daily_main_board 全量主板,top18 策略
    显式传 universe_topk=300/500(比 80% 更严格),不使用本函数/本参数。

    注意:这是当前时点的快照,有幸存者偏差。回测时应用 universe.filter_universe_topk
    每月动态过滤,基于历史成交额排序。
    """
    if cache_mod.exists("top_liquid_stocks"):
        meta = cache_mod.load_meta("top_liquid_stocks") or {}
        if meta.get("date") == datetime.now().strftime("%Y-%m-%d") and meta.get("percentile") == percentile:
            return cache_mod.load("top_liquid_stocks")["stock_code"].tolist()

    print(f"  拉取全市场快照选取流动性 top {percentile*100:.0f}% 主板股票...")
    ak = _ak()
    try:
        df = ak.stock_zh_a_spot()
    except Exception as e:
        print(f"  错误: {e}")
        return []

    df = df.rename(columns={"代码": "stock_code", "成交额": "amount"})
    df["stock_code"] = df["stock_code"].str.replace("sh", "").str.replace("sz", "").str.replace("bj", "")
    df = df[df["stock_code"].apply(is_main_board)]
    df = df[df["amount"] > 0]
    df = df.sort_values("amount", ascending=False)
    keep_n = max(1, int(len(df) * percentile))
    df = df.head(keep_n)

    cache_mod.save("top_liquid_stocks", df[["stock_code", "amount"]],
                   meta={"date": datetime.now().strftime("%Y-%m-%d"), "percentile": percentile})
    return df["stock_code"].tolist()


def download_all(start_date: str, end_date: str, codes: list[str] | None = None,
                 max_workers: int = 4, percentile: float = 0.8,
                 cache_name: str | None = None) -> pd.DataFrame:
    """主入口:下载所需数据,缓存。

    若 codes 为 None,自动选取流动性 top percentile 主板股票(默认前 80%,~2433 股)。
    percentile=1.0 表示全量主板(~3042 股)。

    cache_name: 自定义缓存名(不含 .parquet 后缀)。None 时用默认
    daily_top{N}_{start}_{end} 命名。传 "daily_main_board" 可让其他模块
    (web/runner.py、quant_picker)直接通过 cm.load("daily_main_board") 读到。
    """
    print("=" * 60)
    print(f"开始下载数据 {start_date} ~ {end_date}")
    print("=" * 60)

    print("\n[1/5] 获取股票代码列表...")
    all_codes_df = fetch_all_stock_codes()
    print(f"  共 {len(all_codes_df)} 只股票")

    if codes is None:
        codes = fetch_top_liquid_stocks(percentile=percentile)
    print(f"  实际下载 {len(codes)} 只主板股票")

    print("\n[2/5] 拉取日线数据(sina 源,~7s/只)...")
    if cache_name is None:
        cache_name = f"daily_top{len(codes)}_{start_date}_{end_date}"
    if cache_mod.exists(cache_name):
        print(f"  命中缓存: {cache_name}")
        big = cache_mod.load(cache_name)
    else:
        big = fetch_history_bulk(codes, start_date, end_date, max_workers=max_workers)
        if len(big) > 0:
            cache_mod.save(cache_name, big, meta={"start": start_date, "end": end_date,
                                                   "codes": len(codes), "rows": len(big)})
            print(f"  缓存已保存: {cache_name}")

    print("\n[3/5] 获取当前 ST 列表 + 推断历史 ST 状态...")
    st_df = fetch_st_stocks()
    print(f"  当前 ST 股票 {len(st_df)} 只")
    if len(big) > 0:
        st_history = fetch_st_history(big, start_date, end_date)
        print(f"  ST 历史记录 {len(st_history)} 条")

    print("\n[4/5] 获取龙虎榜数据...")
    lhb_df = fetch_lhb_detail(start_date, end_date)
    print(f"  龙虎榜记录 {len(lhb_df)} 条")

    print("\n[5/5] 获取基准指数(上证指数)...")
    idx_df = fetch_index_daily("000001", start_date, end_date)
    print(f"  指数记录 {len(idx_df)} 条")

    print("\n" + "=" * 60)
    print("下载完成")
    print("=" * 60)
    return big


if __name__ == "__main__":
    df = download_all("2022-07-01", "2025-07-11", percentile=0.8)
    if len(df) > 0:
        print(f"\n主数据形状: {df.shape}")
        print(f"股票数: {df['stock_code'].nunique()}")
        print(f"日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
