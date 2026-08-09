# TradingAgents-quant 整合改动清单

> 日期: 2026-07-18(初稿)/ 2026-07-19(完成)
> 基座: TradingAgents-astock v0.2.18(fork 自 simonlin1212/TradingAgents-astock)
> 目标: 在现有 LLM 多 Agent 投研流水线前插入"量化策略前置筛选层",形成"量化广度扫描 + LLM 深度分析"双层架构

## 0. 实施完成状态(2026-07-19)

| 类别 | 计划文件数 | 实际完成 | 状态 |
|------|-----------|---------|------|
| 类 A 量化层迁移 | 23 | 23 | ✅ 完成 |
| 类 B LangGraph 集成 | 24 | 24 | ✅ 完成 |
| 类 C Web UI 4-tab | 10 | 10 | ✅ 完成 |
| 类 D CLI + 自动化 | 3 | 3 | ✅ 完成 |
| 类 E 配置依赖 | 3 | 3 | ✅ 完成 |
| 性能优化 | - | - | ✅ 完成(30 -> 12 min) |
| 端到端冒烟测试 | - | - | ✅ 完成 |
| 真 LLM 端到端测试 | - | - | ⏳ 待 API quota |

**版本**: 0.2.18 -> 0.3.0(详见 CHANGELOG.md)
**包名**: tradingagents-astock -> tradingagents-quant
**Web UI 品牌**: TradingAgents-Astock -> Aquant 投研工具

后续优化方向见本文档第 5 节"风险点与决策点"和 CHANGELOG 0.3.0 的 Fixed/Performance 段落。

## 1. 关键发现汇总

### 1.1 TradingAgents 核心架构
- **LangGraph 拓扑**: `START -> 7 Analyst 顺序串联 -> Quality Gate -> Bull/Bear 循环辩论 -> Research Manager -> Trader -> 3 方风险辩论 -> Portfolio Manager -> END`
- **状态对象**: `AgentState(MessagesState)`,字段整体覆盖式 reducer,新增字段只需声明类型
- **Analyst prompt**: 用 `ChatPromptTemplate.from_messages + prompt.partial(...)`,已有 `build_instrument_context(ticker)` 注入额外上下文的机制
- **LLM 工厂**: `create_llm_client(provider, model, base_url)`,支持 9 个 provider,可实例化第 2 个 client 做对比
- **节点风格**: 闭包工厂 `create_xxx(llm) -> node_fn(state) -> dict`
- **数据层**: 21 个单标的接口,无全市场批量日线 API(需新增)
- **入口**: `TradingAgentsGraph.propagate(company_name, trade_date) -> (final_state, signal)`

### 1.2 TradingAgents UI 现状
- **Streamlit 模式**: sidebar + main area 状态机(无 tabs),5 种视图切换(历史/运行中/完成/出错/欢迎)
- **后台运行**: `threading.Thread + ProgressTracker dataclass`,流式消费 `graph.stream()`
- **进度阶段**: `PIPELINE_STAGES` 12 个阶段(7 分析师 + 质量门 + 辩论 + 交易 + 风控 + PM),不要插队改编号
- **报告展示**: `report_viewer.py` 用 `st.expander` 渲染 7 个 Analyst 报告
- **历史**: 扫 `~/.tradingagents/logs/<ticker>/<date>/full_states_log_*.json`
- **导出**: `_collect_sections` PDF/Markdown 共用入口,7 段报告
- **CLI**: Typer 单命令 `analyze`(8 步问卷式)
- **入口点**: `tradingagents`(CLI) + `tradingagents-web`(Streamlit)

### 1.3 stock_pick_live 量化层
- **42 策略**: 在 `strategy_library_final.py` 注册(S=11/A=18/B=10/C=3),~290 个文件但只迁 42 个
- **基类**: `BaseStrategy(ABC)`,抽象方法 `generate_signals(daily_df, current_date, portfolio, top_k)`
- **sina_fetcher**: 零依赖(requests+pandas),ThreadPoolExecutor 并发
- **主循环**: multiprocessing.Pool(spawn) + imap_unordered,worker 预热三档缓存(30/90/120)
- **加权分**: `weighted_score = sum(strategy_composite_score)`,无 tier 权重
- **入场建议**: 基于 `new_performance.win_rate/total_return`,文本拼接
- **universe 预过滤**: 流动性前 70%(20 日均成交额),~2118 只
- **市场过滤**: MA(15,35) + MA(90) 双均线 + 长趋势
- **可独立运行**: 剥离 rich UI 后,`run_strategies_live` 核心可独立
- **依赖**: akshare 重(可弃)、rich/colorama 仅 UI(不迁)、xgboost/sklearn ML 专用(42 策略未用,不迁)

## 2. 整体架构

### 2.1 改造后的数据流

```
[用户在 Web UI 点"今日选股"]
    ↓
tradingagents/quant/quant_picker.py
    跑 42 策略(multiprocessing.Pool,~3 分钟)
    产出: Top 20 候选 + 命中策略 + 加权分 + 入场建议
    ↓
[用户在"📊 量化选股" tab 勾选要深度分析的股票]
    Top N 默认"全部",可选 5/10/20/50
    ↓
[用户点"AI 深度分析"]
    ↓
对每只选中股票并行/串行调 LangGraph:
    ┌─ Quant Picker 节点(确定性计算,把量化上下文写入 state)
    ├─ 7 Analyst(每个 prompt 注入 quant_pick_context)
    │   ├─ 数据来源: mootdx + 东财 + 同花顺(已有)
    │   └─ 新增数据来源: quant_pick_context(命中策略/tier/win_rate/加权分)
    ├─ Bull vs Bear 辩论
    ├─ Research Manager(deep LLM)
    ├─ Trader(A 股约束)
    ├─ 3 方风险辩论
    └─ Portfolio Manager(deep LLM,输出 Buy/Hold/Sell + 仓位)
    ↓
[可选] Compare LLM Runner 节点
    用第 2 个 LLM 重新跑同一只股票
    产出: secondary_decision
    ↓
Conflict Resolver 节点
    合并: 量化分 + 主 LLM 决策 + 副 LLM 决策 + 命中策略质量
    标签: 🟢 强买 / 🟡 关注 / 🟠 冲突 / 🔴 弃
    ↓
[用户在"🎯 综合推荐" tab 看]
    Top 1-3 推荐 + 完整推理 + 冲突标注
    [📥 导出 Markdown] [📥 导出 PDF]
```

### 2.2 双数据源共存策略

| 层 | 数据源 | 用途 |
|---|---|---|
| 量化层 | sina_fetcher(stock_pick_live 已有) | 42 策略批量跑 ~2118 股,需快速拿全市场日线 |
| 投研层 | mootdx + 东财 + 同花顺(TradingAgents 已有) | 7 Analyst 单股深度分析,需龙虎榜/解禁/财报 |

两个数据源各取所长,sina_fetcher 拿全市场日线快,mootdx 拿单股深度数据全。

## 3. 完整改动清单

按 5 大类组织,共 ~50 个文件。

### 类 A: 量化层迁移(从 stock_pick_live 拷贝)

| # | 文件 | 处置 | 改造要点 | 依赖 |
|---|---|---|---|---|
| A1 | `tradingagents/quant/__init__.py` | 新增 | 包入口,导出 `pick` API | - |
| A2 | `tradingagents/quant/config.py` | 改造 | 从 stock_pick_live/config.py 裁剪:删 `BACKTEST_*`/`ML_*`/`RULE_WEIGHTS`;保留 `UNIVERSE_*`/`LIMIT_*`/`MAX_HOLDING_DAYS`/`TOP_K`/`CACHE_DIR`(改为相对 `tradingagents/quant/`) | 无 |
| A3 | `tradingagents/quant/sina_fetcher.py` | 直接拷贝 | 零依赖,改 import 路径即可 | requests |
| A4 | `tradingagents/quant/data/__init__.py` | 直接拷贝 | 空 | - |
| A5 | `tradingagents/quant/data/cache.py` | 改造 | `from config import CACHE_DIR` 改相对 import | A2 |
| A6 | `tradingagents/quant/data/universe.py` | 改造 | `from data.fetcher import ...` 改为可选 lazy import(实时选股不需要);`get_st_codes_on_date` 实时场景降级到当前快照 | A2, A5 |
| A7 | `tradingagents/quant/data/st_filter.py` | 改造 | `cache_mod.load("st_history_*")` 硬编码文件名参数化 | A5 |
| A8 | `tradingagents/quant/strategy/__init__.py` | 直接拷贝 | 空 | - |
| A9 | `tradingagents/quant/strategy/base.py` | 改造 | `from backtest.engine import Signal` 改相对 import | A18 |
| A10 | `tradingagents/quant/strategy/market_filter.py` | 直接拷贝 | `from features.indicators import ma` 保留 | A14 |
| A11 | `tradingagents/quant/strategy/strategy_library_final.py` | 改造 | 2268 行,42 策略配置;`module` 字段批量替换 `strategy.xxx` -> `tradingagents.quant.strategy.xxx`(46 处) | 42 个策略模块 |
| A12 | `tradingagents/quant/strategy/<42 个策略>.py` | 改造 | 改 `from backtest.engine import Signal`、`from data.universe import`、`from features.pipeline import`、`from strategy.base import` 为相对 import | A9, A14, A6, A18 |
| A13 | `tradingagents/quant/features/__init__.py` | 直接拷贝 | 空 | - |
| A14 | `tradingagents/quant/features/indicators.py` | 直接拷贝 | 纯 pandas/numpy | - |
| A15 | `tradingagents/quant/features/factors.py` | 直接拷贝 | 纯 pandas/numpy | - |
| A16 | `tradingagents/quant/features/pipeline.py` | 直接拷贝 | `build_features_vectorized` 核心,带 `_BFV_CACHE` | A14, A15 |
| A17 | `tradingagents/quant/backtest/__init__.py` | 直接拷贝 | 空 | - |
| A18 | `tradingagents/quant/backtest/engine.py` | 改造 | **仅提取 `Signal` 类**(line 30-38);BacktestEngine 完整回测引擎不迁 | - |
| A19 | `tradingagents/quant/backtest/portfolio.py` | 改造 | 仅保留 `__init__(capital, max_positions, calendar)` + 空 positions,裁掉 T+1/手续费 | A2 |
| A20 | `tradingagents/quant/utils/__init__.py` | 直接拷贝 | 空 | - |
| A21 | `tradingagents/quant/utils/trading_calendar.py` | 改造 | `_fetch_calendar()` 用 akshare,改为 lazy/可注入 | A2 |
| A22 | `tradingagents/quant/quant_picker.py` | 新增 | 从 `daily_pick_live.py` 提炼 `run_strategies_live` + `_worker_init` + `_worker_run` + `get_tier_of` + `_compute_entry_advice`;从 `live_ui.py` 提炼 `_aggregate`;暴露 `pick(daily_df, today, top_k, n_workers) -> list[dict]` API | A2-A21 |
| A23 | `tradingagents/quant/data_update.py` | 改造 | 从 `data_update_live.py` 去 rich Progress 包装,改名 `increment_data(daily_df, idx_df, codes, max_workers) -> DataFrame` | A3, A5 |

### 类 B: 核心代码修改(LangGraph 集成)

| # | 文件 | 操作 | 改动要点 | 风险 |
|---|---|---|---|---|
| B1 | `tradingagents/agents/utils/agent_states.py` | 修改 | 加字段:`quant_pick_context: str`、`quant_compare_decision: str`、`final_ranked_decision: str`(`:46-79`) | 低 |
| B2 | `tradingagents/graph/propagation.py` | 修改 | `create_initial_state` (`:18-58`) 初始化新字段为空串 | 低 |
| B3 | `tradingagents/default_config.py` | 修改 | 新增配置组:`quant_layer_enabled`、`quant_strategies_count`、`quant_top_n_mode`("all"/"selectable")、`quant_top_n_default`、`quant_compare_llm_enabled`、`quant_compare_llm_provider`、`quant_compare_think_llm`(`:51` 前) | 低 |
| B4 | `tradingagents/agents/quant_picker_node.py` | 新增 | LangGraph 节点封装:调 `quant_picker.pick()`,把 Top N 结果格式化为 `quant_pick_context` 字符串,写入 state | 中 |
| B5 | `tradingagents/agents/compare_llm_runner.py` | 新增 | 双 LLM 对比节点:用第 2 个 LLM 重跑同一只股票(精简版:只跑 Research Manager + Portfolio Manager,跳过 7 Analysts 节省成本) | 中 |
| B6 | `tradingagents/agents/conflict_resolver.py` | 新增 | 冲突检测+排序节点:合并量化分 + 主 LLM 决策 + 副 LLM 决策,输出 `final_ranked_decision`(带 🟢🟡🟠🔴 标签) | 中 |
| B7 | `tradingagents/agents/__init__.py` | 修改 | 导出 3 个新工厂(`:1-47`) | 低 |
| B8 | `tradingagents/graph/setup.py` | 修改 | `setup_graph` (`:29`) 加 `Quant Picker` 节点 + `START->Quant Picker->first Analyst`;Portfolio Manager 后加 `Compare LLM Runner`(conditional) + `Conflict Resolver`(`:141,210`) | **高** |
| B9 | `tradingagents/graph/trading_graph.py` | 修改 | `__init__` (`:61-139`) 实例化第 2 个 LLM client;`_create_tool_nodes` (`:163-225`) 加 quant 数据 tool;`propagate` (`:301`) 加 batch 入口 `propagate_batch(tickers, trade_date)` | **高** |
| B10 | `tradingagents/agents/utils/agent_utils.py` | 修改 | 加 `build_quant_context(state) -> str` 函数(`:47` 附近),参考 `build_instrument_context` | 低 |
| B11 | `tradingagents/agents/analysts/market_analyst.py` | 修改 | `system_message` 末尾拼 `{quant_pick_context}`;`prompt.partial` 注入 | 中 |
| B12 | `tradingagents/agents/analysts/social_media_analyst.py` | 修改 | 同 B11 | 中 |
| B13 | `tradingagents/agents/analysts/news_analyst.py` | 修改 | 同 B11 | 中 |
| B14 | `tradingagents/agents/analysts/fundamentals_analyst.py` | 修改 | 同 B11 | 中 |
| B15 | `tradingagents/agents/analysts/policy_analyst.py` | 修改 | 同 B11 | 中 |
| B16 | `tradingagents/agents/analysts/hot_money_tracker.py` | 修改 | 同 B11 | 中 |
| B17 | `tradingagents/agents/analysts/lockup_watcher.py` | 修改 | 同 B11 | 中 |
| B18 | `tradingagents/agents/managers/portfolio_manager.py` | 修改 | prompt (`:42-74`) 注入 `quant_pick_context`;返回字段加 `final_ranked_decision`(`:97-100`) | 中 |
| B19 | `tradingagents/dataflows/a_stock.py` | 修改 | 新增 `get_market_daily_ohlcv(date, universe)` 全市场批量日线接口(`:548` 附近);复用 `_get_mootdx_client` + `_build_name_code_map` | 中 |
| B20 | `tradingagents/dataflows/interface.py` | 修改 | `TOOLS_CATEGORIES` (`:51-94`) + `VENDOR_METHODS` (`:103-178`) 注册新方法 | 低 |
| B21 | `tradingagents/agents/schemas.py` | 修改 | 加 `ConflictResolvedDecision` Pydantic schema(`:60` 附近) | 低 |
| B22 | `tradingagents/graph/conditional_logic.py` | 修改(小) | 加 `should_run_compare_llm` (`:81-91` 附近),根据 config 决定是否跑副 LLM | 低 |
| B23 | `tradingagents/graph/reflection.py` | 修改(小) | reflection prompt 可选注入 quant 命中策略,评估量化层质量(`:14-29`) | 低 |
| B24 | `tradingagents/graph/trading_graph.py` `_log_state` | 修改 | 落盘新字段 `quant_pick_context` / `final_ranked_decision`(`:420-463`) | 低 |

### 类 C: UI 修改(Web)

| # | 文件 | 操作 | 改动要点 | 风险 |
|---|---|---|---|---|
| C1 | `web/app.py` | 修改 | 主区从状态机改为 `st.tabs(["📊 量化选股", "🤖 AI 深度分析", "🎯 综合推荐", "📜 历史"])`;`_build_config` (`:159`) 加 quant/secondary/compare 字段;tab 间用 session_state 传 picks | **高** |
| C2 | `web/runner.py` | 修改 | 新增 `run_quant_pick_in_thread(tickers, config, tracker)`、`run_compare_in_thread`(双 LLM);复用 Tracker 模式 | 中 |
| C3 | `web/progress.py` | 修改 | 新增 `QuantProgressTracker` 子类(独立阶段常量);**不动 `PIPELINE_STAGES`** 避免 stage_id 重编号 | 中 |
| C4 | `web/components/sidebar.py` | 修改 | `_render_llm_config` (`:133-191`) 加:副 LLM 选择、Top N 模式("全部"/"5"/"10"/"20"/"50")、是否启用对比 checkbox | 低 |
| C5 | `web/components/progress_panel.py` | 修改 | 新增 `render_quant_progress` 函数;原 `render_progress` 不动 | 低 |
| C6 | `web/components/report_viewer.py` | 修改 | `render_report` (`:120` 之前) 注入量化上下文卡片(`st.info`);新增 `render_compare_report(state_a, state_b, ticker)` 双 LLM 并排展示 | 中 |
| C7 | `web/components/quant_pick.py` | 新增 | Top 20 表格 + 勾选 + 入场建议列;`render_quant_picker(picks_df) -> list[selected_tickers]` | 低 |
| C8 | `web/components/recommendation.py` | 新增 | 综合推荐 tab 渲染:强买/关注/冲突/弃四档;冲突行双信号展示 | 低 |
| C9 | `web/history.py` | 修改 | 新增 `get_quant_history`、`save_quant_pick`、`save_recommendation`;不动 `get_history` | 低 |
| C10 | `web/pdf_export.py` | 修改 | `_collect_sections` (`:553`) 头部插量化上下文段;新增 `generate_compare_pdf/markdown`;`_REPORT_SECTIONS` 不动 | 中 |

### 类 D: CLI 修改

| # | 文件 | 操作 | 改动要点 | 风险 |
|---|---|---|---|---|
| D1 | `cli/main.py` | 修改 | 末尾注册新子命令 `quant-pick`(从新模块 import);原 `analyze` 不动 | 中 |
| D2 | `cli/quant_pick.py` | 新增 | `quant-pick` 子命令实现:参数化跑 42 策略,输出 JSON/CSV | 低 |
| D3 | `scripts/daily_pipeline.py` | 新增 | 每日 cron 入口:选股 -> Top N -> 逐只 LLM -> 综合推荐 -> 落盘 | 低 |

### 类 E: 配置与依赖

| # | 文件 | 操作 | 改动要点 | 风险 |
|---|---|---|---|---|
| E1 | `pyproject.toml` | 修改 | 可选新增 `tradingagents-quant = "cli.quant_pick:main"` 入口;dependencies 加 `pandas>=2.0`、`numpy>=1.24`、`pyarrow>=14`、`requests` | 低 |
| E2 | `.env.example` | 新增 | LLM key 模板(`MINIMAX_API_KEY`、`DEEPSEEK_API_KEY` 等) | 低 |
| E3 | `requirements-quant.txt` | 新增(可选) | 量化层独立依赖,便于单独安装 | 低 |

## 4. 分阶段实施计划

| 阶段 | 周期 | 内容 | 产出 |
|---|---|---|---|
| **P0: 环境搭建** | 0.5 天 | 验证 TradingAgents-quant 可跑,配 DeepSeek API key | 现有功能跑通 |
| **P1: 量化层迁移** | 3-4 天 | 类 A 全部 23 个文件迁移 + 改 import + 单元测试 | `python -c "from tradingagents.quant import pick"` 可用 |
| **P2: 核心 LangGraph 集成** | 4-5 天 | 类 B 全部 24 个文件:加 Quant Picker 节点 + 改 7 Analyst prompt + 加 Compare LLM + Conflict Resolver | `TradingAgentsGraph.propagate(ticker, date)` 注入量化上下文跑通 |
| **P3: UI 改造** | 4-5 天 | 类 C 全部 10 个文件:tabs 重构 + 量化选股 tab + 综合推荐 tab + 多 LLM 对比展示 | Web UI 三段式工作流可用 |
| **P4: CLI + 自动化** | 2-3 天 | 类 D 全部 3 个文件 + 类 E 配置 | `tradingagents quant-pick` 子命令可用 + 每日自动 pipeline |
| **P5: 测试与抛光** | 2-3 天 | 端到端测试 + 历史回放 + 文档 | 完整可交付 |

**总计: ~16-20 天(2-3 周)MVP**

## 5. 风险点与决策点

### 5.1 高风险点

1. **setup.py 拓扑改造(B8)**: LangGraph 一旦编译不可动态加节点,需在 `setup_graph` 一次性建好;conditional edges 的 reducer 字段必须预先在 state 声明
   - **缓解**: 先写 unit test 验证 state 字段声明,再改 setup.py

2. **app.py 主区改 tabs(C1)**: 影响原"单标的分析"流程,需保证原有功能在新 tab 内仍可用
   - **缓解**: 保留原"AI 深度分析"tab 的状态机逻辑,只在外层包 tabs

3. **全市场批量数据性能(B19)**: mootdx TCP 单连接串行拉 ~2118 股日线可能 >5 分钟
   - **缓解**: 量化层继续用 sina_fetcher(已优化并发),mootdx 只用于单股深度分析

4. **双 LLM provider 不一致(B5)**: 第 2 个 LLM 若不支持 structured output,Compare LLM Runner 需走 freetext fallback
   - **缓解**: 复用 `invoke_structured_or_freetext`(portfolio_manager.py:76-82)

### 5.2 中风险点

1. **量化层确定性 vs LLM 节点风格冲突(B4)**: `create_xxx(llm)` 工厂假设所有节点都用 LLM,quant picker 是纯计算
   - **缓解**: 改工厂签名为 `create_quant_picker(config)` 或 `create_quant_picker(quant_engine)`,破坏隐式约定但清晰

2. **strategy_library_final.py 的 module 字段批量替换(A11)**: 46 处 `strategy.xxx` 需改为 `tradingagents.quant.strategy.xxx`
   - **缓解**: 一次性 sed 脚本 + 单元测试验证 42 策略都能 import

3. **PIPELINE_STAGES 不动(C3)**: 不插队改编号,新增 `QuantProgressTracker` 独立
   - **缓解**: UI 上单独画一行量化进度条

### 5.3 待你决策的点

1. **Top N 默认值**: 你说"默认全部或者可以选择数量的模式"。我理解为:
   - 默认 = "全部"(对 Top 20 都跑 LLM 分析)
   - 可选 = 5 / 10 / 20 / 50 / 全部
   - 对吗?

2. **多 LLM 对比模式**: 默认关闭(opt-in)?
   - 启用时:同一只股票用主+副两个 LLM 各跑一遍,成本翻倍
   - 关闭时:只用主 LLM
   - 我建议默认关闭,用户在 sidebar 勾选启用

3. **Compare LLM Runner 是否跑完整 7 Analysts**:
   - 方案 A(完整): 副 LLM 跑完整流水线(7 Analysts + 辩论 + PM),成本 1x
   - 方案 B(精简,推荐): 副 LLM 只跑 Research Manager + Portfolio Manager,跳过 7 Analysts,成本 ~0.3x
   - 我建议方案 B,因为 7 Analysts 主要是数据收集(两个 LLM 看到的数据一样),价值在最终决策层

4. **Compare LLM 启用时的并发**:
   - 串行:主 LLM 跑完 -> 副 LLM 跑,总时延 2x
   - 并行:主+副同时跑,总时延 1x 但 LLM API 可能限流
   - 我建议串行(避免限流风险)

5. **stock_pick_live 后续**:
   - 整合完成后,stock_pick_live 还保留作为"纯量化备用"
   - 策略库同步:stock_selector R&D 产出新入库策略 -> 手动复制到 TradingAgents-quant/strategy/
   - 这个流程要不要写个 sync 脚本?(简单 cp 命令,但避免漏文件)

6. **冲突展示方式**:
   - 量化 Buy + LLM Sell = 🟠 冲突
   - 展示时:同时显示两个决策 + 推理链,用户自己判断
   - 不做"谁优先"的硬规则
   - 对吗?

## 6. 不动的文件(明确)

以下文件**不修改**,保留原样:

- `tradingagents/agents/researchers/`(Bull/Bear 辩论逻辑)
- `tradingagents/agents/risk_mgmt/`(3 方风险辩论)
- `tradingagents/agents/trader/`(Trader)
- `tradingagents/agents/managers/research_manager.py`(Research Manager)
- `tradingagents/llm_clients/factory.py`(LLM 工厂,已支持多 provider)
- `tradingagents/dataflows/a_stock.py` 除新增 `get_market_daily_ohlcv` 外不动
- `web/launch.py`(Streamlit 启动器)
- `web/components/progress_panel.py` 原 `render_progress` 不动
- `web/pdf_export.py` 原 `_REPORT_SECTIONS` 不动
- `cli/main.py` 原 `analyze` 命令不动
- `pyproject.toml` 的 `[project.scripts]` 原两条不动(仅可选新增第三条)

## 7. 最终决策(2026-07-18 确认)

| # | 决策点 | 最终选择 | 影响 |
|---|---|---|---|
| 1 | Top N 默认值 | **默认 20,可选 5/10/20** | C4 sidebar 下拉框选项;B3 quant_top_n_default=20 |
| 2 | 多 LLM 对比功能 | **暂不实现(P1-P5 范围外)** | B5/B22/C2/C4/C6/C10 中 compare_llm 相关项 defer,字段在 B3 保留但默认 False |
| 3 | Compare LLM Runner 精简版 | moot(因 2 暂不做) | - |
| 4 | Compare LLM 串行/并行 | moot(因 2 暂不做) | - |
| 5 | stock_pick_live 同步脚本 | **写一个简单 sync 脚本** | 放 `scripts/sync_strategies.py`,P4 阶段做 |

**多 LLM 对比 defer 清单**(后续如要启用,从这些点切入):
- B5 `compare_llm_runner.py`(不创建)
- B6 `conflict_resolver.py` 保留但简化:只合并量化分 + 主 LLM 决策,无 secondary_decision 输入
- B22 `should_run_compare_llm` 不加
- C2 `run_compare_in_thread` 不加
- C4 副 LLM 选择/对比 checkbox 不加
- C6 `render_compare_report` 不加
- C10 `generate_compare_pdf/markdown` 不加
- B1/B2 `quant_compare_decision` 字段不加(只保留 `quant_pick_context` + `final_ranked_decision`)
- B3 `quant_compare_llm_enabled` 字段保留默认 False(为未来预留接口)

**冲突展示规则**(简化版,无副 LLM):
- 量化 Buy + LLM Buy = 🟢 强买
- 量化 Buy + LLM Hold = 🟡 关注
- 量化 Buy + LLM Sell = 🟠 冲突
- 量化无 + LLM Buy = 🟡 关注
- 量化无 + LLM Sell = 🔴 弃
- 同时显示量化分/命中策略 + LLM 推理链,用户自行判断

## 8. 启动实施

P0(环境搭建) + P1(量化层迁移)开始执行。
