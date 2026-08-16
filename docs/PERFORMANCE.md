# 量化层性能说明（v0.4.0）

本文记录 TradingAgents-quant 量化选股层的性能优化路径、基准数据与调优开关。
测试环境：Windows 11 / 32GB RAM / Python 3.13，`daily_main_board.parquet`
2,911,309 行 × 3,042 只主板股票，最新交易日 2026-07-17。

## 一、基准结果

| 模式 | 配置 | 耗时 | 说明 |
|------|------|------|------|
| v0.3.x 历史口径 | liquid 缓存、10 策略 | 约 733s | 历史记录，见 CHANGELOG |
| v0.4.0 旧路径 | `prune_universe=false`、4 workers | 358s | 全市场特征预热，18 策略 |
| v0.4.0 冷缓存 | universe-prune + 双 Pool + 按需特征列 | **69s** | universe 缓存与事件池缓存均未命中 |
| v0.4.0 热缓存 | 同上，缓存命中 | **37s** | 同一交易日重复选股 |

三组 v0.4.0 输出逐项一致：Top 10 代码、`all_records` 行数完全相同，18/18 策略 0 错误。

## 二、已落地的优化

### 1. Universe-prune（`quant_picker.py`）

主进程先为每个规则策略计算 top 300/500 universe 代码集合，worker 只为该集合
计算特征，跳过全市场 60–90s 特征预热。

- 开关：`DEFAULT_CONFIG["quant_universe_prune"] = true`
- 函数参数：`pick(..., prune_universe=False)` 可回退旧路径
- FC 因子策略自动排除，保持完整市场截面 rank

### 2. 双 Pool 执行

规则策略与 FC 因子策略分池执行：

- 规则池：worker 裁剪后释放全市场 DataFrame，降低峰值内存
- FC 池：worker 数不超过 3 个 FC 任务，只加载全市场数据

### 3. Universe 列表持久化

- 开关：`DEFAULT_CONFIG["quant_universe_cache"] = true`
- 缓存目录：`CACHE_DIR/universe_cache/`
- 失效条件：日线内容指纹、交易日、ST 历史/上市日期/交易日历文件指纹、价格与涨跌停过滤签名任一变化

### 4. 按需特征列

`build_features_vectorized(daily_df, columns=[...])` 按依赖图只计算所需列。
top18 规则策略在 `features/strategy_features.py` 中声明真实列需求。

top500 universe 单进程基准：

| 计算方式 | 耗时 | 输出列数 |
|----------|------|----------|
| 全量 50 列 | 5.8s | 50 |
| 8 列子集 | 1.0s | 20 |

## 三、调优建议

| 资金体量 | 价格上限建议 | 其他 |
|----------|--------------|------|
| ≤ 5 万 | 40–50 元 | 保持 top 500、主板 only |
| 5–10 万 | 60–70 元 | 保持 top 500 |
| > 10 万 | 80 元或更高 | 可按偏好扩展板块 |

当前默认：

- `quant_price_min = 3.0`
- `quant_price_max = 70.0`
- `quant_exclude_limit_up_down = true`

## 四、验证方式

```bash
python -m pytest tests/ -q --disable-warnings
python -m ruff check tradingagents web cli scripts tests
```

关键回归测试：

- `tests/test_quant_picker.py`：universe-prune 数据选择、universe 缓存复用
- `tests/test_feature_columns.py`：特征子集与全量逐列一致、依赖展开
- `tests/test_event_pool_cache.py`：事件池数据指纹失效
- `tests/test_cache_update.py`：并发更新不丢行、不重复
- `tests/test_universe_limit.py`：涨跌停过滤、价格边界
