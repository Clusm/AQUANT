<h1 align="center">Aquant 投研工具</h1>

<p align="center">
  本项目基于 <a href="https://github.com/TauricResearch/TradingAgents">TauricResearch/TradingAgents</a> 改造而来:<br>
  → 增加 A 股数据层、7 分析师、量化前置筛选层，形成 <b>TradingAgents-quant</b>(v0.4.0)。
</p>

<p align="center">
  <b>量化前置筛选 + LLM 多 Agent 深度分析</b>双层架构<br>
  全 Apache 2.0 开源 · pip install 即跑 · 零外部服务依赖
</p>

<p align="center">
  <b>⚠️ 免责声明：本项目仅供学习研究与技术演示，不构成任何投资建议。投资决策请咨询持牌专业机构。</b>
</p>

<p align="center">
  <a href="https://github.com/Clusm/AQUANT/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Clusm/AQUANT?style=social"/></a>
  <a href="https://github.com/Clusm/AQUANT/network/members"><img alt="Forks" src="https://img.shields.io/github/forks/Clusm/AQUANT?style=social"/></a>
  <a href="https://arxiv.org/abs/2412.20138"><img alt="论文" src="https://img.shields.io/badge/论文-arXiv_2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue"/></a>
  <a href="./CHANGES_FROM_UPSTREAM.md"><img alt="改动记录" src="https://img.shields.io/badge/改动记录-CHANGES-orange"/></a>
</p>

---

## 目录

- [为什么做这个 Fork](#为什么做这个-fork)
- [与上游对比](#与上游对比)
- [架构概览](#架构概览)
- [7 个 Analyst 角色](#7-个-analyst-角色)
- [数据源](#数据源)
- [快速开始](#快速开始)
- [Web UI](#web-ui)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [致谢](#致谢)
- [许可证](#许可证)

---

## 项目溯源

本项目基于 TauricResearch/TradingAgents 改造而来，继承并扩展了其多 Agent 辩论框架：

```
TauricResearch/TradingAgents (65K ⭐ 原版)
  ├─ 多 Agent 辩论框架(LangGraph 拓扑)
  ├─ 4 Analyst + Bull/Bear + Risk Panel + Portfolio Manager
  └─ yfinance + Alpha Vantage 数据层
       │
       ↓ TradingAgents-quant 扩展
       │
TradingAgents-quant (v0.4.0,本项目)
  ├─ A 股数据层重写:mootdx + 东财 + 新浪 + 同花顺(直连 HTTP)
  ├─ 7 Analyst(新增 政策/游资/解禁 3 个 A 股特化角色)
  ├─ A 股辩论/交易约束(T+1、涨跌停、手数、ST)
  ├─ 量化前置筛选层(tradingagents/quant/,18 策略)
  ├─ LangGraph 拓扑扩展:START -> Quant Picker -> ... -> Conflict Resolver -> END
  ├─ Web UI 7-tab 重构
  ├─ CLI quant-pick 子命令 + daily_pipeline 脚本
  ├─ Conflict Resolver 节点(🟢强买/🟡关注/🟠冲突/🔴弃)
  └─ 默认 LLM 改用 DeepSeek(quick=Flash / deep=Pro)
```

原版 TauricResearch/TradingAgents 与本项目均采用 Apache 2.0 开源，完整归属声明见 [NOTICE](./NOTICE)。

## 为什么做这个 Fork

原版 TradingAgents 是一个出色的多 Agent 投研框架,但它针对美股设计:数据走 Yahoo Finance / Alpha Vantage,分析师不懂 A 股制度,辩论和决策完全面向美股市场。

**TradingAgents-quant 的目标**:在 A 股深度特化(数据层 / 7 Analyst / 辩论层 / Trader / Web UI)的基础上,于 LLM 多 Agent 流水线之前插入量化前置筛选层,形成"量化广度扫描 + LLM 深度分析"双层架构。量化层提供基于历史回测的确定性信号锚,LLM 层提供语义理解和综合判断,Conflict Resolver 节点据此给最终推荐打 4 档标签。

### 核心改造

| 维度 | 原版 | 本 Fork |
|------|------|---------|
| **数据源** | Yahoo Finance / Alpha Vantage | mootdx + 东财 + 新浪 + 同花顺（全免费直连） |
| **Analyst 角色** | 4 个（市场/情绪/新闻/基本面） | **7 个**（+政策分析师/游资追踪/解禁监控） |
| **交易规则** | 美股（T+0、无涨跌停） | A 股（T+1、涨跌停、最小手数、交易时段） |
| **输出语言** | 英文 | 中文报告（内部辩论保持英文以保证推理质量） |
| **Alpha 基准** | SPY | 沪深 300（CSI 300） |

---

## 与上游对比

| 特性 | 原版 TradingAgents | **本 Fork** |
|------|-------------------|-------------|
| 许可证 | Apache 2.0 | **全 Apache 2.0** |
| 部署依赖 | pip install | **开箱即用** |
| A 股数据 | ❌ | **mootdx + 东财 + 新浪 + 同花顺（直连 HTTP）** |
| A 股特化角色 | ❌ | **政策/游资/解禁 3 个深度角色** |
| A 股交易约束 | ❌ | **T+1/涨跌停/手数/ST 全覆盖** |

---

## 量化前置筛选层(v0.3.0 新增,v0.4.0 升级 top18 终态库)

v0.3.0 在原 LLM 多 Agent 流水线**之前**插入量化前置筛选层,形成"量化广度扫描 + LLM 深度分析"双层架构。v0.4.0 将策略库升级为 2026-08-16 top18 终态库(24 家族去底部 6,来源 stock_selector 审计):

```
[用户输入日期 + Top N]
    ↓
📊 量化层(tradingagents/quant/,multiprocessing.Pool)
    ├─ daily_main_board cache(全量主板 ~3042 股,选股层做价格/涨跌停/流动性过滤)
    ├─ 策略并行: S=5 / A=11 / B=2(共 18 个有效策略)
    ├─ 加权分 = sum(strategy_composite_score)
    └─ 产出: Top N 候选 + 命中策略 + 胜率 + 入场建议
    ↓
[用户勾选要深度分析的股票]
    ↓
🤖 LLM 层(原 TradingAgents 7 Analyst + 辩论 + PM)
    ├─ Quant Picker 节点(确定性计算,把量化上下文写入 state)
    ├─ 7 Analyst(prompt 注入 quant_pick_context)
    ├─ Bull vs Bear 辩论
    ├─ Research Manager(deep LLM)
    ├─ Trader(A 股约束)
    ├─ 3 方风险辩论
    └─ Portfolio Manager(deep LLM,Buy/Hold/Sell + 仓位)
    ↓
🎯 Conflict Resolver 节点(纯规则,无 LLM)
    合并: 量化分 + LLM 决策 + 命中策略质量
    标签: 🟢 强买 / 🟡 关注 / 🟠 冲突 / 🔴 弃
```

**为什么需要量化前置层?**

- **降低 LLM 调用成本**: 量化层在全量主板中扫描选出 Top N 后再让 LLM 深度分析,避免对 3000+ 股票逐只调 LLM(成本和时延都不可接受)
- **提供确定性信号锚**: LLM 输出有随机性,量化层基于历史回测的胜率/持仓天数给 LLM 一个确定性参考,Conflict Resolver 节点据此给最终推荐打标签
- **多时间框架覆盖**: 量化策略涵盖日线/周线/月线/季度多时间框架,弥补 LLM 主要看新闻和基本面的盲点

---

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    7 Analyst 研报生成                      │
│  Market → Social → News → Fundamentals                   │
│  → Policy → Hot Money → Lockup                           │
│         （每个 Analyst 带工具循环）                          │
├─────────────────────────────────────────────────────────┤
│               Bull vs Bear 投研辩论                       │
│         Bull Researcher ←→ Bear Researcher               │
│               （最多 N 轮辩论）                             │
├─────────────────────────────────────────────────────────┤
│              Research Manager 综合研判                     │
│         （深度思考 LLM，输出投资计划）                       │
├─────────────────────────────────────────────────────────┤
│                  Trader 交易方案                          │
│         （A 股约束：T+1/涨跌停/手数）                       │
├─────────────────────────────────────────────────────────┤
│        Aggressive ←→ Conservative ←→ Neutral             │
│               三方风险辩论                                 │
├─────────────────────────────────────────────────────────┤
│            Portfolio Manager 最终决策                      │
│     （深度思考 LLM，输出 Buy/Hold/Sell + 仓位）             │
└─────────────────────────────────────────────────────────┘
```

**双 LLM 设计**：
- `quick_think_llm`：所有 Analyst、Researcher、Trader、Risk Debater
- `deep_think_llm`：Research Manager 和 Portfolio Manager（需要综合全局信息做决策）

---

## 7 个 Analyst 角色

### 原版 4 角色（A 股适配）

| 角色 | 职责 | 数据工具 |
|------|------|---------|
| 🏪 市场分析师 | K 线形态、技术指标、量价分析 | `get_stock_data`, `get_indicators` |
| 💬 舆情分析师 | 社交媒体情绪、散户讨论热度 | `get_news` |
| 📰 新闻分析师 | 行业新闻、公告、宏观事件 | `get_news`, `get_global_news`, `get_insider_transactions` |
| 📊 基本面分析师 | 财报三表、盈利能力、估值 | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` |

### A 股特化 3 角色（新增）

| 角色 | 职责 | 数据工具 | 为什么需要 |
|------|------|---------|-----------|
| 🏛️ 政策分析师 | 监管政策、产业政策、窗口指导 | `get_news`, `get_global_news` | A 股是政策市，政策变化直接影响板块轮动 |
| 🔥 游资追踪师 | 龙虎榜、大单流向、主力资金动态 | `get_stock_data`, `get_news`, `get_insider_transactions` | 游资是 A 股短线定价的核心力量 |
| 🔓 解禁监控师 | 限售股解禁、大股东减持、股权质押 | `get_insider_transactions`, `get_news`, `get_fundamentals` | 解禁是 A 股特有的重大供给冲击因素 |

所有 7 个 Analyst 的报告会流入后续的 Bull/Bear 辩论和三方风险辩论，确保 A 股特色因素贯穿整条决策链。

---

## 数据源

全部免费，无需 API Key，无积分墙：

| 来源 | 协议 | 提供内容 |
|------|------|---------|
| **mootdx** | TCP 7709 | OHLCV K 线、财务快照、F10 文本 |
| **腾讯财经** | HTTP (`qt.gtimg.cn`) | PE / PB / 市值 / 换手率（实时） |
| **东方财富** | HTTP (datacenter / push2) | 龙虎榜、限售解禁、板块行情、个股信息 |
| **新浪财经** | HTTP | K 线历史、财报三表 |
| **同花顺** | HTTP (10jqka) | EPS 一致预期 |
| **财联社** | HTTP (cls.cn) | 全球财经快讯 |
| **百度股市通** | HTTP (finance.pae.baidu) | 概念板块分类、资金流向 |

> 完全不依赖 Tushare（积分墙）、Alpha Vantage（海外 API）、Yahoo Finance（不支持 A 股）。

---

> **数据源优先级 & 东财防封（v0.2.11）**：行情 / K线 / 市值 / 财务能从 mootdx（通达信 TCP，不封 IP）或腾讯拿到的，一律走它们；东财只用于它独有的数据（龙虎榜 / 解禁 / 资金流 / 板块 / 个股新闻等）。所有东财请求统一走内置节流入口 `_em_get()`：串行限流（默认间隔 ≥1s + 0.1~0.5s 随机抖动）+ 复用 Keep-Alive 会话，多 Agent 跑批量分析不再触发临时封 IP（东财风控实测：每秒 >5 / 并发 ≥10 / 1 分钟 ≥200 触发封禁）。批量场景可设环境变量 `EM_MIN_INTERVAL=1.5~2` 进一步降速。**仅东财限流，mootdx / 腾讯 / 新浪 / 同花顺 / 财联社 / 百度 不受影响。**

## 快速开始

### 1. 环境准备

```bash
# Python >= 3.10
git clone https://github.com/Clusm/AQUANT.git
cd AQUANT
pip install -e .

# 如需使用 Google Gemini 模型（可选）：
pip install -e ".[google]"

# 如希望全市场代码优先走 adata（可选，未装时自动回退 akshare）：
pip install -e ".[quant-data]"
```

> **装完即可用，无需 Docker。** 安装后直接跑 `streamlit run web/app.py`（Web UI）或 `tradingagents`（CLI）即可，详见下方「Web UI」「CLI 方式」两节。Docker 仅是可选的部署方式，本地开发不需要。

### 2. 配置 LLM

> **必须使用 API Key**，不能用 Claude/ChatGPT 订阅版。每次分析需 30-50 次 LLM 调用，只有 API 模式支持。

在项目根目录创建 `.env` 文件，按你选择的供应商配置：

```bash
# ── 方案 A：DeepSeek（v0.4.0 默认，推荐）────────────────
DEEPSEEK_API_KEY=sk-xxx
# 申请地址：https://platform.deepseek.com/

# ── 方案 B：MiniMax（国内直连备选）────────────────────
MINIMAX_API_KEY=sk-xxx
# 申请地址：https://platform.minimaxi.com/

# ── 方案 C：智谱 GLM ─────────────────────────────────
ZHIPU_API_KEY=xxx
# 申请地址：https://open.bigmodel.cn/

# ── 方案 D：通义千问 Qwen ────────────────────────────
DASHSCOPE_API_KEY=sk-xxx
# 申请地址：https://dashscope.console.aliyun.com/

# ── 方案 E：OpenAI ───────────────────────────────────
OPENAI_API_KEY=sk-xxx

# ── 方案 F：Anthropic ────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-xxx

# ── 方案 G：Kimi（Anthropic 兼容 API）────────────────
ANTHROPIC_AUTH_TOKEN=your-kimi-token
```

### 3. 运行分析

根据你选择的供应商修改 config：

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph

# ── DeepSeek 示例（v0.4.0 默认，推荐）──────────────
config = {
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",        # Research Manager + Portfolio Manager
    "quick_think_llm": "deepseek-v4-flash",     # 7 Analyst + Researcher + Trader + Risk
    "output_language": "Chinese",
}

# ── MiniMax 示例 ───────────────────────────────────
# config = {
#     "llm_provider": "minimax",
#     "deep_think_llm": "MiniMax-M2.7",
#     "quick_think_llm": "MiniMax-M2.7-highspeed",
#     "output_language": "Chinese",
# }

# ── Anthropic + Kimi 示例 ───────────────────────────
# config = {
#     "llm_provider": "anthropic",
#     "deep_think_llm": "claude-sonnet-4-6",
#     "quick_think_llm": "claude-sonnet-4-6",
#     "backend_url": "https://api.kimi.com/coding/",
#     "output_language": "Chinese",
# }

ta = TradingAgentsGraph(debug=True, config=config)
final_state, decision = ta.propagate("688017", "2026-05-12")
print(decision)
```

### 4. CLI 方式

```bash
tradingagents            # 交互式 CLI(单股 LLM 深度分析)
tradingagents --help     # 查看所有选项

# v0.3.0 量化层独立子命令(不需 LLM key,跑全市场策略扫描)
tradingagents quant-pick --today 2026-07-19 --top-n 10 --workers 8 --cache daily_main_board --output-format json
tradingagents quant-pick --help   # 查看所有量化选项
```

### 5. 每日自动化 Pipeline(v0.3.0 新增)

`scripts/daily_pipeline.py` 是每日 cron 入口,默认只跑量化层,`--with-llm` 启用 LLM 深度分析:

```bash
# 仅量化层(冷缓存约 70s,热缓存约 37s,不需 LLM key)
python scripts/daily_pipeline.py --today 2026-07-19 --top-n 10 --workers 8

# 量化层 + LLM 深度分析(对 Top N 逐只调 LangGraph,需 LLM key)
python scripts/daily_pipeline.py --today 2026-07-19 --top-n 5 --with-llm     --llm-provider deepseek --tickers 600428,000739,603669

# 落盘位置:outputs/daily/<YYYY-MM-DD>/
#   quant/top_picks.json       - 量化层 Top N 结果
#   llm/<ticker>/full_state.json - 每只股票 LLM 完整 state
#   llm/<ticker>/complete_report.md - 每只股票完整报告
```

---

## Web UI

内置 Streamlit 可视化界面(v0.3.0 改名 **Aquant 投研工具**),7 tab 布局,覆盖"量化选股 → AI 深度分析 → 买入计划 → 持仓跟踪 → 交易记录与策略跟踪 → 综合推荐 → 历史"完整工作流,适合不写代码的用户。

### 启动

```bash
# 方式一:命令行启动(推荐)
tradingagents-web

# 方式二:直接运行(开发期推荐,绕过 launch.py)
streamlit run web/app.py
```

打开浏览器访问 `http://localhost:8501`。

### 功能

- **7 Tab 布局**:量化选股 / AI 深度分析 / 买入计划 / 持仓跟踪 / 交易记录 / 综合推荐 / 历史(统一细线矢量图标,非 emoji)
- **量化层集成**:量化选股 tab 跑量化策略 multiprocessing.Pool,产出 Top N 候选,支持全选/中文名/命中策略详情折叠
- **买入计划**:Top N 表每行一键「计划买入」,持久化命中策略、出场规则(信号出场 / 固定持仓 / ATR 参考)、策略优化记录(OOS累计收益/回撤/Sharpe/盈亏比/笔数/平均持仓)、次日涨跌停参考价,并自动关联 LLM 历史标签;支持按 计划中/持仓中/已卖出/已放弃 筛选
- **持仓跟踪**:确认买入后进入 📈 tab,展示买入价/最新价/盈亏/持有天数,自动提示 T+1、-5% 止损预警、+8% 止盈预警、建议到期;可切换显示已卖出持仓
- **交易记录与策略跟踪**:已平仓交易自动汇总实盘收益/胜率,并按命中策略统计实盘 vs 回测胜率、平均收益
- **综合推荐**:🎯 tab 按 🟢强买/🟡关注/🟠冲突/🔴弃 四档分类展示(Conflict Resolver 节点)
- **模型配置极简**:侧边栏仅 DeepSeek 官方 / OpenCode 中转两条数据源 + API Key 密码输入框;快速/深度模型已内置为 DeepSeek-V4-Flash / DeepSeek-V4-Pro;API Key 自动保存到本机 `~/.tradingagents/web_config.json`,重启无需重填;Top N 固定 20,worker 数固定 8
- **一键分析**:输入 6 位 A 股代码或中文名,点击「开始分析」;分析日期自动使用今天
- **实时进度**:12 阶段 pipeline 实时显示(7 分析师 -> 质量门控 -> 辩论 -> 风控 -> 决策)
- **完整报告**:信号卡片(Buy/Hold/Sell)、7 份分析师报告、多空辩论、风控评估、量化上下文
- **报告导出**:一键下载 **Markdown**(零依赖) 或 **PDF**(自动适配 Windows/macOS/Linux 中文字体),含量化上下文段
- **历史记录**:自动保存并展示 AI 分析/量化选股/综合推荐三类历史
- **断点续跑**:LangGraph checkpoint resume,崩溃后从最后一个成功节点恢复

### 截图

> Web UI 欢迎页截图待补;启动后访问 http://localhost:8501 即可看到 7-tab 工作流(量化选股 / AI 深度分析 / 买入计划 / 持仓跟踪 / 交易记录 / 综合推荐 / 历史,统一矢量图标)。

---

## 配置说明

所有配置通过 `config` 字典传入，完整选项：

### LLM 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `llm_provider` | `"deepseek"` | LLM 提供商。Web UI 固定 `deepseek`,仅保留 **DeepSeek 官方** 与 **OpenCode 中转** 两条数据源 |
| `deep_think_llm` | `"deepseek-v4-pro"` | Research Manager + Portfolio Manager 用的模型 |
| `quick_think_llm` | `"deepseek-v4-flash"` | 所有 Analyst / Researcher / Trader 用的模型 |
| `backend_url` | `None` | OpenCode 中转网关地址。Web UI 侧边栏填写,或读 `.env` 的 `BACKEND_URL`;DeepSeek 官方模式自动留空 |
| `llm_api_key` | `None` | API Key。Web UI 模型配置区密码输入框填写,持久化在 `~/.tradingagents/web_config.json`,重启自动恢复;未填时回退 `DEEPSEEK_API_KEY` 环境变量 |
| `output_language` | `"Chinese"` | 报告输出语言(内部辩论始终英文) |
| `max_debate_rounds` | `1` | Bull vs Bear 辩论轮数 |
| `max_risk_discuss_rounds` | `1` | 风险三方辩论轮数 |
| `data_vendors` | 全部 `"a_stock"` | 数据供应商路由 |
| `checkpoint_enabled` | `False` | 启用 SQLite 断点续跑 |
| `memory_log_max_entries` | `None` | 交易记忆最大条目数 |

### 量化层配置(v0.3.0 新增)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `quant_layer_enabled` | `True` | 启用量化前置筛选层。False 则跳过 Quant Picker 节点 |
| `quant_daily_cache_name` | `"daily_main_board"` | 日线缓存名。`daily_main_board`=全量主板(~3042 股,默认,价格/涨跌停/流动性过滤在选股层执行);`daily_main_board_liquid`=流动性前 80%(~2433 股,数据采集层已截断) |
| `quant_top_n_default` | `20` | 量化层返回 Top N 候选数。Web/CLI 统一固定为 20;`pick()` 仍允许 5/10/20 |
| `quant_price_min` | `3.0` | 候选股最低股价,过滤低价/退市风险集中区 |
| `quant_price_max` | `70.0` | 候选股最高股价(5 万以内建议手动设为 50;当前默认 70 控制一手成本) |
| `quant_exclude_limit_up_down` | `True` | 当日收盘处于涨停价或跌停价的股票不进入候选池(按板块自动识别 10%/20% 幅度) |
| `quant_liquidity_percentile` | `0.8` | legacy 回退值,仅 `filter_universe_topk(topk=None)` 读取。top18 终态策略显式使用 top 300/500,不受此值影响 |
| `quant_n_workers` | `8` | multiprocessing.Pool worker 数 |
| `quant_slice_days` | `0` | 策略数据切片天数。0=全量历史(周月季策略 EMA 路径依赖,必须全量) |
| `quant_top_k_per_strategy` | `2` | 每个策略最多返回 top_k 只股票 |
| `quant_compare_llm_enabled` | `False` | 双 LLM 对比(v0.3.0 暂不实现,字段保留) |

**量化层环境变量**:
- `QUANT_CACHE_DIR`: 覆盖默认 `tradingagents/quant/outputs/cache/`,开发期可指向 `stock_pick_live/outputs/cache/` 共享数据

> 量化层性能优化、基准数据与调优建议见 **[docs/PERFORMANCE.md](./docs/PERFORMANCE.md)**。
> 18 个策略的 OOS 优化记录见 **[docs/STRATEGY_OPT_RECORDS.md](./docs/STRATEGY_OPT_RECORDS.md)**;LLM 管线评估见 **[docs/LLM_PIPELINE_EVAL.md](./docs/LLM_PIPELINE_EVAL.md)**。

---

## 常见问题排错

**Q: 用 DeepSeek/通义/智谱，却报 `OpenAIError: The api_key client option must be set ... OPENAI_API_KEY`？**
每个供应商用**各自的环境变量**，不是 OPENAI_API_KEY：DeepSeek=`DEEPSEEK_API_KEY`、通义=`DASHSCOPE_API_KEY`、智谱=`ZHIPU_API_KEY`、MiniMax=`MINIMAX_API_KEY`、xAI=`XAI_API_KEY`、OpenRouter=`OPENROUTER_API_KEY`。在项目根目录 `.env` 里设置对应变量后**重启**程序。（v0.2.12 起缺 key 会直接提示该用哪个变量名。）

**Q: 导出 PDF 报 `UnicodeEncodeError: 'latin-1' codec can't encode`？**
你的环境里装了**旧版 `fpdf`（pyfpdf）**，它和本项目用的 `fpdf2` 都以 `fpdf` 名称导入、互相冲突。执行：`pip uninstall -y fpdf && pip install "fpdf2>=2.8.6"`。实在不行可改用「下载 Markdown」导出（零依赖，永远可用）。

**Q: Docker 里导出 PDF 报「未找到中文字体」？**
v0.2.12 起 Dockerfile 已内置 `fonts-noto-cjk`，重新 `docker build` 即可。旧镜像可临时 `apt install fonts-noto-cjk`，或改用 Markdown 导出。

**Q: Docker 启动报 `[Errno 13] Permission denied: /home/appuser/.tradingagents/cache`？**
旧版镜像里没预建数据目录，`docker-compose` 的命名卷挂上来时被 Docker 建成 `root` 属主，而容器内进程以 `appuser` 运行、写不进去。v0.2.14 起 Dockerfile 已预建 `/home/appuser/.tradingagents`（cache/logs/memory）并归属 appuser，命名卷会继承该属主。**升级方式**：`git pull` 后 `docker compose build --no-cache` 重建镜像；若想保留旧数据卷可先 `docker run --rm -v tradingagents_data:/d alpine chown -R 1000:1000 /d` 修正属主，否则 `docker volume rm tradingagents_data` 后重建即可。

**Q: 部分分析师报告（情绪/新闻/基本面/政策/游资/解禁）空白不显示？**
这些报告由对应 Analyst 调用数据工具后生成，**空报告会被自动跳过不显示**。数据源本身是健康的（腾讯/mootdx/同花顺/东财实测出数）；报告为空通常是**所选模型 tool-call 能力弱**（如部分 deepseek/minimax 轻量模型不稳定地调用工具）。建议换用 tool-call 更稳的模型（deepseek-chat / 通义 / GLM-4 / Claude / GPT 等），或重试。

**Q: 装 `[google]`（Gemini）后 pip 报 httpx 冲突：mootdx 要 `httpx<0.26`、google-genai 要 `httpx>=0.28`？**
先澄清：**litellm / mcp 不是本项目的依赖**——报错里若提到它们，是你环境里其它包带来的，与 TradingAgents 无关。本项目核心安装（`pip install -e .`）不依赖 httpx≥0.28，**默认不冲突**；冲突只在装 `[google]` 用 Gemini 时出现（mootdx 与 google-genai 的 httpx 上下限互斥）。解法：① **mootdx 取行情走 TCP 协议、运行时根本不调用 httpx**，可让 httpx 升到满足 google-genai 的版本，pip 那条 `incompatible` 只是警告、不影响 mootdx 运行（实测 mootdx 0.11.7 在 httpx 0.28.1 下取数正常）；② 或把跑 Gemini 的环境与 mootdx 数据层分到不同 venv；③ 最省心是用 MiniMax / DeepSeek / 通义等国内直连模型，不装 `[google]` 就没这问题。

**Q: 不进 CLI 交互，怎么批量跑多只标的、拿到和 CLI 一样的完整报告？**
看 `examples/run_cases.py`：它复用 CLI 的 `save_report_to_disk()`，每只标的输出与 CLI 一致的 `complete_report.md`（分析师 / 研究 / 交易 / 风险 / 组合五个分区）+ 一份字段齐全的 `summary.json`。用法：`uv run python examples/run_cases.py`（跑全部）或 `uv run python examples/run_cases.py 688017`（单只）；改 `build_config()` 切换 provider/model。

---

## 项目结构

```
TradingAgents-quant/                          # v0.3.0 改名(原 TradingAgents-Astock)
├── tradingagents/
│   ├── agents/
│   │   ├── analysts/                          # 7 个分析师(中文 prompt + A 股框架)
│   │   │   ├── market_analyst.py
│   │   │   ├── social_media_analyst.py
│   │   │   ├── news_analyst.py
│   │   │   ├── fundamentals_analyst.py
│   │   │   ├── policy_analyst.py              # A 股特化
│   │   │   ├── hot_money_tracker.py           # A 股特化
│   │   │   └── lockup_watcher.py              # A 股特化
│   │   ├── researchers/                       # Bull / Bear 研究员
│   │   ├── risk_mgmt/                         # 激进 / 保守 / 中立 辩手
│   │   ├── managers/                          # Research Manager + Portfolio Manager
│   │   ├── trader/                            # Trader(A 股交易约束)
│   │   ├── quant_picker_node.py               # v0.3.0 LangGraph 量化节点
│   │   ├── conflict_resolver.py               # v0.3.0 冲突解决节点(纯规则)
│   │   └── utils/                             # 状态定义、工具函数
│   ├── quant/                                 # v0.3.0 引入,v0.4.0 top18 终态库
│   │   ├── quant_picker.py                    # pick() 主入口
│   │   ├── sina_fetcher.py                    # 全市场日线抓取
│   │   ├── data_update.py                     # 增量数据更新
│   │   ├── config.py                          # 量化层配置
│   │   ├── strategy/                          # 量化策略库
│   │   │   ├── strategy_library_final.py      # top18 终态库:S=5/A=11/B=2
│   │   │   ├── optimization_records.py        # 18 策略 OOS 优化记录(固化数据)
│   │   │   ├── base.py                        # BaseStrategy(ABC)
│   │   │   └── *.py                           # 策略实现
│   │   ├── features/                          # indicators / factors / pipeline
│   │   ├── backtest/                          # Signal 类 / Portfolio
│   │   ├── data/                              # cache / universe / st_filter
│   │   └── utils/                             # trading_calendar
│   ├── dataflows/
│   │   ├── a_stock.py                         # A 股数据 vendor(直连 HTTP API)
│   │   ├── interface.py                       # 数据接口抽象层
│   │   └── ...
│   └── graph/
│       ├── trading_graph.py                   # 主入口:TradingAgentsGraph
│       ├── setup.py                           # LangGraph 拓扑(含 Quant Picker + Conflict Resolver)
│       ├── propagation.py                     # 状态初始化与传播
│       ├── reflection.py                      # 交易反思(CSI 300 基准)
│       └── conditional_logic.py
├── web/                                       # v0.4.0 7-tab 重构
│   ├── app.py                                 # 最小入口(spawn guard)
│   ├── app_main.py                            # Streamlit main 函数
│   ├── runner.py                              # 后台线程(含 run_quant_pick_in_thread)
│   ├── progress.py                            # ProgressTracker + QuantProgressTracker
│   ├── history.py                             # AI/quant/recommendation 三类历史
│   ├── pdf_export.py                          # PDF/Markdown 报告(含量化段)
│   ├── launch.py                              # CLI 启动器
│   ├── position_store.py                     # 买入计划/持仓跟踪持久化
│   ├── user_config.py                         # 模型数据源/API Key 本机持久化
│   └── components/                            # UI 组件
│       ├── sidebar.py                         # 侧边栏(DeepSeek 官方/OpenCode + API Key)
│       ├── progress_panel.py                  # 实时进度面板(含量化进度)
│       ├── report_viewer.py                   # AI 报告展示
│       ├── quant_pick.py                      # 量化选股表 + 全选 + 计划买入
│       ├── buy_plan.py                        # 买入计划详情(出场规则/退出建议/回测记录/涨跌停参考)
│       ├── position_tracker.py                # 持仓跟踪与止盈止损预警
│       ├── trade_tracker.py                   # 交易记录与策略表现跟踪
│       └── recommendation.py                  # 综合推荐 4 档展示
├── cli/
│   ├── main.py                                # Typer 主入口(analyze + quant-pick)
│   ├── quant_pick.py                          # v0.3.0 quant-pick 子命令
│   └── ...
├── scripts/
│   ├── daily_pipeline.py                      # v0.3.0 每日 cron 入口
│   └── ...
├── requirements-quant.txt                     # v0.3.0 量化层独立依赖
├── INTEGRATION_PLAN.md                        # v0.3.0 量化整合计划
├── CHANGES_FROM_UPSTREAM.md                   # 与上游的完整改动记录
├── CHANGELOG.md                               # 变更日志(含 0.3.0 quant 整合)
├── DEV_LOG.md                                 # 开发者日志(含 Week 8 quant 整合)
├── NOTICE                                     # Apache 2.0 归属声明
├── LICENSE                                    # Apache 2.0 许可证
└── pyproject.toml                             # 包定义(tradingagents-quant v0.4.0)
```

---

## 致谢

本项目站在 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)（65K ⭐ 原版）的基础上:

- 继承其 LangGraph 拓扑与 Agent 角色设计;
- 增加 A 股数据层、7 Analyst、A 股交易约束、量化前置筛选层、Conflict Resolver 节点;
- v0.4.0 进一步加入买入计划、持仓跟踪、交易记录与策略跟踪工作流。

**原始论文**:[TradingAgents: Multi-Agents LLM Financial Trading Framework](https://arxiv.org/abs/2412.20138)

---

## 许可证

[Apache License 2.0](./LICENSE)

本项目是 TauricResearch/TradingAgents 的 fork，继承 Apache 2.0 许可证。详见 [NOTICE](./NOTICE)。

---

## 免责声明

> **本项目仅供学习研究与技术演示，不构成任何投资建议。**
>
> - 本系统产出的所有分析报告和交易信号均由 AI 自动生成，可能存在错误或偏差
> - 投资决策请咨询持有中国证监会颁发资质的专业机构
> - 作者不对使用本工具产生的任何投资损失承担责任
> - 股市有风险，投资需谨慎
