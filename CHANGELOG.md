# Changelog

All notable changes to TradingAgents are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes within the 0.x line are called out explicitly.

## [0.4.0] — 2026-08-16

发布前全面审计 + top18 终态策略库 + Web UI 主题统一 + 买入计划/持仓跟踪工作流。这是 v0.3.x 之后的发布候选版,重点解决"文档与代码事实不一致、事件池陈旧缓存、UI 样式散落"三类上线风险,并把 Web UI 从纯选股/分析工具升级为完整的"计划买入 → 持仓跟踪"闭环。

### Added

- **top18 终态策略库**(`strategy_library_final.py`):24 家族去底部 6,有效策略 18 个(S=5/A=11/B=2),指标口径 OOS 2025-01-01~2026-07-14。新增 `bull_align_ma20_bounce` / `continuous_strong_close` / `event_templates` / `factor_combo_rebalance` / `factor_ranked_event` / `fc_factors` / `long_consolidation_breakout_v2` / `monthly_breakout` / `volume_price_trend` / `weekly_adx_dmi_breakout` / `weekly_breakout_pullback` 等策略实现。
- **统一 Web 主题**(`web/theme.py`):全局 CSS 与 HTML 小件收敛为设计令牌,主区 Hero、侧栏品牌卡、进度阶段卡、空状态卡统一;移除 Google Fonts 外部依赖,中文字体栈本地回退。
- **事件池缓存指纹**(`event_templates.py`):缓存 key 增加日线数据内容指纹,增量更新后旧事件池自动失效,避免陈旧信号参与 factor_ranked_event。
- **Universe-prune 策略提速**(`quant_picker.py`):主进程一次计算/复用各任务 top 300/500 股票池,worker 只对任务 universe 计算特征并跳过全市场预热。FC 因子策略(全市场截面 rank)自动排除,保持完整 universe。
- **双 Pool 执行**(`quant_picker.py`):规则策略池 worker 释放全市场日线,FC 因子策略单独全市场 Pool,降低峰值内存。
- **universe 列表持久化**(`quant_universe_cache=true`):按日线内容指纹 + ST/上市日期/日历缓存指纹 + 过滤参数签名缓存 top 300/500 代码列表,交易日不变时主进程不再重复计算。
- **按需特征列**(`features/strategy_features.py` + `build_features_vectorized(columns=...)`):top18 规则策略声明真实需要的列,按依赖图只计算所需特征。top500 日线特征 5.8s -> 约 1s。
- 实测 3042 股缓存、8 workers:冷缓存 **69s**、热缓存 **37s**、18/18 策略 0 错误;关闭优化回退路径 358s(4 workers),Top N 与 `all_records` 完全一致。
- **缓存更新跨进程锁**(`quant/data/cache.py`):`update()` 的 read-old/merge/write 全流程加进程内 + 跨进程文件锁,并改为临时 parquet + `os.replace` 原子写;同 key 去重保留 last,重跑或并发更新不产生重复行、不互相覆盖。
- **发布依赖补全**:`akshare>=1.16` / `python-dateutil>=2.9` 纳入主依赖;`adata` 独立为 `[quant-data]` 可选依赖。
- `tests/test_event_pool_cache.py`:覆盖事件池数据指纹与缓存失效。
- **买入计划 / 持仓跟踪**(`web/position_store.py` + `web/components/buy_plan.py` + `web/components/position_tracker.py`):Top N 候选表每行一键「计划买入」,计划详情展示命中策略的出场规则(信号出场 / 固定持仓 / 固定持仓 + 信号出场保护)、ATR 参数参考、次日涨跌停参考价,并自动关联 LLM 历史标签与置信分;确认买入后进入持仓跟踪,按 -5% 止损 / +8% 止盈 / 建议到期 / T+1 规则给出状态与预警。
- **Web 7-tab 布局**:量化选股 / AI 深度分析 / 买入计划 / 持仓跟踪 / 交易记录与策略跟踪 / 综合推荐 / 历史,tab 统一为细线矢量图标(去掉 emoji);新增实盘交易收益与策略实际表现跟踪;Sidebar 移除量化参数区(Top N 固定 20、worker 固定 8),模型配置区仅保留数据源与 API Key,快速/深度模型内置为 DeepSeek-V4-Flash / DeepSeek-V4-Pro。
- **模型数据源精简**:Web UI 仅保留 DeepSeek 官方与 OpenCode 中转两条数据源,API Key 优先使用侧边栏输入,回退 `DEEPSEEK_API_KEY` 环境变量。

### Changed

- 默认 Top N 统一固定为 **20**(Web/CLI/pipeline/`pick()` 四处一致)。
- 默认日线缓存统一为 `daily_main_board` 全量主板;`daily_main_board_liquid` 作为可选降速项保留。
- 股价上限默认调整为 70 元;新增当日涨停/跌停不入选过滤(`quant_exclude_limit_up_down=true`,按板块识别 10%/20% 幅度,发生在价格过滤之后、流动性排序之前)。
- README / USAGE / NOTICE / CLAUDE 的策略数与版本事实统一为 18 策略 / v0.4.0。
- `scripts/eval_recommendations.py` 移除硬编码 Windows 绝对路径,改为 `QUANT_CACHE_DIR` + 项目缓存目录自动探测。

### Performance

- 3042 股缓存、18 策略、8 workers 实测:冷缓存 **102s**(0 错误),事件池/页缓存命中后 **39s**;`prune_universe=false` 旧路径 4 workers 为 358s。两组 Top 10 与 `all_records` 行数逐项一致。

### Fixed

- `web/launch.py` 显式检查 `streamlit` 启动命令并返回真实退出码。
- `web/components/report_viewer.py` 对历史报告中的信号/股票标签做 HTML 转义后再渲染。
- `scripts/eval_recommendations.py` JSON 读取显式 UTF-8。
- 量化层说明文案与 argparse/CLI 默认值不再残留 "10 个有效策略"。
- `web/components/quant_pick.py` S/A/B/C 分级列合并短线 `n_{tier}` 与中线 `n_M_{tier}` 计数,修复中线命中时等级列显示为空。
- 移除 Sidebar 嵌套 expander(Streamlit `Expanders may not be nested` 报错),OpenCode Base URL 与 API Key 改为独立 widget key 后再写 session_state。
- LLM 历史日志补写 `final_signal_label` / `conviction_score` / `data_quality_summary`,`web/history.extract_signal` 优先读取新字段并对旧日志兼容。
- 发布前复查修复:`买入计划/持仓跟踪` 同一计划在两个 tab 同时展开会触发 Streamlit 重复 form key(现按 tab 前缀隔离);历史 tab 不再重复渲染选股表,避免覆盖主 tab 勾选状态。
- `web/position_store.py` 状态迁移(确认买入/放弃/卖出)改为单锁原子校验;损坏的 `plans.json` 自动备份后再重建;NaN 量化指标安全落盘;最新价在缓存缺该股时正确回退腾讯行情;持有天数改用交易日历,避免周末/节假日提前触发到期。
- 主 tab 矢量图标通过 CSS `:not()` 限定主 Tab 作用域,避免误标报告页内部的多空/风控嵌套 tabs;PDF 封面信号改用文字标签(中文字体无 emoji 字形)并缓存 PDF 生成结果;`strip_think_tags` 兼容 `<thinking>` / `<think>` 标签对。
- 策略优化记录固化:从 `stock_selector/strategy_combo_opt/outputs/cache/portfolio_metrics_v2_oos.json` 固化 18 个策略的 OOS 累计收益/回撤/Sharpe/盈亏比/卖出笔数/平均持仓,买入计划「命中策略与出场规则」展示该回测口径;入场建议中的 `均收+154.61%` 错误标签统一更正为 `OOS累计收益+154.6%`。
- 买入计划出场规则增加初学者可读建议:ATR 止损/移动止盈触发条件、保本 kill 语义、信号出场 vs 固定持仓执行差异、因子组合策略的小资金适用性提示;买入计划与持仓跟踪改为 `st.fragment`,状态筛选切换不再重跑整页。
- LLM 管线优化:vendor 工具结果 180s 进程级缓存(7 个分析师重复请求同一数据时只请求一次);Conflict Resolver 增加中文评级解析(如「最终评级:减持 (Underweight)」),减少 Unknown 样本;本地 44 条历史信号回测显示置信分 61-100 组 5/10 日前向收益优于低置信组,管线整体方向有效但强买样本仍少。
- 数据源兜底:`get_insider_transactions` 兼容 mootdx F10 返回 dict 的情况(历史报告里最高频失败项);`get_fund_flow` 增加新浪资金流备用源;`get_industry_comparison` 增加新浪行业板块备用源;三大财务报表在 Sina 为空时自动切换东财 datacenter;`get_fundamentals` 增加东财前十大股东数据。
- 批量分析重试不再丢失其它 tracker;AI 分析 tab 增加「重试失败任务」按钮,合并保留已完成/运行中任务。
- Sidebar 移除「分析日期」和快速/深度模型选择;模型固定 DeepSeek-V4-Flash / DeepSeek-V4-Pro;API Key 持久化到 `~/.tradingagents/web_config.json`,重启自动恢复;非 ASCII API Key/网关地址提前给出明确中文报错,不再抛 httpx `'ascii' codec` 异常。

### Tested

- `pytest tests/` **384 passed + 44 subtests,1 skipped**(新增事件池缓存、主题、universe-prune、缓存锁、买入计划/持仓跟踪、tab 图标测试后本版实跑)。
- Streamlit AppTest:`exceptions=0`,7 个主 tab 及嵌套报告 tabs 全部正常渲染(覆盖重复 widget key / 计划详情 / 持仓详情 / 批量重试保留 tracker 场景)。

## [0.3.1] — 2026-08-09

上线前审计(AUDIT_REPORT.md)v0.3.1 跟进清单收尾:补齐 8 个新模块的剩余单元测试 + 清理 P2/P3 遗留项。

### Added - 单元测试(审核 P1 #2 剩余)

- **`tests/test_quant_picker.py`**(29 用例):`_compute_entry_advice` 入场建议(短/中线/无数据 N/A)、`get_tier_of` 分级 + 中线 `M_` 前缀、`needs_full_data` 周月季判断、`_aggregate`/`compute_top_n` 按代码分组聚合(加权分/加权胜率/加权持仓天/tier 计数/排序切片)、`format_top_picks_summary` 摘要格式化、`pick()` top_n 5/10/20 参数校验。
- **`tests/test_quant_pick_component.py`**(10 用例):`web/components/quant_pick.py` 抽出的 `_prepare_display_df`(rank/名称/胜率/持仓格式化)+ `_select_all_updates`(全选联动 session_state 更新)。
- **`tests/test_cli_quant_pick.py`**(14 用例):`cli/quant_pick.py` 的 `_parse_today` / `_resolve_output_path`(目录自动命名 + 格式映射)/ `_serialize_result`(JSON 兼容序列化)。

### Fixed

- **`web/components/quant_pick.py` 抽纯函数 + 缺列防崩**:`render_quant_picker` 拆出 `_prepare_display_df` / `_select_all_updates`(原全选联动逻辑内嵌不可测)。顺带修复隐藏 bug:历史数据缺 `avg_win_rate`/`avg_holding_days` 列时 `df.get(col, 0)` 返回标量导致 `.round()` 崩,现缺列填 0。
- **`tradingagents/graph/trading_graph.py` yfinance 懒加载(P2 #4)**:验证发现 yfinance 并非死 import——`y_finance.py` 是 default_config 可选 vendor(Options: a_stock/alpha_vantage/yfinance),`stockstats_utils.py`/`yfinance_news.py` 也依赖,全删不安全。改为 `_fetch_returns` 内懒加载,graph 模块不再硬依赖;yfinance 不可用/失败时静默降级 Sina fallback,行为不变。
- **`web/app_main.py` 综合推荐批量路径 save_recommendation 去重(P2 #5)**:tab_rec 遍历 trackers 落盘加 `rec_saved_{ticker}_{date}` 守卫(与单股路径一致),避免每次 rerun 重复写盘。
- **`web/components/recommendation.py` 截断改完整显示(P3 #8)**:`final_decision[:1500]` / `final_ranked[:800]` 截断移除,外层 expander 默认收起,长报告完整可读。
- **`web/components/sidebar.py` provider 回退(P3 #9)**:`_PROVIDERS` 从仅 DeepSeek 扩到 7 个(DeepSeek/OpenAI/Anthropic/Qwen/GLM/MiniMax/Ollama 本地),help 文案列全各供应商对应 API key 环境变量。

### Tested

- `pytest tests/ --ignore=tests/test_google_api_key.py` **243 passed + 44 subtests,0 failures**(较 v0.3.0 新增 53 用例)。
- 懒加载改造后 `test_memory_log.py`(60 用例,覆盖 `_fetch_returns`)全绿;`yfinance.Ticker` 的 mock patch 依旧有效。

## [0.3.0] — 2026-07-19

本版是 TradingAgents-astock fork 的"量化整合大版本":在原 LLM 多 Agent 投研流水线**之前**插入 46 策略量化前置筛选层,形成"量化广度扫描 + LLM 深度分析"双层架构。包名 `tradingagents-astock` -> `tradingagents-quant`,版本 0.2.18 -> 0.3.0。

### Added - 量化层(类 A,23 文件)

- **`tradingagents/quant/`**:从 stock_pick_live 迁入完整量化层。`pick()` API 跑 46 策略(multiprocessing.Pool + spawn)产出 Top N 候选 + 命中策略 + 加权分 + 入场建议。
- **46 策略**(`strategy/strategy_library_final.py` 2267 行):S=11 / A=18 / B=14 / C=3,涵盖日线/周线/月线/季度多时间框架。`module` 字段全部改为 `tradingagents.quant.strategy.xxx`(46 处)。
- **features/backtest/data/utils** 四个子模块: indicators/factors/pipeline、Signal 类、cache/universe/st_filter、trading_calendar,纯 pandas/numpy 实现。
- **`sina_fetcher.py`**:零依赖全市场日线抓取(ThreadPoolExecutor 并发)。

### Added - LangGraph 集成(类 B,24 文件)

- **`agents/quant_picker_node.py`**:LangGraph 节点封装,把量化层 Top N 结果格式化为 `quant_pick_context` 字符串写入 state。batch 模式 no-op 优化。
- **`agents/conflict_resolver.py`**:纯规则冲突解决节点(无 LLM),合并量化 Buy/Sell + LLM Buy/Hold/Sell 输出 4 档标签:🟢 强买 / 🟡 关注 / 🟠 冲突 / 🔴 弃。
- **`graph/setup.py` 拓扑改造**:`START -> Quant Picker -> 7 Analyst -> Bull/Bear 辩论 -> Research Manager -> Trader -> 3 方风险辩论 -> Portfolio Manager -> Conflict Resolver -> END`。
- **7 个 Analyst prompt 注入**:market/social/news/fundamentals/policy/hot_money/lockup 全部加 `{quant_pick_context}` partial,让 LLM 分析师看到量化层命中策略/tier/胜率/加权分。
- **`agent_states.py` + `propagation.py`**:加 `quant_pick_context` + `final_ranked_decision` 字段并初始化。
- **`default_config.py`**:加 7 个 quant_* 配置(quant_layer_enabled=True / quant_daily_cache_name="daily_main_board_liquid" / quant_top_n_default=20 / quant_n_workers=8 等)。

### Added - Web UI 4-tab 重构(类 C,10 文件)

- **`web/app.py` + `web/app_main.py` 拆分**:app.py 改为最小入口(只含 `if __name__ == "__main__":` guard),app_main.py 装所有 streamlit 代码。修复 Windows multiprocessing spawn worker 重复 import 导致的进程增殖崩溃。
- **4 tab 布局**:📊 量化选股 / 🤖 AI 深度分析 / 🎯 综合推荐 / 📜 历史。
- **`components/quant_pick.py`**:Top N 候选表 + 全选 checkbox + 中文名展示 + 命中策略详情折叠。
- **`components/recommendation.py`**:综合推荐 tab,按 🟢强买 / 🟡关注 / 🟠冲突 / 🔴弃 四类展示。
- **`components/progress_panel.py`**:加 `render_quant_progress` 函数,量化层 46 策略并行进度条。
- **`progress.py`**:加 `QuantProgressTracker` 子类(独立阶段常量,不动 `PIPELINE_STAGES` 编号)。
- **`runner.py`**:加 `run_quant_pick_in_thread`,后台线程跑 `pick()` 流式消费。
- **`history.py`**:加 `get_quant_history` / `save_quant_pick` / `save_recommendation`。
- **`pdf_export.py`**:`_collect_sections` 头部插量化上下文段。
- **品牌改名**:TradingAgents-Astock -> **Aquant 投研工具**(Web UI 标题)。

### Added - CLI + 自动化(类 D,3 文件)

- **`cli/quant_pick.py`**(~260 行):Typer `quant-pick` 子命令,9 个选项,4 种输出格式(terminal/json/csv/markdown),默认 `--cache daily_main_board_liquid`。
- **`scripts/daily_pipeline.py`**(~250 行):每日 cron 入口,11 个选项,默认只跑量化层,`--with-llm` 启用 LLM 深度分析(对 Top N 逐只调 `TradingAgentsGraph.propagate()`),落盘 `outputs/daily/<YYYY-MM-DD>/`。

### Added - 配置依赖(类 E,2 文件)

- **`pyproject.toml`**:包名改 `tradingagents-quant`,版本 0.3.0;dependencies 加 `numpy>=1.24` / `pyarrow>=14.0`;新增 `[project.optional-dependencies] quant = [...]`。
- **`requirements-quant.txt`**:新建,量化层独立依赖(pandas/numpy/pyarrow/requests/pytz)。

### Changed - 默认 LLM 改 DeepSeek

- `default_config.py` `llm_provider` `openai -> deepseek`,`deep_think_llm` `gpt-5.4 -> deepseek-v4-pro`,`quick_think_llm` `gpt-5.4-mini -> deepseek-v4-flash`。
- `web/components/sidebar.py` `_PROVIDERS` 缩减到 DeepSeek 一项(快速=Flash,深度=Pro)。
- `tradingagents/llm_clients/model_catalog.py` DeepSeek deep 模型列表重排,Pro 优先。

### Performance - 量化层 30 -> 12 分钟

- `daily_main_board`(3042 股)-> `daily_main_board_liquid`(2129 股,流动性前 70%),6 处文件统一改:`default_config.py` / `quant_picker.py` / `cli/quant_pick.py` / `scripts/daily_pipeline.py` / `agents/quant_picker_node.py`。
- 实测对比(workers=8,--today 2026-07-14):v1 全量 cache 4 workers 1836.8s(30.6 min)-> v2 liquid cache 8 workers 733.6s(12.2 min),**加速 2.50x**。
- 慢策略 258s -> 142-159s(-40%),快策略 14-20s -> 11-15s。

### Fixed

- **multiprocessing spawn 崩溃循环**:原 `web/app.py` 所有 streamlit 代码在模块顶层,Windows spawn worker 重复 import 时重执行 streamlit,导致进程数无限增殖。拆为 `app.py`(最小入口)+ `app_main.py`(main 函数)。
- **量化层"无候选股票"**:`pick()` 加日期 fallback,当 `today` 超过 cache 最新日期时自动回退到 cache 最新日期,避免 46 策略 `_eligible_by_date.get(current_date)` 返回 None 导致零信号。
- **选股历史点不显示**:Tab 1/4 点历史按钮加载 `quant_picks` 后未渲染。加新分支 `elif quant_picks and len(top_picks)>0:` 调 `render_quant_picker`,且历史按钮同时清 `quant_tracker` 避免 branch 2 优先匹配。
- **FINAL_STATS 46 vs 42 不一致**:旧文档标 42 策略,实际 46(S=11/A=18/B=14/C=3),全文档统一。
- **trading_graph 不消费 final_ranked_decision**:Conflict Resolver 输出未被下游读取,修。
- **conflict_resolver rating 解析失效**:rating 标签正则匹配错误,修。
- **daily_pipeline batch 优化失效**:批量模式没复用 Pool,修。
- **create_initial_state 缺 final_trade_decision**:加初始化,避免 PM 节点读 None。
- **trading_graph fallback cache name 不一致**:fallback 路径仍用旧 cache 名,统一改 liquid。
- **checkpoint resume pre_quant_context 丢失**:resume 时 quant_pick_context 字段未保留,修。
- **daily_pipeline main 永远 return 0**:错误码被吞,改返回实际退出码。

### Tested

- 量化层端到端冒烟测试:`QUANT_CACHE_DIR=stock_pick_live/outputs/cache py -m cli.main quant-pick --today 2026-07-14 --top-n 10 --workers 8 --output-format json`,46 策略全跑通,0 错误,12.2 min,Top 1: 600428(加权分 24.81,6 个策略命中)。
- Web UI 4 tab 布局:Streamlit 1.50 实测,4 tab 切换正常,选股/分析/推荐/历史四态工作流跑通。
- 真 LLM 端到端测试:待 DeepSeek API quota 恢复后跑 `tradingagents analyze` 验证 Quant Picker + Conflict Resolver 节点真生效(待 task #8)。
- 单元测试:`pytest tests/ --ignore=tests/test_google_api_key.py` 135 passed + 44 subtests,0 failures。

### Refined - 2026-07-20 上线前审校

**策略库精简(用户决策)**:46 个策略 -> 10 个有效(S=2/A=3/B=2/C=3),36 个弃用策略**彻底清除**(不再保留定义)。`strategy_library_final.py` 从 2267 行精简到 477 行,删除 36 个弃用策略 .py 文件,只保留 10 个 active 策略文件 + 4 个核心文件(`__init__.py` / `base.py` / `market_filter.py` / `strategy_library_final.py`)。

**Batch flow 三 bug 修复**(4 股并行 AI 分析 + 综合推荐 pipeline):
- **quant_pick 重复**:`web/app_main.py` `_build_quant_contexts_for_batch` 一次性构建所有 ticker 的量化上下文(优先复用 `save_quant_pick` JSON,否则同步调 `prepare_quant_contexts`),通过 `run_analysis_in_thread(pre_quant_context=...)` 注入,使 Quant Picker LangGraph 节点 no-op。修复前 N 只股票 = N×3 min,修复后 = 1×3 min。
- **tab_rec 丢标的**:`tab_rec` 原只读 `st.session_state["tracker"]`(单 active ticker),N-1 只被静默丢弃。改为遍历 `st.session_state["trackers"]` 字典,对每个 is_complete tracker 自动 `save_recommendation`。
- **memory_log 写竞争**:N 线程并发 `store_decision()` / `update_with_outcome()` 损坏 markdown(条目文本互相渗透)。`tradingagents/agents/utils/memory.py` 加模块级 `threading.Lock`,覆盖 append + read-modify-write。

**包元数据清理**:
- 删除死依赖 `redis>=6.2.0` 和 `backtrader>=1.9.78.123`(代码无任何 import,违反 CLAUDE.md "零外部服务依赖" 原则)。
- `tradingagents/__init__.py` 补 `__version__ = "0.3.0"`,与 `pyproject.toml` 一致。
- `pyproject.toml` `[project.urls]` 加 Changelog URL。
- `tests/test_google_api_key.py` 加 `pytest.importorskip("langchain_google_genai")`,未装 `[google]` extra 时跳过而非崩溃。

**文档归档与品牌一致**:
- `INTEGRATION_PLAN.md` -> `docs/INTEGRATION_PLAN_v0.3.0.md`(320 行规划文档,完成使命后归档)。
- `CLAUDE.md` 版本号 `0.2.18 -> 0.3.0`,标题 `TradingAgents-Astock -> TradingAgents-quant`。
- `NOTICE` 品牌名 `TradingAgents-Astock -> TradingAgents-quant`,补 v0.3.0 改造清单。
- `web/components/report_viewer.py` 导出文件名前缀 `TradingAgents-Astock_ -> Aquant_`。
- `README.md` 删除引用已删除图片的两处 `<img>` 标签(web-ui-welcome.png / wechat-sponsor.jpg)。
- 全文档统一策略数表述:README/strategy_library_final.py docstring/FINAL_STATS 三处对齐为 "10 个有效 (S=2 A=3 B=2 C=3)"。

**冗余清理**:
- 删除一次性脚本:`scripts/_fix_strategy_imports.py`、`scripts/_inject_quant_context_to_analysts.py`、`scripts/debug_single_strategy.py`(均含硬编码 Windows 绝对路径,完成使命)。
- 归档调试脚本到 `scripts/archive/`:`test_pick_v3_12strats.py`、`test_ai_analysis_live.py`、`test_back_half_llm.py`、`test_batch_flow.py`(保留作历史回归参考)。
- 删除根目录 `test.py`、`test_astock.py`、`test_data_quality.py`(用途已被 `tests/` 覆盖)。
- 清空 `outputs/` 和 `tradingagents/quant/outputs/` 内容(测试产物)。
- `.gitignore` 加 `outputs/`、`tradingagents/quant/outputs/`、`*.parquet`、`stock_pick_live/` 条目。

### Breaking Changes

- 包名 `tradingagents-astock` -> `tradingagents-quant`。`pip install -e .` 后 import 路径不变(`tradingagents.*` 子模块未改名)。
- 默认 LLM 从 OpenAI 改 DeepSeek。原 OpenAI 用户需在 `.env` 设 `OPENAI_API_KEY` 并在 Web UI 侧栏手动切换 provider(注:Web UI 当前仅展示 DeepSeek,其他 provider 需手动改 sidebar.py `_PROVIDERS`)。
- `default_config.py` 默认 `quant_layer_enabled=True`,即 `TradingAgentsGraph.propagate()` 默认会跑量化层。若想跳过,设 `config["quant_layer_enabled"]=False`。
- **2026-07-20**:弃用策略彻底清除。原 46 个策略中 36 个被弃用(前视偏差修复后严重退化),已从 `strategy_library_final.py` 和 `strategy/` 目录物理删除,不保留定义。如需恢复,从 `stock_pick_live` 上游项目重新迁入。
- **2026-07-21 P1 修复**:
  - `tradingagents/agents/conflict_resolver.py` 节点函数加 `try/except` 防御,极端输入(state 为 None/list/int/string/带异常的 .get)降级为 🔴 弃 + 错误信息,不再崩溃 LangGraph 流水线。
  - `tradingagents/default_config.py` `quant_top_n_default` 20 -> 10(平衡覆盖与 API 成本,20 易耗尽 DeepSeek quota)。
  - `web/components/sidebar.py` Top N selectbox 默认选项调为 "10",help 文案更新。
- **2026-07-21 风控辩论中文输出修复**:5 个辩论 agent(Bull/Bear researcher + Aggressive/Conservative/Neutral debator)原本 prompt 全英文且未调 `get_language_instruction()`,导致 Web UI 风控评估 3 个标签页内容为英文,仅 PM 最终总结是中文。修复:① 顶部加 `from tradingagents.agents.utils.agent_utils import get_language_instruction`;② prompt 末尾追加 `{get_language_instruction()}`(英文骨架保留以保推理质量,仅强制输出语言);③ argument 前缀中文化(`Bull Analyst:` -> `多方分析师:` / `Aggressive Analyst:` -> `激进分析师:` 等)。同步更新 `agent_utils.py:38` docstring 反映新策略。`pytest tests/` 135 passed。
- **2026-07-21 综合推荐显示股票名称**:Tab 3 综合推荐 4 列分类下 expander 标题原显示 6 位代码(如 `300750`),用户难以辨认。`tradingagents/dataflows/a_stock.py` 新增 `get_stock_name(ticker)` 公共函数(基于 `_code_to_name` 反查,失败返回 None 而非抛错);`web/components/recommendation.py` expander 标题改为 `名称（代码）` 格式,反查失败时回退到原代码格式。
- **2026-07-21 修复 NotFoundError DOM 异常**:`web/history.py` `_save_incomplete_index` 用 `tmp.replace(target)` 原子写,Windows 上偶发被杀软/索引器持锁导致 `PermissionError [WinError 5]`。该函数经 `get_incomplete_history` 在 sidebar 每次 rerun 调用,崩溃后 Streamlit script_runner 中断渲染,浏览器 React DOM 残留半构建节点,触发 `NotFoundError: removeChild`。修复:① `_save_incomplete_index` 加 4 次重试(50/100/150/200ms 退避)+ 最终失败回退到直接 `open()+json.dump` + 仍失败则静默 `logger.warning`;② `get_incomplete_history` 调用处再加一层 try/except 兜底,sidebar 渲染永不崩。`pytest tests/` 135 passed。
- **2026-07-21 修复 Bull/Bear 辩论路由崩溃(回归自 2026-07-21 风控辩论中文输出修复)**:把 `bull_researcher.py` 的 `argument` 前缀从 `"Bull Analyst: "` 改为 `"多方分析师: "`后,`conditional_logic.py:77` 的 `should_continue_debate` 路由器 `current_response.startswith("Bull")` 永远为 False,fall through 到 `return "Bull Researcher"`,但 Bull Researcher 的 conditional edges 只允许 `{Bear Researcher, Research Manager}`,LangGraph 抛 `KeyError: 'Bull Researcher'` 中断分析。修复:`should_continue_debate` 同时识别英文 `Bull` 和中文 `多方` 前缀,兼容新旧 history(checkpoint resume 场景)。风控辩论路由不受影响(用 `latest_speaker` 字段,仍是英文 `Aggressive`/`Conservative`/`Neutral`)。`pytest tests/` 135 passed。
- **2026-07-21 Conflict Resolver 3 状态逻辑(手动选股不再被降级)**:原 `_detect_quant_hit` 只区分 hit/miss 两态,手动输入的 ticker 因 `quant_picker_node` 跳过注入(无负面锚)导致 `quant_pick_context` 为空,被 Conflict Resolver 当成"未命中"降级 LLM Buy->🟡关注 / Hold->🔴弃。重构为 3 态:① `hit`(命中策略)② `miss`(跑了量化但未命中,降级合理)③ `skipped`(手动选股,量化层未参与,不降级)。`_detect_quant_state` + `_assign_label` 重写,`skipped + Buy -> 🟢强买` / `skipped + Hold -> 🟡关注` / `skipped + Sell -> 🔴弃`。`pytest tests/` 135 passed。
- **2026-07-21 修复深度分析 vs 综合推荐信号不一致**:深度分析 tab 的 "TRADING SIGNAL" 大字显示 `tracker.signal`,原来源是 `parse_rating(final_ranked_decision)` 抓 LLM 5 档 rating(Buy/Sell/Hold),而综合推荐 tab 显示 Conflict Resolver 的 4 档标签(🟢强买/🟡关注/🟠冲突/🔴弃),两者会 diverge(如 LLM Buy + quant miss 时深度分析显示绿色 BUY,推荐却显示黄色 🟡关注)。修复:① `agent_states.py` 加 `final_signal_label` 字段;② `conflict_resolver.py` 节点返回 `final_signal_label: label`(4 档中文标签);③ `trading_graph.py:finalize_graph_run` 优先用 `final_signal_label`,空时回退到 `parse_rating`(兼容旧数据);④ `report_viewer.py:_signal_style` + `pdf_export.py:_signal_color` 扩展识别 emoji 前缀(🟢/🟡/🟠/🔴)映射颜色,保留 5 档英文回退;⑤ `report_viewer.py:77` / `pdf_export.py:448,681` 去掉 `.upper()`(中文标签无需大写)。两个 tab 现在显示同一信号,颜色一致。`pytest tests/` 135 passed。

## [0.2.18] — 2026-07-10

合并社区 PR #75（致谢 @wangyuxun6699），与 v0.2.17 的 #76 修复同属一类问题：LLM 工具调用把非股票标识当 `ticker` 传入。

### 合并社区 PR
- **#75 新闻工具校验 ticker 防概念词中断分析（@wangyuxun6699）**：运行 000629 分析时部分 Agent 把概念词「钒电池」当 `ticker` 传给 `get_news`，底层解析抛 ValueError 中断分析。三层修复：① `get_news` / `get_insider_transactions` 增加 6 位代码校验，误传时**返回可恢复的错误提示**（不抛异常、不中断 LangGraph）；② 修正 5 个分析师提示词里误导性的 `get_news(query, ...)` 描述 → `get_news(ticker, ...)`（**这是模型传概念词的提示词层根因**）；③ 强化 `instrument_context`，明确「参数名为 ticker 时只传目标股票代码」。
- 与 v0.2.17 的 `resolve_ticker` 报错改进形成互补防线：提示词预防 → 工具层校验软着陆 → 解析层报错可自纠。

### 测试
- PR 新增 `tests/test_news_data_tools.py` 3 项（概念词拦截不进 vendor 层 / 合法 6 位码正常路由）通过。
- 全量回归：Python 3.12 干净 venv 下 `pytest tests/` **135 passed + 44 subtests**（仅 test_google_api_key 因未装可选依赖 `[google]` 跳过）。

## [0.2.17] — 2026-07-10

两个健壮性修复，无破坏性变更、无新依赖。

### 修复
- **fpdf 包损坏导致 Web UI 启动即崩（#72）**：`web/pdf_export.py` 顶部的 `from fpdf import FPDF` 一旦失败（fpdf2 卸载不干净留下 namespace 残包、或 pyfpdf 1.x 没有 `fpdf.enums`），`web/app.py` 在 import 链上直接崩溃、整个应用起不来。现改为守卫式导入：fpdf 坏了只禁用 PDF 导出（Markdown 导出照常），点击 PDF 按钮时给出确切修复命令 `pip uninstall -y fpdf fpdf2 && pip install "fpdf2>=2.8.0"`。
- **LLM 把行业名当股票代码时报错信息不可自纠（#76）**：弱模型做工具调用时偶尔把行业/概念名（如 002174 游族网络所属行业「游戏」）当 `ticker` 传入，旧报错「找不到股票 '游戏'，请检查名称是否正确」让用户困惑（自己输入的明明是 002174）、也无法引导模型纠正。新报错写明「ticker 只接受 6 位代码或完整股票名称，行业/概念/板块名无效」，模型读到 ToolMessage 后可在下一次调用自我纠正。

### 测试
- 实测模拟损坏 fpdf（`sys.modules` 注入空 namespace 包，复现 #72 同款 `cannot import name 'FPDF' from 'fpdf' (unknown location)`）：`web.pdf_export` import 成功、`generate_markdown` 正常出稿、`generate_pdf` 抛带修复指引的 `PDFExportError`。
- `resolve_ticker` 回归：`002174`/`600519.SH`/`贵州茅台` 正常解析；`游戏` 触发新报错文案。
- `tests/test_pdf_export.py` + `test_safe_ticker_component.py` + `test_stock_display.py` + `test_web_history.py` + `test_astock_sina_supplement.py` 共 25 项通过（2 项 pdf 字体用例在本机因 fpdf2 2.8.4 < 2.8.6 环境原因失败，HEAD 上同样失败，与本次改动无关）。

## [0.2.16] — 2026-06-28

本版采纳一个社区贡献的批量样例脚本 + 文档补充，无核心代码改动。

### 采纳社区贡献
- **`examples/run_cases.py` 升级（采纳 #68 @zcc2xj）**：旧版批量脚本只把 `final_trade_decision` 手写进简易 `.md`。新版复用 CLI 的 `save_report_to_disk()`，每只标的输出与 CLI **完全一致**的 `complete_report.md`（分析师 / 研究 / 交易 / 风险 / 组合五个分区子目录 + 合并报告），并落一份字段齐全的 `summary.json`（10 个顶层报告 + Bull/Bear 辩论 + 三方风险辩论历史）。解决 #68「example 脚本如何拿到 CLI 那样的 complete_report.md」。

### 文档
- **README 常见问题新增 httpx 依赖冲突说明（#70）**：澄清 **litellm / mcp 不是本项目依赖**（用户报错里这两条来自其环境的其它包）；核心安装 `pip install -e .` 默认不冲突，仅装 `[google]` 用 Gemini 时 mootdx（`httpx<0.26`）与 google-genai（`httpx>=0.28`）互斥。给出解法：mootdx 走 TCP、运行时不调 httpx（实测 0.11.7 在 httpx 0.28.1 下取数正常，可放心升 httpx）/ 分 venv / 用国内直连模型不装 `[google]`。
- README 常见问题新增「不进 CLI 怎么批量跑、拿完整报告」条目，指向 `examples/run_cases.py`。

### 测试
- `examples/run_cases.py` py_compile 语法通过；静态核对 `save_report_to_disk(final_state, ticker, save_path)` 签名匹配、`complete_report.md` 路径返回值正确（`cli/main.py:738-739`），脚本引用的 10 个顶层 state 字段 + debate 子状态字段全部匹配 `agent_states.py` 真实定义（含 policy/hot_money/lockup 三个 A 股特化字段）。端到端运行需用户自备 LLM key。
- httpx 解法复用 a-stock-data 同源实测：净 venv 装 mootdx 0.11.7 后 `--no-deps` 升 httpx 0.28.1，`bars()` 取日线 / 1 分钟均正常。

## [0.2.15] — 2026-06-20

本版合并 4 个社区 PR + 一批针对性修复，主线集中在「数据可靠性 + 模型可用性 + 全新安装体验」。

### 合并社区 PR（致谢贡献者）
- **#64（@wikinl）**：A 股日 K 数据滞后时未触发新浪补齐 → 修复（mootdx 返回非空但最新日期早于目标日时强制走新浪补最新交易日，并把 `15:00:00` 时间戳压到自然日，避免被 `Date <= cutoff` 误过滤）。直接缓解 #60「数据缺失」。
- **#57（@zhanghang02）**：Web 支持中断续跑 + 侧边栏暂停/停止控制（LangGraph checkpoint resume）。缓解 #27「页面刷新丢数据」。
- **#56（@zhanghang02）**：中文 PDF 字体发现 + 排版稳定性增强（`fc-match`/WQY 优先、字体环境变量覆盖、TTC 字面选择）。
- **#55（@zhanghang02）**：报告标的统一显示为「代码 + 名称」。合并时解决与 #57 在 `web/runner.py` 的冲突（#57 的 `finalize_graph_run` 已含 `graph.ticker`/`_log_state`，仅保留归一化调用挪到落盘前）。

### 修复
- **mootdx 0.11.x 全新安装 BESTIP 空串崩溃 → 中文股票名解析失败（#46/#66 根因之一）**：`_get_mootdx_client()` 升级为健壮版——TCP 探测内置可用通达信服务器列表，用显式 `server=(ip,port)` 绕过 `BESTIP.HQ` 空串 bug，三级 fallback（bestip 测速 → 裸 factory → 明确报错）。`_build_name_code_map()` 改走该 client 并加 try/except，解析失败时给出「请重试或直接输入 6 位代码」而非冒泡成风马牛不相及的报错。实测 mootdx 0.11.7：10/10 服务器可达，`贵州茅台→600519`、`宁德时代→300750` 正常。
- **`.env` 未优先于残留环境变量（#66）**：`web/app.py` 的 `load_dotenv` 改为 `override=True`，让 `.env` 的值优先；并注明启动后改 `.env` 需重启 Web 服务。
- **fpdf2 版本下限过低导致 #56 在旧版崩溃**：`collection_font_number`（TTC 字面选择）是 fpdf2 **2.8.6**（2026-02-18）才引入的参数，旧约束 `fpdf2>=2.8.0` 下用户若缓存 2.8.0~2.8.5 会在中文 PDF 导出时抛 `TypeError` → 收紧为 `fpdf2>=2.8.6`，错排提示同步更新。

### 新增
- **OpenRouter 进入 Web 侧栏模型选择器（摘自 #32，缓解 #45/#62）**：`factory`/`_PROVIDER_CONFIG` 早已支持 OpenRouter，但侧栏 `_PROVIDERS` 未列 → 补上「OpenRouter（聚合）」一项，选中后填 `vendor/model` 形式的模型 ID（如 `deepseek/deepseek-chat`）即可。凭证池/profile 体系（#32 其余部分）超出「加个模型」范围，另行评估。

### 文档
- README「快速开始」明确「装完即可用、无需 Docker」（直接 `streamlit run web/app.py` 或 `tradingagents`），缓解 #46 安装说明困惑。

### 测试
- 4 个 PR 自带测试在隔离环境实测：`test_stock_display`(11)/`test_progress_pause`(4)/`test_web_history`(3)/`test_astock_sina_supplement`(2) 全通过（PDF 测试在 Python 3.9 + 旧 fpdf2 环境因版本特性跳过，真实 ≥3.10 + fpdf2≥2.8.6 环境正常）。
- mootdx 健壮 client + 中文名解析在 mootdx 0.11.7 真实环境实测通过。

## [0.2.14] — 2026-06-18

### 修复

- **Docker 命名卷权限崩溃（#46，感谢 @tyraanTao 等报告）**：`docker compose up` 后容器内进程以
  `appuser` 运行，但 `docker-compose.yml` 的命名卷 `tradingagents_data` 挂到
  `/home/appuser/.tradingagents` 时，由于镜像里没有预建该目录，Docker 把挂载点建成了
  `root:root`，导致应用写缓存被拒：`[Errno 13] Permission denied: /home/appuser/.tradingagents/cache`。
  Dockerfile 现在在 `USER appuser` 之后**预建** `/home/appuser/.tradingagents`（含 `cache` /
  `logs` / `memory` 三个子目录）——Docker 对空命名卷会继承镜像挂载点目录的属主，于是卷归属 appuser，
  容器可正常写入。
  - 升级：`git pull` 后 `docker compose build --no-cache` 重建镜像；旧数据卷可先
    `docker run --rm -v tradingagents_data:/d alpine chown -R 1000:1000 /d` 修正属主，
    或 `docker volume rm tradingagents_data` 后重建。

### 说明

- 仅 Dockerfile 改动（预建数据目录），Python 代码 / 数据层 / Agent 逻辑零改动。
- 同批排查的 #59（PDF `latin-1` 崩溃）与 #66（`OPENAI_API_KEY` 报错）经复现确认已分别在
  v0.2.12 修复（`_ensure_fpdf2()` 守卫 + Markdown 兜底 / 各供应商独立 Key 提示），升级即可，无需改动。

## [0.2.13] — 2026-06-04

### Security

- **CLI 路径穿越加固（#51，感谢 @mituxunzhi 报告并给出修复方向）**：CLI 是唯一未对 ticker 做
  路径组件校验的入口（Web UI / `a_stock.py` / `checkpointer.py` / `stockstats_utils.py` 早已统一走
  `safe_ticker_component`）。ticker 会被拼进 `results_dir / <ticker> / <date>` 和报告保存路径，
  形如 `../../tmp/evil` 的输入可写到目标目录之外。三处加固：
  - `cli/utils.py:normalize_ticker_symbol()` — 现在委托 `safe_ticker_component()` 校验（拒绝
    `/`、`..`、`~`、`\0`、绝对路径、纯点等），并返回校验/解析后的安全值（中文名自动解析为 6 位代码）；
  - `cli/main.py:get_ticker()` — 输入后即校验，非法则提示并**重新询问**（而非崩溃），返回安全值；
  - `cli/main.py` 报告保存 — 保存路径先 `.resolve()`，若落在当前目录之外则**提示并要求确认**，
    拒绝则取消保存。
  - 实测：`../../tmp/evil`、`/etc/passwd`、`~/secret`、`a/../../b`、`\x00evil`、`.` 等 11 个穿越载荷
    全部被拒；`SPY` / `600519` / `0700.HK` / `^GSPC` / `BRK.B` 等正常代码全部通过且保留交易所后缀。

### 说明

- 纯 CLI 入口安全加固，复用既有 `safe_ticker_component` 校验器，数据层 / Agent 逻辑零改动。

## [0.2.12] — 2026-06-03

### Fixed

- **PDF 导出中文崩溃（#54）**：项目依赖 `fpdf2`，但它和早已废弃的 `pyfpdf`（1.x）**都以 `fpdf`
  名称导入**，二者共存时谁后装谁生效。用户环境里若残留 pyfpdf，导出中文报告会在库内部抛出晦涩的
  `UnicodeEncodeError: 'latin-1' codec can't encode`（pyfpdf 用 latin-1 编码每一页）。
  `web/pdf_export.py` 新增 `_ensure_fpdf2()`：导出前检测 fpdf 版本，若是旧库则抛出**可操作**的中文
  提示（`pip uninstall -y fpdf && pip install "fpdf2>=2.8.0"`），不再让 PDF 渲染到一半崩溃。
- **Docker 内无法导出 PDF（#48）**：运行镜像基于 `python:3.12-slim`，不含任何中文字体，
  `_find_cjk_font()` 返回 None → 抛「未找到中文字体」。Dockerfile 运行阶段新增
  `apt-get install fonts-noto-cjk`，容器内 PDF 导出开箱即用。
- **DeepSeek/通义/智谱等报 `OPENAI_API_KEY must be set`（#42）**：这些 OpenAI 兼容供应商各自需要
  **专属环境变量**（DeepSeek=`DEEPSEEK_API_KEY`、通义=`DASHSCOPE_API_KEY`、智谱=`ZHIPU_API_KEY`、
  MiniMax=`MINIMAX_API_KEY` 等），但 key 缺失时 ChatOpenAI 只会抛出令人误解的 `OPENAI_API_KEY` 错误。
  `openai_client.py` 现在在缺 key 时**明确指出该供应商对应的环境变量名**；Web 侧边栏 help 文案也补齐了
  每个供应商的 key 变量对照，避免用户设错。

### 说明

- 三项均为环境/配置类问题的健壮性修复，数据层与 Agent 逻辑无改动。PDF 修复经 fpdf2 实测生成
  中文报告通过 + 旧库检测单测通过；#42 经 api_key 解析分支单测全用例通过。

## [0.2.11] — 2026-05-30

### Changed

- **东财接口统一限流防封（移植自 a-stock-data v3.2）**：数据层 `a_stock.py` 里所有指向
  `eastmoney.com` 的请求（push2 / push2his / datacenter-web / search-api / np-weblist
  共 7 个调用点）统一收口到新的节流入口 `_em_get()`，多 Agent 投研跑批量分析时不再触发
  临时封 IP（社区实测东财风控：每秒 >5 / 并发 ≥10 / 1 分钟 ≥200 / 5 分钟 ≥300 触发封禁，
  多位用户反馈过）。具体：
  - 模块级 last-call 时间戳 + 最小间隔 `EM_MIN_INTERVAL`（默认 1.0s，可用同名环境变量覆盖）
    + 0.1~0.5s 随机抖动，串行限流，QPS ≤ 1；
  - 复用 `requests.Session`（Keep-Alive）+ 默认 UA；各端点保留自己的 Referer/Origin header；
  - **仅东财接口限流**——mootdx(TCP) / 腾讯 / 新浪 / 同花顺 / 财联社 / 百度 等非东财源
    不受影响（实测不封 IP）。批量场景可设 `EM_MIN_INTERVAL=1.5~2` 进一步降速。

### Tested

- 实测 4 次连续 `_em_get` 请求东财 push2（600519 = 贵州茅台），HTTP 200 返回真实数据；
  相邻调用间隔 1.47 / 1.18 / 1.42s 均 ≥1.0s，限流生效。
- `get_industry_comparison` / `get_fund_flow` / `get_dragon_tiger_board` 三个东财公共函数
  端到端跑通（走同一已验证的 `_em_get` 通道）；`py_compile` 通过；grep 复核：7 个 `_em_get`
  调用点 + 0 个残留 `_req.` + 8 个非东财源（mootdx/腾讯/新浪/同花顺/财联社/百度）未被误伤。

---

## [0.2.10] — 2026-05-30

### Added

- **Web UI 支持第三方 / 代理 API 网关（#35）**：侧边栏新增「API Base URL」输入框，
  也可在 `.env` 设 `BACKEND_URL`。方便国内用户通过中转网关访问 Claude / OpenAI 等模型
  （API Key 仍从 `.env` 读取，如 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）。
  侧边栏输入优先于环境变量，留空则用所选供应商官方地址。

---

## [0.2.9] — 2026-05-30

### Added

- **Markdown 报告导出**：分析结果页新增「下载 Markdown」按钮。MD 导出零字体依赖、
  跨平台永远可用，是 PDF 之外的稳妥兜底（#17 多位用户请求）。

### Fixed

- **PDF 中文字体跨平台崩溃（#22 / #30 / #31）**：原 `_FONT_CANDIDATES` 只列了
  macOS/Linux 字体，Windows 用户找不到中文字体 → fpdf 回退 Helvetica → 渲染中文时
  抛 `FPDFUnicodeEncodingException` / `Character "股" ... outside the range`。
  现改为**按操作系统排序的字体候选**（Windows 微软雅黑/黑体/宋体、macOS 苹方、
  Linux Noto/文泉驿）+ 递归扫描字体目录兜底。
- **PDF 失败拖垮整个结果页**：`generate_pdf` 原先在结果页渲染时被 eager 调用，一旦
  报错整页崩成 traceback，用户连分析结果都看不到。现改为 **try/except 包裹 + 懒生成**，
  PDF 失败只禁用 PDF 按钮并提示改用 Markdown，分析报告照常显示。
- **长串中文表格/段落渲染报错（#31）**：`multi_cell` 遇到无空格的长中文串抛
  `Not enough horizontal space to render a single character`。已为内容 `multi_cell`
  加 `wrapmode="CHAR"` 并复位左边距，中文按字符正确换行。
- **缺字体时优雅降级**：系统无任何中文字体时，`generate_pdf` 抛出清晰中文报错
  （指引安装字体或改用 Markdown），不再是深层 fpdf traceback。

### Tested

- Streamlit 1.50 环境用 fpdf2 2.8.4 实测：含中文标题、表格、列表、200 字无空格长串的
  报告成功生成 7 页 PDF（目视确认中文渲染无乱码、长串正确换行）；Markdown 导出正常；
  无字体路径正确抛 RuntimeError。

---

## [0.2.8] — 2026-05-30

### Fixed

- **Web UI 侧边栏收起后无法展开（#36）**：为录视频清爽化界面的自定义 CSS 把整个
  顶栏 `stHeader` 和工具栏 `stToolbar` 都 `display:none` 掉了。但 Streamlit ≥1.36 的
  「展开侧边栏」按钮 `stExpandSidebarButton` 正好嵌在工具栏内部，于是侧边栏一旦收起
  ——无论是手动点收起箭头，还是**页面缩放 / 窄屏时 Streamlit 自动收起**——展开按钮
  跟着被隐藏，再也调不出来，刷新、重启都没用。原先那行兜底的 `collapsedControl`
  选择器是旧版 DOM，在 1.45+ 已不存在，等于没写。
  修复：不再整个隐藏顶栏/工具栏，改为**保留二者、将 header 透明化、只精准隐藏
  Deploy 按钮 / 主菜单 / 状态条 / 装饰条**，侧边栏展开按钮恢复可见可点，录屏依旧干净。
  已用 Streamlit 1.50 + headless Chrome 在收起/展开两种状态下实测验证。

---

## [0.2.7] — 2026-05-19

### Fixed

- **百度 PAE 资金流下线**：`fundflow` + `fundsortlist` 接口已返回空，
  `get_fund_flow()` 全部替换为东财 push2 资金流 API（分钟级 + 日级 20 天）
- **龙虎榜机构动向**：`RPT_ORGANIZATION_BUSSINESS` 报表配置已下线，
  改用 BUY/SELL 席位明细筛选 `OPERATEDEPT_CODE="0"`（机构专用席位）
- **东财全球资讯**：新增必填参数 `req_trace`（UUID），否则返回 403

---

## [0.2.6] — 2026-05-19

### Fixed

- **依赖冲突**：`langchain-google-genai` 移至可选依赖组 `[google]`，
  消除与 mootdx 的 httpx 版本冲突。`pip install -e .` 开箱即用，
  需要 Google Gemini 时 `pip install -e ".[google]"`。
- **WebUI 模型写死 minimax**：侧边栏新增 LLM 供应商和模型选择器，
  支持 9 个供应商（MiniMax/DeepSeek/Qwen/GLM/OpenAI/Anthropic/Google/xAI/Ollama），
  默认仍为 MiniMax 但用户可自由切换。
- **阶段分析内容消失**：进度面板现在展示所有已完成阶段的报告（按时间倒序），
  不再只显示最新的一个。最新阶段自动展开，历史阶段可点击展开。

### Changed

- `.env.example` 补充 `MINIMAX_API_KEY=` 条目
- README 快速开始增加 Google 可选依赖安装说明
- README Web UI 功能列表更新

## [0.2.5] — 2026-05-17

### Breaking Changes

- **移除 akshare 依赖** — `akshare>=1.18.0` 从 `pyproject.toml` 中删除。
  所有原 akshare 调用已替换为直接 HTTP API（东财 datacenter、新浪财经、
  同花顺 10jqka、财联社 cls.cn、百度股市通）。

### Changed

- `tradingagents/dataflows/a_stock.py` 全面重构数据获取层：
  - `get_stock_data()` → 新浪 JSON K线 API + push2.eastmoney 实时行情
  - `get_stock_info()` → push2.eastmoney 个股基本信息
  - `get_stock_news()` → 东财 np-weblist 滚动新闻（已有，无变化）
  - `get_financial_data()` → 新浪财经财报三表 API
  - `get_market_news()` → 财联社 cls.cn 快讯 + 东财 np-weblist
  - `get_analyst_forecast()` → 同花顺 10jqka EPS 一致预期
  - `get_dragon_tiger_board()` → 东财 datacenter RPT_DAILYBILLBOARD
  - `get_restricted_release()` → 东财 datacenter RPT_LIFT_STAGE
  - `get_industry_overview()` → push2.eastmoney 板块行情
- 新增内部 helper：`_eastmoney_datacenter()`、`_ths_eps_forecast()`、`_sina_kline_fallback()`
- 所有函数签名和返回格式保持不变，对上层 Agent 透明

### Fixed

- 彻底消除 akshare + pandas 3.0 + pyarrow 的 `ArrowInvalid` 崩溃问题
- 消除 akshare 与 mootdx 的 httpx 版本冲突

## [0.2.4] — 2026-04-25

### Added

- **Structured-output decision agents.** Research Manager, Trader, and Portfolio
  Manager now use `llm.with_structured_output(Schema)` on their primary call
  and return typed Pydantic instances. Each provider's native structured-output
  mode is used (`json_schema` for OpenAI / xAI, `response_schema` for Gemini,
  tool-use for Anthropic, function-calling for OpenAI-compatible providers).
  Render helpers preserve the existing markdown shape so memory log, CLI
  display, and saved reports keep working unchanged. (#434)
- **LangGraph checkpoint resume** — opt-in via `--checkpoint`. State is saved
  after each node so crashed or interrupted runs resume from the last
  successful step. Per-ticker SQLite databases under
  `~/.tradingagents/cache/checkpoints/`. `--clear-checkpoints` resets them. (#594)
- **Persistent decision log** replacing the per-agent BM25 memory. Decisions
  are stored automatically at the end of `propagate()`; the next same-ticker
  run resolves prior pending entries with realised return, alpha vs SPY, and
  a one-paragraph reflection. Override path with `TRADINGAGENTS_MEMORY_LOG_PATH`.
  Optional `memory_log_max_entries` config caps resolved entries; pending
  entries are never pruned. (#578, #563, #564, #579)
- **DeepSeek, Qwen (Alibaba DashScope), GLM (Zhipu), and Azure OpenAI**
  providers, plus dynamic OpenRouter model selection.
- **Docker support** — multi-stage build with separate dev and runtime images.
- **`scripts/smoke_structured_output.py`** — diagnostic that exercises the
  three structured-output agents against any provider so contributors can
  verify their setup with one command.
- **5-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell) used
  consistently by Research Manager, Portfolio Manager, signal processor, and
  the memory log; Trader keeps 3-tier (Buy / Hold / Sell) since transaction
  direction is naturally ternary.
- **Pytest fixtures** — lazy LLM client imports plus placeholder API keys so
  the test suite runs cleanly without credentials. (#588)

### Changed

- **`backend_url` default is now `None`** rather than the OpenAI URL. Each
  provider client falls back to its native default. The previous default
  leaked the OpenAI URL into non-OpenAI clients (e.g. Gemini), producing
  malformed request URLs for Python users who switched providers without
  overriding `backend_url`. The CLI flow is unaffected.
- All file I/O passes explicit `encoding="utf-8"` so Windows users no longer
  hit `UnicodeEncodeError` with the cp1252 default. (#543, #550, #576)
- Cache and log directories moved to `~/.tradingagents/` to resolve Docker
  permission issues. (#519)
- `SignalProcessor` reads the rating from the Portfolio Manager's rendered
  markdown via a deterministic heuristic — no extra LLM call.
- OpenAI structured-output calls default to `method="function_calling"` to
  avoid noisy `PydanticSerializationUnexpectedValue` warnings emitted by
  langchain-openai's Responses-API parse path. Same typed result, no warnings.

### Fixed

- Empty memory no longer triggers fabricated past-lessons in agent prompts;
  the memory-log redesign makes this structurally impossible since only the
  Portfolio Manager consults memory and only when entries exist. (#572)
- Tool-call logging processes every chunk message, not just the last one, and
  memory score normalization handles empty score arrays. (#534, #531)

### Removed

- `FinancialSituationMemory` (the per-agent BM25 system) and the dead
  `reflect_and_remember()` plumbing; subsumed by the persistent decision log.
- Hardcoded Google endpoint that caused 404 when `langchain-google-genai`
  changed its API path. (#493, #496)

### Contributors

Thanks to everyone who shaped this release through code, design, and reports:

- [@claytonbrown](https://github.com/claytonbrown) — checkpoint resume (#594), test fixtures (#588), design feedback on cost tracking (#582) and structured validation (#583)
- [@Bcardo](https://github.com/Bcardo) — memory-log redesign (#579), empty-memory hallucination report (#572), encoding fix proposal (#570)
- [@voidborne-d](https://github.com/voidborne-d) — memory persistence design (#564), portfolio manager state fix (#503)
- [@mannubaveja007](https://github.com/mannubaveja007) — structured-output feature request (#434)
- [@kelder66](https://github.com/kelder66) — RAM-only memory issue (#563)
- [@Gujiassh](https://github.com/Gujiassh) — tool-call logging fix (#534), test stub PR (#533)
- [@iuyup](https://github.com/iuyup) — memory score normalization fix (#531)
- [@kaihg](https://github.com/kaihg) — Google base_url fix (#496)
- [@32ryh98yfe](https://github.com/32ryh98yfe) — Gemini 404 report (#493)
- [@uppb](https://github.com/uppb) — OpenRouter dynamic model selection (#482)
- [@guoz14](https://github.com/guoz14) — OpenRouter limited-model report (#337)
- [@samchenku](https://github.com/samchenku) — indicator name normalization (#490)
- [@JasonOA888](https://github.com/JasonOA888) — y_finance pandas import fix (#488)
- [@tiffanychum](https://github.com/tiffanychum) — stale import cleanup (#499)
- [@zaizou](https://github.com/zaizou) — Docker permission issue (#519)
- [@Stosman123](https://github.com/Stosman123), [@mauropuga](https://github.com/mauropuga), [@hotwind2015](https://github.com/hotwind2015) — Windows encoding bug reports (#543, #550, #576)
- [@nnishad](https://github.com/nnishad), [@atharvajoshi01](https://github.com/atharvajoshi01) — encoding fix proposals (#568, #549)

## [0.2.3] — 2026-03-29

### Added

- **Multi-language output** for analyst reports and final decisions, with a
  CLI selector. Internal agent debate stays in English for reasoning quality. (#472)
- **GPT-5.4 family models** in the default catalog, with deep/quick model split.
- **Unified model catalog** as a single source of truth for CLI options and
  provider validation.

### Changed

- `base_url` is forwarded to Google and Anthropic clients so corporate proxies
  work consistently across providers. (#427)
- Standardised the Google `api_key` parameter to the unified `api_key` form.

### Fixed

- Backtesting fetchers no longer leak look-ahead data when `curr_date` is in
  the middle of a fetched window. (#475)
- Invalid indicator names from the LLM are caught at the tool boundary instead
  of crashing the run. (#429)
- yfinance news fetchers respect the same exponential-backoff retry as price
  fetchers. (#445)

### Contributors

- [@ahmedk20](https://github.com/ahmedk20) — multi-language output (#472)
- [@CadeYu](https://github.com/CadeYu) — model catalog typing (#464)
- [@javierdejesusda](https://github.com/javierdejesusda) — unified Google API key parameter (#453)
- [@voidborne-d](https://github.com/voidborne-d) — yfinance news retry (#445)
- [@kostakost2](https://github.com/kostakost2) — look-ahead bias report (#475)
- [@lu-zhengda](https://github.com/lu-zhengda) — proxy/base_url support request (#427)
- [@VamsiKrishna2021](https://github.com/VamsiKrishna2021) — invalid indicator crash report (#429)

## [0.2.2] — 2026-03-22

### Added

- **Five-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell)
  introduced for the Portfolio Manager.
- **Anthropic effort level** support for Claude models.
- **OpenAI Responses API** path for native OpenAI models.

### Changed

- `risk_manager` renamed to `portfolio_manager` to match the role description
  shown in the CLI display.
- Exchange-qualified tickers (e.g. `7203.T`, `BRK.B`) preserved across all
  agent prompts and tool calls.
- Process-level UTF-8 default attempted for cross-platform consistency
  (note: this approach did not actually take effect; replaced in v0.2.4 with
  explicit per-call `encoding="utf-8"` arguments).

### Fixed

- yfinance rate-limit errors are retried with exponential backoff. (#426)
- HTTP client SSL customisation is supported for environments that need
  custom certificate bundles. (#379)
- Report-section writes handle list-of-string content gracefully.

### Contributors

- [@CadeYu](https://github.com/CadeYu) — exchange-qualified ticker preservation (#413)
- [@yang1002378395-cmyk](https://github.com/yang1002378395-cmyk) — HTTP client SSL customisation (#379)

## [0.2.1] — 2026-03-15

### Security

- Patched `langchain-core` vulnerability (LangGrinch). (#335)
- Removed `chainlit` dependency affected by CVE-2026-22218.

### Added

- `pyproject.toml` build-system configuration; the project now installs via
  modern packaging tooling.

### Removed

- `setup.py` — dependencies consolidated to `pyproject.toml`.

### Fixed

- Risk manager reads the correct fundamental report source. (#341)
- All `open()` calls receive an explicit UTF-8 encoding (initial pass).
- `get_indicators` tool handles comma-separated indicator names from the LLM. (#368)
- `Propagation` initialises every debate-state field so risk debaters never
  see missing keys.
- Stock data parsing tolerates malformed CSVs and NaN values.
- Conditional debate logic respects the configured round count. (#361)

### Contributors

- [@RinZ27](https://github.com/RinZ27) — `langchain-core` security patch (#335)
- [@Ljx-007](https://github.com/Ljx-007) — risk manager fundamental-report fix (#341)
- [@makk9](https://github.com/makk9) — debate-rounds config issue (#361)

## [0.2.0] — 2026-02-04

This is the largest release since the initial public version. The framework
moved from single-provider to a multi-provider architecture and grew several
production-ready surfaces.

### Added

- **Multi-provider LLM support** (OpenAI, Google, Anthropic, xAI, OpenRouter,
  Ollama) via a factory pattern, with provider-specific thinking configurations.
- **Alpha Vantage** integration as a configurable primary data provider, with
  yfinance as a community-stability fallback.
- **Footer statistics** in the CLI: real-time tracking of LLM calls, tool
  calls, and token usage via LangChain callbacks.
- **Post-analysis report saving** — the framework writes per-section markdown
  files (analyst reports, debate transcripts, final decision) when a run
  completes.
- **Announcements panel** — fetches updates from `api.tauric.ai/v1/announcements`
  for the CLI welcome screen.
- **Tool fallbacks** so a single vendor outage does not stop the pipeline.

### Changed

- Risky / Safe risk debaters renamed to **Aggressive / Conservative** for
  consistency with the displayed agent labels.
- Default data vendor switched to balance reliability and quota across
  community deployments.
- Ollama and OpenRouter model lists updated; default endpoints clarified.

### Fixed

- Analyst status tracking and message deduplication in the live display.
- Infinite-loop guard in the agent loop; reflection and logging hardened.
- Various data-vendor implementation bugs and tool-signature mismatches.

### Contributors

This release is the first with substantial outside contributions; many community
PRs from late 2025 also landed here.

- [@luohy15](https://github.com/luohy15) — Alpha Vantage data-vendor integration (#235)
- [@EdwardoSunny](https://github.com/EdwardoSunny) — yfinance fetching optimisations (#245)
- [@Mirza-Samad-Ahmed-Baig](https://github.com/Mirza-Samad-Ahmed-Baig) — infinite-loop guard, reflection, and logging fixes (#89)
- [@ZeroAct](https://github.com/ZeroAct) — saved results path support (#29)
- [@Zhongyi-Lu](https://github.com/Zhongyi-Lu) — `.env` gitignore (#49)
- [@csoboy](https://github.com/csoboy) — local Ollama setup (#53)
- [@chauhang](https://github.com/chauhang) — initial Docker support attempt (#47, later reverted; the merged Docker support shipped in v0.2.4)

## [0.1.1] — 2025-06-07

### Removed

- Static site assets that had been bundled with v0.1.0; the public site now
  lives separately.

## [0.1.0] — 2025-06-05

### Added

- **Initial public release** of the TradingAgents multi-agent trading
  framework: market / sentiment / news / fundamentals analysts; bull and bear
  researchers; trader; aggressive, conservative, and neutral risk debaters;
  portfolio manager. LangGraph orchestration, yfinance data, per-agent
  BM25 memory, single-provider OpenAI integration, interactive CLI.

[0.2.4]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/TauricResearch/TradingAgents/releases/tag/v0.1.0
