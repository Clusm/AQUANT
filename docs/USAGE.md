# Aquant 投研工具 - 使用文档

> TradingAgents-quant v0.4.0
> 量化前置筛选 + LLM 多 Agent 深度分析双层架构

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [Web UI 7-tab 工作流](#web-ui-7-tab-工作流)
- [CLI quant-pick 子命令](#cli-quant-pick-子命令)
- [每日自动化 pipeline](#每日自动化-pipeline)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 安装

### 系统要求

- Python >= 3.10(推荐 3.12/3.13)
- Windows / macOS / Linux
- 至少一个 LLM provider 的 API Key(默认 DeepSeek)

### 安装步骤

```bash
git clone https://github.com/Clusm/AQUANT.git
cd AQUANT
pip install -e .
```

可选依赖:

```bash
# Google Gemini 支持
pip install -e ".[google]"
```

### 配置 API Key

复制 `.env.example` 为 `.env`,填入你的 API Key:

```bash
cp .env.example .env
```

```ini
# 必填(默认 provider)
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# 可选其他 provider
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
ZHIPU_API_KEY=
MINIMAX_API_KEY=
DASHSCOPE_API_KEY=
XAI_API_KEY=
OPENROUTER_API_KEY=

# 可选:第三方中转/代理网关
# BACKEND_URL=https://your-proxy.com/v1
```

### 启动 Web UI

```bash
tradingagents-web
# 或
python web/launch.py
```

浏览器访问 http://localhost:8501

### 启动 CLI

```bash
tradingagents --help          # 查看所有命令
tradingagents analyze          # 交互式问卷,跑单股 LLM 深度分析
tradingagents quant-pick --help # 量化选股子命令
```

---

## 快速开始

### 场景一:今日选股 + AI 深度分析(推荐工作流)

1. 启动 Web UI:`tradingagents-web`
2. 在侧边栏选择模型数据源(DeepSeek 官方 / OpenCode 中转)并填写 API Key;分析日期自动使用今天,无需手动选择
3. 点 **开始选股** 按钮,冷缓存约 70s 跑完 18 策略(热缓存约 37s)
4. 在 **量化选股** tab 看到 Top N 候选表;勾选要深度分析的股票,或点某行的 **计划买入** 创建次日买入计划
5. 点 **开始 AI 分析**(单只)或 **并行分析全部 N 只**(批量)
6. 等 ~3-5 分钟跑完 7 Analyst + Bull/Bear 辩论 + PM
7. 在 **综合推荐** tab 看 🟢强买 / 🟡关注 / 🟠冲突 / 🔴弃 四档推荐
8. 点击导出 Markdown 或 PDF 报告

### 场景二:已知代码,直接 AI 分析

1. 在侧边栏输入框填股票代码(6 位数字)或中文名(如 "宁德时代")
2. 点 **开始分析** 按钮
3. 在 **AI 深度分析** tab 看进度

### 场景三:命令行批量选股

```bash
# 跑量化选股,输出 JSON
tradingagents quant-pick --today 2026-07-20 --top-n 10 --workers 8 --output-format json

# 跑完整每日 pipeline(量化 + 对 Top 5 逐只 LLM 分析)
python scripts/daily_pipeline.py --today 2026-07-20 --with-llm --top-n 5
```

---

## Web UI 7-tab 工作流

### 量化选股 tab

显示 Top N 候选表(Top N 固定 20):

| 排名 | 代码 | 名称 | 分级 | 命中 | 加权分 | 胜率 | 持仓天 | 计划买入 |
|------|------|------|------|------|--------|------|--------|----------|
| 1 | 600428 | 营口港 | S2 | 6 | 24.81 | 65.2% | 12d | 📋 |
| 2 | 300750 | 宁德时代 | A3 | 5 | 22.10 | 60.0% | 15d | 📋 |
| ... | | | | | | | | |

- **全选 checkbox**:一键勾选/取消全部
- **分级 S/A/B/C**:短线与中线命中合并显示(如 `S2` = 短线 1 + 中线 1)
- **📋 计划买入**:一键创建次日买入计划,同一股票同日期不会重复创建
- **命中策略详情**(展开):每只股票命中的策略列表 + 描述 + 逻辑 + 触发原因
- **入口按钮**:
  - 单只分析:选一只点 **开始 AI 分析**
  - 批量分析:选 2 只以上出现 **并行分析全部 N 只** 按钮

### AI 深度分析 tab

12 阶段进度条:

```
[1/12] Quant Picker         [████████████] 100%
[2/12] Market Analyst       [████████████] 100%
[3/12] Social Media         [████████░░░░]  75%
[4/12] News Analyst         [░░░░░░░░░░░░]   0%
...
[12/12] Portfolio Manager   [░░░░░░░░░░░░]   0%
```

完成后显示:
- 信号卡片(Buy/Hold/Sell)
- 7 份分析师报告(可折叠)
- Bull/Bear 辩论记录
- 3 方风险辩论
- Portfolio Manager 最终决策
- 量化上下文卡片(命中策略 + 加权分 + 胜率)
- 下载 Markdown / PDF 按钮

### 买入计划 tab

展示已创建计划,点击进入详情:
- 命中策略逐一标注出场类型:**信号出场**(无固定持仓天数)、**固定持仓 N 天**、**固定持仓 + 信号出场保护**
- 出场操作建议按策略展示:ATR 止损(入场价 - N×ATR14)、移动止盈(浮盈触发后最高收盘 - N×当日ATR)、保本 kill 天数、信号出场说明;因子组合策略会额外提示小资金仅参考选股方向
- 回测(OOS)记录:累计收益 / 最大回撤 / Sharpe / 盈亏比 / 卖出笔数 / 平均持仓,数据来源与口径见 `docs/STRATEGY_OPT_RECORDS.md`
- 次日涨跌停参考价:以信号日收盘价为基准,按板块 10%/20%/30% 与 0.01 tick 计算
- LLM 标签与置信分:自动读取该股票同日的历史分析日志(`final_signal_label` / `conviction_score`)
- 状态筛选:全部 / 计划中 / 持仓中 / 已卖出 / 已放弃(局部刷新,不重跑整页)
- 操作:填写实际买入日期/价格/数量确认买入,或放弃计划;买入价格默认填入信号日收盘价

### 持仓跟踪 tab

确认买入后自动进入本 tab:
- 展示买入日、买入价、最新价、盈亏比例、持有天数
- 状态规则:**持有中** / **建议到期**(达到计划持仓天数且非信号出场) / **止损预警 -5%** / **止盈预警 +8%** / **已卖出**
- A 股 T+1:买入当日禁止卖出,UI 给出醒目提示
- 可打开「显示已卖出持仓」查看历史已实现盈亏

### 交易记录与策略跟踪 tab

- 已平仓交易:买入日/买入价、卖出日/卖出价、已实现收益、持有交易日、卖出原因、命中策略
- 策略表现:每个命中策略的实盘次数、盈利次数、实盘胜率、平均收益,并与回测胜率对比
- 当前持仓的浮动收益一览
- 手动卖出表单记录卖出价、日期与原因(手动/止损/止盈/到期)

### 综合推荐 tab

4 列彩色卡片,按 Conflict Resolver 输出的标签分类:

| 🟢 强买 | 🟡 关注 | 🟠 冲突 | 🔴 弃 |
|---------|---------|---------|-------|
| 量化+LLM 双买入信号 | 量化买入但 LLM 谨慎 | 量化与 LLM 信号冲突 | 双负面或无信号 |

点击每只股票展开看完整推理链。

### 历史 tab

三类历史记录:
- 量化选股历史(每次 `pick()` 的 Top N + 命中策略)
- AI 分析历史(每只股票每次 LLM 分析的完整状态)
- 综合推荐历史(每次 Conflict Resolver 输出)

---

## CLI quant-pick 子命令

```bash
tradingagents quant-pick [OPTIONS]
```

选项:

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--today` | 系统日期 | 选股日期(YYYY-MM-DD) |
| `--top-n` | 20 | 返回 Top N 候选数 |
| `--workers` | 8 | multiprocessing.Pool 大小 |
| `--cache` | daily_main_board | 日线缓存名 |
| `--slice-days` | 0 | 切片天数(0=全历史) |
| `--top-k` | 2 | 每策略返回 top_k 只 |
| `--output-format` | terminal | terminal/json/csv/markdown |
| `--output-file` | - | 输出文件路径(不填则 stdout) |

示例:

```bash
# 默认参数,终端表格输出
tradingagents quant-pick --today 2026-07-20

# JSON 文件输出,Top 10
tradingagents quant-pick --today 2026-07-20 --top-n 10 --output-format json --output-file picks.json

# 全量主板(慢但覆盖广)
tradingagents quant-pick --cache daily_main_board --top-n 50
```

---

## 每日自动化 pipeline

`scripts/daily_pipeline.py` 是每日 cron 入口:

```bash
python scripts/daily_pipeline.py [OPTIONS]
```

选项:

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--today` | 系统日期 | 选股日期 |
| `--top-n` | 20 | 量化层返回 Top N |
| `--with-llm` | False | 启用 LLM 深度分析(对 Top N 逐只跑) |
| `--workers` | 8 | 量化层并行 worker 数 |
| `--cache` | daily_main_board | 日线缓存名 |
| `--output-dir` | outputs/daily | 输出目录 |
| `--llm-only-top` | 5 | LLM 只分析 Top N 中的前 K 只(节省 API quota) |

示例:

```bash
# 只跑量化层(默认)
python scripts/daily_pipeline.py --today 2026-07-20

# 跑完整 pipeline:量化 + 对 Top 5 逐只 LLM 分析
python scripts/daily_pipeline.py --today 2026-07-20 --with-llm --llm-only-top 5
```

输出落盘到 `outputs/daily/<YYYY-MM-DD>/`:

- `quant_picks.json` - 量化选股结果
- `analysis/<ticker>/full_states_log_<timestamp>.json` - 每只股票完整 LLM 状态
- `analysis/<ticker>/report.md` - 每只股票 Markdown 报告
- `recommendations.json` - 综合推荐汇总

cron 示例(每个交易日 18:00 跑):

```cron
0 18 * * 1-5 cd /path/to/TradingAgents-quant && python scripts/daily_pipeline.py --with-llm --llm-only-top 5 >> logs/cron.log 2>&1
```

---

## 配置说明

### LLM 配置

`tradingagents/default_config.py` 默认值:

```python
"llm_provider": "deepseek",
"deep_think_llm": "deepseek-v4-pro",      # 辩论/决策用
"quick_think_llm": "deepseek-v4-flash",   # 常规分析用
"backend_url": None,                       # None=用 provider 官方地址
"max_debate_rounds": 1,                    # Bull/Bear 辩论轮数
"max_risk_discuss_rounds": 1,              # 3 方风险辩论轮数
"output_language": "Chinese",              # 报告输出语言
"checkpoint_enabled": False,               # LangGraph checkpoint resume
```

Web UI 侧边栏只保留模型数据源(DeepSeek 官方 / OpenCode 中转)与 API Key 输入框;快速/深度模型已内置为 DeepSeek-V4-Flash / DeepSeek-V4-Pro;API Key 保存在本机 `~/.tradingagents/web_config.json`,重启后自动恢复;分析日期统一使用今天;量化参数(Top N=20、worker 数)由代码固定,不再暴露在侧边栏。API Key 或网关地址包含中文/空格时会被拦截并给出中文提示。

### 量化层配置

```python
"quant_layer_enabled": True,               # 总开关
"quant_daily_cache_name": "daily_main_board",  # 全量主板(默认,选股层过滤)
"quant_top_n_default": 20,                 # Top N 候选数(Web/CLI 固定 20)
"quant_n_workers": 8,                      # multiprocessing.Pool 大小
"quant_slice_days": 0,                     # 0=全历史
"quant_top_k_per_strategy": 2,             # 每策略返回 top_k 只
"quant_compare_llm_enabled": False,        # 副 LLM 对比(预留,未实现)
```

### 数据缓存

- 量化层日线缓存:`tradingagents/quant/outputs/cache/`(parquet 格式,~50MB)
- LangGraph checkpoint:`~/.tradingagents/cache/<ticker>/<date>/sqlite.db`
- 分析日志:`~/.tradingagents/logs/<ticker>/<date>/full_states_log_*.json`
- 综合推荐:`~/.tradingagents/recommendations/<date>/<ticker>.json`
- 量化选股结果:`~/.tradingagents/quant_picks/<date>.json`
- 买入计划/持仓:`~/.tradingagents/positions/plans.json`

---

## 项目结构

```
TradingAgents-quant/
├── tradingagents/
│   ├── __init__.py                    # __version__ = "0.4.0"
│   ├── default_config.py              # 默认配置
│   ├── agents/
│   │   ├── analysts/                  # 7 个 Analyst
│   │   ├── researchers/               # Bull/Bear 辩论
│   │   ├── risk_mgmt/                 # 3 方风险辩论
│   │   ├── trader/                    # Trader(A 股约束)
│   │   ├── managers/                  # Research Manager + Portfolio Manager
│   │   ├── quant_picker_node.py       # [v0.3.0] Quant Picker LangGraph 节点
│   │   ├── conflict_resolver.py       # [v0.3.0] Conflict Resolver 节点
│   │   └── quality_gate.py
│   ├── graph/                         # LangGraph 拓扑
│   │   ├── trading_graph.py           # TradingAgentsGraph 主入口
│   │   ├── setup.py                   # 节点连接
│   │   ├── propagation.py             # state 初始化
│   │   └── checkpointer.py            # SqliteSaver resume
│   ├── dataflows/                     # A 股数据 vendor
│   │   ├── a_stock.py                 # mootdx + 东财 + 新浪 + 同花顺 直连
│   │   ├── interface.py               # vendor dispatch
│   │   └── utils.py                   # safe_ticker_component
│   ├── quant/                         # [v0.3.0] 量化前置筛选层
│   │   ├── quant_picker.py            # pick() API
│   │   ├── sina_fetcher.py            # 全市场日线抓取
│   │   ├── data_update.py             # 增量更新
│   │   ├── config.py
│   │   ├── strategy/                  # top18 终态库(S=5/A=11/B=2)
│   │   │   ├── strategy_library_final.py  # 策略注册表
│   │   │   ├── optimization_records.py    # 18 策略 OOS 优化记录(固化数据)
│   │   │   ├── base.py                # BaseStrategy(ABC)
│   │   │   ├── market_filter.py       # MA(15,35) + MA(90) 双均线
│   │   │   └── *.py                   # 18 个策略实现
│   │   ├── features/                  # indicators / factors / pipeline
│   │   ├── backtest/                  # Signal 类 / Portfolio
│   │   ├── data/                      # cache / universe / st_filter
│   │   └── utils/                     # trading_calendar
│   └── llm_clients/                   # 9 个 provider LLM 工厂
├── web/                               # Streamlit Web UI
│   ├── app.py                         # 最小入口
│   ├── app_main.py                    # main 函数(避免 spawn 崩溃)
│   ├── launch.py                      # tradingagents-web 命令入口
│   ├── runner.py                      # 后台线程
│   ├── progress.py                    # ProgressTracker
│   ├── history.py                     # 历史 save/load
│   ├── pdf_export.py                  # PDF/Markdown 导出
│   ├── position_store.py               # 买入计划/持仓跟踪持久化
│   ├── user_config.py                  # 模型数据源/API Key 本机持久化
│   └── components/                    # UI 组件
│       ├── sidebar.py                  # 侧边栏
│       ├── progress_panel.py           # 实时进度
│       ├── report_viewer.py            # AI 报告展示
│       ├── quant_pick.py               # Top N 表格 + 计划买入
│       ├── buy_plan.py                 # 买入计划详情
│       ├── position_tracker.py         # 持仓跟踪
│       ├── trade_tracker.py            # 交易记录与策略跟踪
│       └── recommendation.py           # 4 档推荐
├── cli/
│   ├── main.py                        # Typer 主入口
│   └── quant_pick.py                  # [v0.3.0] quant-pick 子命令
├── scripts/
│   ├── daily_pipeline.py              # [v0.3.0] 每日 cron 入口
│   ├── incremental_update.py          # 增量数据更新
│   ├── precompute_features.py         # 性能预热
│   ├── smoke_structured_output.py     # 多 provider smoke 测试
│   └── archive/                       # 一次性调试脚本(保留作历史参考)
├── tests/                             # pytest 测试套件
├── docs/
│   ├── USAGE.md                       # 本文档
│   ├── STRATEGY_OPT_RECORDS.md        # 18 策略 OOS 优化记录
│   ├── LLM_PIPELINE_EVAL.md           # LLM 推荐 vs 后续股价评估
│   ├── PERFORMANCE.md                 # 量化层性能基准
│   └── INTEGRATION_PLAN_v0.3.0.md     # 整合计划(归档)
├── pyproject.toml
├── requirements.txt
├── requirements-quant.txt             # 量化层独立依赖
├── CHANGELOG.md
├── README.md
├── CLAUDE.md                          # 项目级 AI 助手指令
├── NOTICE                             # 归属声明
└── LICENSE                            # Apache 2.0
```

---

## 常见问题

### Q1: 为什么选股有时慢,有时快?

18 个策略 × ~3042 股全历史日线。v0.4.0 起默认开启 universe-prune:主进程先算好
各策略的 top 300/500 股票池,worker 只为该池计算特征,跳过全市场 60-90s 预热。
本机实测(3042 股缓存、8 workers):
- 冷缓存(无 universe 缓存/无事件池缓存):**69s**,18 策略 0 错误
- 热缓存(universe 持久化 + 事件池命中):**37s**
- 关闭优化回退旧路径(4 workers):358s
- 三组 Top 10 与 all_records 行数完全一致

优化手段:
- `quant_universe_prune=true`:规则策略按 top 300/500 universe 裁剪
- FC 因子策略单独全市场 Pool,规则策略 worker 不保留全量日线
- `quant_universe_cache=true`:universe 代码列表按数据指纹持久化
- `build_features_vectorized(columns=...)`:top18 规则策略按需特征列,top500 日线特征从 5.8s 降到约 1s
- 可选 `daily_main_board_liquid` 缓存(流动性前 80%,~2433 股),但会牺牲覆盖范围
- 调高 `--workers`(但 Windows spawn 启动慢,>8 收益递减)
- 用 `scripts/precompute_features.py` 预热周线/月线缓存

### Q2: 为什么 LLM 分析有时返回中文股票名而非代码?

deepseek-v4-flash 等模型在 tool call 时偶尔返回中文名。`safe_ticker_component` 已加兜底自动转码,但不同模型表现仍有差异。建议:
- 用 deepseek-v4-pro 做决策(更稳)
- 在 Web UI 侧栏手动切到 pro 模型

### Q3: 批量并行分析时 API quota 不够怎么办?

- 调小 `--llm-only-top`(daily_pipeline)
- 在 Web UI 勾选少量股票(5-10 只)
- 切换到更便宜的 provider(如 DeepSeek Flash)
- 关闭辩论:`config["max_debate_rounds"] = 0`

### Q4: Conflict Resolver 标签规则?

| 量化层 | LLM | 标签 | 含义 |
|--------|-----|------|------|
| 命中 | Buy/Overweight | 🟢 强买 | 双重买入信号共振 |
| 命中 | Hold | 🟡 关注 | 量化看好但 LLM 谨慎 |
| 命中 | Sell/Underweight | 🟠 冲突 | 量化与 LLM 信号冲突,需用户判断 |
| 未命中 | Buy/Overweight | 🟡 关注 | LLM 独立信号,无量化共振 |
| 未命中 | Sell/Underweight | 🔴 弃 | 双重负面信号 |
| 未命中 | Hold | 🔴 弃 | 无买入信号 |

### Q5: 弃用的 36 个策略还能恢复吗?

不能(已物理删除)。如需恢复,从上游 `stock_pick_live` 项目重新迁入:
- 复制 `stock_pick_live/strategy/<name>.py` 到 `tradingagents/quant/strategy/`
- 修 import 路径(`from backtest.engine import` -> `from tradingagents.quant.backtest.engine import` 等)
- 在 `strategy_library_final.py` 加策略定义

弃用原因详见 `docs/INTEGRATION_PLAN_v0.3.0.md` 第 5 节或 `CHANGELOG.md` v0.3.0 Refined 段。

### Q6: Windows 上 multiprocessing 报错?

Windows 默认 spawn context,worker 会重新 import `__main__`。本项目通过 `web/app.py` 的 `if __name__ == "__main__":` guard 防止 worker 重复执行 streamlit 代码。如果自定义脚本遇到类似问题,确保入口文件有同样 guard。

### Q7: 东财接口被封 IP 怎么办?

`a_stock.py` 已对东财请求统一走 `_em_get()` 节流(默认间隔 1.0s + 0.1-0.5s 抖动)。批量场景可设 `EM_MIN_INTERVAL=1.5~2` 进一步降速。mootdx/腾讯/新浪/同花顺 不受影响。

### Q8: 如何添加自定义策略?

1. 在 `tradingagents/quant/strategy/` 新建 `<your_strategy>.py`,继承 `BaseStrategy`,实现 `generate_signals(daily_df, current_date, portfolio, top_k)`
2. 在 `strategy_library_final.py` 加策略定义,指定 `module` / `class` / `params` / `engine_params` / `tier`
3. 在 `NEW_TIERS_FINAL` 对应 tier 列表加入策略名
4. 跑 `python -m pytest tests/test_signal_processing.py` 验证

### Q9: 如何禁用量化层只跑 LLM?

```python
config = DEFAULT_CONFIG.copy()
config["quant_layer_enabled"] = False
graph = TradingAgentsGraph(config=config)
graph.propagate("300750", "2026-07-20")
```

Web UI 侧栏暂无开关,需手改 `_build_config()`(在 `web/app_main.py:50`)。

### Q10: 数据缓存怎么清理?

```bash
# 量化层日线缓存
rm -rf tradingagents/quant/outputs/cache/

# LangGraph checkpoint
rm -rf ~/.tradingagents/cache/

# 分析日志
rm -rf ~/.tradingagents/logs/

# 量化选股历史
rm -rf ~/.tradingagents/quant_picks/
```

---

## 相关文档

- `docs/STRATEGY_OPT_RECORDS.md` — 18 策略 OOS 优化记录总表及口径说明
- `docs/LLM_PIPELINE_EVAL.md` — LLM 推荐与后续股价变化的分档评估
- `docs/PERFORMANCE.md` — 量化层性能基准与调优手段
- `AUDIT_REPORT.md` — 发布前全面审计
- `CHANGELOG.md` — 版本变更记录

## 许可证

Apache License 2.0 - 详见 [LICENSE](../LICENSE) 与 [NOTICE](../NOTICE)

## 免责声明

⚠️ 本项目仅供学习研究与技术演示,不构成任何投资建议。投资决策请咨询持牌专业机构。
