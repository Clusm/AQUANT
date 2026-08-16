# TradingAgents-quant

## 项目概述
基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)（65K Stars）的 A 股深度特化 fork,在 LLM 多 Agent 投研流水线前插入 18 策略量化前置筛选层(top18 终态库,S=5/A=11/B=2,来源 stock_selector `strategy_library_active_top18.py`,OOS 口径 2025-01-01~2026-07-14),形成"量化广度扫描 + LLM 深度分析"双层架构。7 个 Analyst 角色通过 Bull/Bear 辩论 + 三方风险辩论生成投资报告,最终由 Conflict Resolver 节点输出 🟢强买/🟡关注/🟠冲突/🔴弃 4 档推荐。

- **仓库**: https://github.com/Clusm/AQUANT
- **协议**: Apache 2.0
- **Python**: >=3.10
- **当前版本**: 0.4.0
- **包名**: tradingagents-quant (editable install: `pip install -e .`)

## 架构

### 数据层（v0.2.5 全部直连 HTTP，零第三方数据库依赖）
| 来源 | 协议 | 数据 |
|------|------|------|
| mootdx | TCP 7709 | OHLCV K线、财务快照、F10 文本 |
| 腾讯财经 | HTTP (qt.gtimg.cn) | PE/PB/市值/换手率 |
| 东方财富 datacenter | HTTP (datacenter-web) | 龙虎榜、限售解禁、板块行情 |
| 东方财富 push2/push2his | HTTP (push2.eastmoney) | 实时行情、个股信息、板块列表、资金流(分钟+日级) |
| 东方财富 np-weblist | HTTP | 滚动新闻 |
| 新浪财经 | HTTP (money.finance.sina) | K线历史、财报三表 |
| 同花顺 10jqka | HTTP | EPS 一致预期、热股题材 |
| 财联社 cls.cn | HTTP | 全球财经快讯 |
| 百度股市通 | HTTP (gushitong.baidu) | 概念板块归属（资金流已迁移至东财push2） |

### Agent 角色（7 个）
原版 4 个（市场/情绪/新闻/基本面）+ A 股特化 3 个（政策分析师/游资追踪/解禁监控）

### 关键路径
- `tradingagents/dataflows/a_stock.py` — A 股数据 vendor，所有数据获取入口
- `tradingagents/dataflows/utils.py` — `safe_ticker_component` 路径安全校验 + 中文 ticker 自动解析
- `tradingagents/agents/` — 7 个 Analyst + Bull/Bear 辩论逻辑
- `web/` — Streamlit Web UI（7-tab：量化选股 / AI 分析 / 买入计划 / 持仓跟踪 / 交易记录 / 推荐 / 历史）
- `cli/` — CLI 入口

### 中文股票名解析链路
用户/LLM 输入 → `safe_ticker_component` 检测中文 → `resolve_ticker()` → `_build_name_code_map()`（mootdx 全市场映射，缓存）→ 返回 6 位代码

## 已知问题与注意事项

### 依赖冲突（v0.2.6 已缓解）
mootdx 锁死 httpx==0.25.2，与 langchain-google-genai 的 httpx>=0.28.1 冲突。v0.2.6 将 google-genai 移至可选依赖 `[google]`，`pip install -e .` 不再冲突。需要 Google 模型时 `pip install -e ".[google]"`。

### akshare 依赖现状（v0.4.0 已纳入主依赖）
v0.2.5 起日线/增量/指数数据经 `sina_fetcher.py` 直连 sina HTTP API，不再走 akshare。但 akshare **仍是运行时依赖**（v0.4.0 起已声明在 pyproject），用于：
- 交易日历：`trading_calendar.get_calendar()`（首次从 akshare 拉取并本地缓存）
- 全市场代码/上市日期：`universe.get_list_dates()` → `fetcher.fetch_all_stock_codes()`（优先 adata，回退 `ak.stock_zh_a_spot`）
- ST 历史推断：`fetcher.fetch_st_history()` → `fetcher.fetch_st_stocks()`（`ak.stock_zh_a_st_em`）
- 全量建库：`fetcher.download_all` / `fetch_history_bulk`（仅初始化用，Web 报错提示会引导手动跑）

`adata` 仍是可选增强（`pip install -e ".[quant-data]"`），未安装时全市场代码
回退 akshare。

增量更新单一实现为 `tradingagents.quant.data_update.increment_data`（Web 后台、runner、`scripts/incremental_update.py` 共用），底层复用 `sina_fetcher.fetch_bulk_incremental_sina`。落后股票回补单一实现为 `data_update.backfill_stale`（分块 + 探测冷却自限流，`scripts/incremental_update.py` 与 `scripts/backfill_main_board.py` 共用）。v0.4.0 起 `cm.update()` 全流程持有同表文件锁（进程内 + 跨进程）并原子写，read-old/merge/write 不再互相覆盖，且按 `(stock_code, trade_date)` 去重，重跑/并发不会产生重复行。跨机器/多用户仍建议通过任务调度串行这两个脚本，但同机并发已安全。

### 量化层性能（v0.4.0 universe-prune + 双 Pool + 按需特征列）
`pick()` 默认开启 `quant_universe_prune=true` 与 `quant_universe_cache=true`：
1. 主进程先算/复用各任务 top 300/500 universe 代码列表；
2. 规则策略 worker 只保留裁剪后日线并释放全市场 DataFrame；
3. FC 因子策略（全市场截面 rank）在单独全市场 Pool 运行；
4. top18 规则策略通过 `strategy_features.py` 声明按需特征列，`build_features_vectorized(columns=...)` 只计算依赖闭包内的列。

实测 3042 股缓存、8 workers：冷缓存 **69s**、热缓存 **37s**、18/18 策略 0 错误；旧路径 4 workers 358s。三组 Top 10 与 `all_records` 逐项一致。

### 买入计划与持仓跟踪（v0.4.0）
`web/position_store.py` 持久化到 `~/.tradingagents/positions/plans.json`，状态机为 `planned → filled → closed` / `planned → abandoned`。计划详情展示每只命中策略的出场类型（信号出场 / 固定持仓 / 固定持仓 + 信号出场保护）、ATR 止损/移动止盈/保本 kill 操作建议、OOS 优化记录（见 `optimization_records.py`）与次日涨跌停参考价；V1 跟踪规则固定为 -5% 止损、+8% 止盈、建议到期、T+1 买入当日禁卖，ATR 真实跟踪留待 V2。Top N 固定 20，Sidebar 不暴露量化参数与模型选择，API Key 持久化在 `~/.tradingagents/web_config.json`。

### 百度 PAE 资金流接口已下线（v0.2.7 已修复）
`fundsortlist` 和 `fundflow` 两个接口返回空（2026-05-19 确认）。v0.2.7 已替换为东财 push2 资金流 API。同时修复了 `RPT_ORGANIZATION_BUSSINESS`（改用席位筛选机构）和东财全球资讯 `req_trace` 参数。

### vendor 工具结果缓存（v0.4.0）
`route_to_vendor()` 对成功结果做 180s 进程级缓存，按 `(method, args)` 去重：批量分析时 7 个 Analyst 常重复请求同一 ticker/date，现在同参数只打一次，减少数据源封禁和 pipeline 耗时。

### 东财接口防封限流（v0.2.11 新增，移植自 a-stock-data v3.2）
`a_stock.py` 里所有指向 `eastmoney.com` 的请求（push2 / push2his / datacenter-web / search-api / np-weblist 共 7 个调用点）统一走节流入口 `_em_get()`：模块级时间戳串行限流（默认间隔 `EM_MIN_INTERVAL=1.0s`，可用同名环境变量覆盖）+ 0.1~0.5s 随机抖动 + 复用 `requests.Session`（Keep-Alive）+ 默认 UA。多 Agent 跑批量分析不再触发东财临时封 IP。**仅东财限流**——mootdx(TCP) / 腾讯 / 新浪 / 同花顺 / 财联社 / 百度 等非东财源不受影响。批量场景可设 `EM_MIN_INTERVAL=1.5~2` 进一步降速。新增东财端点时务必走 `_em_get` 而非裸 `requests.get`。

### 模型兼容性
deepseek-v4-flash 等模型在 tool call 时可能返回中文股票名而非 6 位代码。`safe_ticker_component` 已加兜底自动转码，但不同模型表现仍有差异。

### 待处理 PR
- PR #18（hejingchi）：start_date 功能 + 主题切换 + Windows 字体。不建议直接 merge（与 v0.2.6 冲突），start_date 功能值得后续自行实现。

## Issue 归档
所有 GitHub Issue 的详细记录在 `issues/` 文件夹中，包含问题描述、根因分析、修复方案和当前状态。

## 开发规范
- 改动前先跑 `python -m pytest tests/ -v` 确保不破坏现有测试
- `safe_ticker_component` 是安全边界，任何绕过路径校验的改动必须慎重评估
- 数据层新增接口遵循 `tradingagents/dataflows/interface.py` 的 vendor 路由模式
- Web UI 改动在 `web/` 目录，用 `streamlit run web/launch.py` 本地测试

## 相关项目
- 上游 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) — 原版框架
