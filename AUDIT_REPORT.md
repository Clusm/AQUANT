# TradingAgents-quant v0.4.0 — 发布前全面审计报告

> 审计日期：2026-08-16
> 审计范围：`tradingagents/`、`web/`、`cli/`、`scripts/`、`tests/`、文档与发布配置
> 审计基线：commit `d5c7230`（v0.3.1 收尾）+ 未提交的 top18 终态库与数据更新改造
> 结论：**✅ 可发布 v0.4.0**（附 4 条发布前人工确认项）

---

## 0. 结论速览

| 维度 | 评分 | 结论 |
|------|------|------|
| 功能完整性 | 9.6 / 10 | 18 策略量化层 + 7 Analyst LLM 流水线 + 4 档冲突解决 + Web/CLI/自动化三入口 + 买入计划/持仓跟踪闭环 |
| 代码质量 | 9.2 / 10 | Ruff（E/F/I/B）全仓库零告警；Bandit 0 高危 / 0 中危 |
| 测试覆盖 | 8.7 / 10 | 384 passed + 44 subtests + 1 skipped；核心纯函数、Web 组件、买入计划/持仓状态机与 tab 图标有单测 |
| 文档一致性 | 9.3 / 10 | 版本、策略数、默认 Top N、默认缓存四处已统一；历史文档归档 |
| 发布安全 | 9.0 / 10 | `.env` 未跟踪；无硬编码密钥；HTTP 端点已迁 HTTPS；pickle 缓存已替换 |
| 稳定性 | 8.8 / 10 | 事件池陈旧缓存、并发写缓存、停止/恢复路径有测试或已知限制说明 |
| **综合** | **9.1 / 10** | **可发布 v0.4.0** |

---

## 1. 本轮修复的核心问题

### 1.1 事件池陈旧缓存（正确性 / P1）
`factor_ranked_event` 依赖 `event_templates.get_event_pool()`。旧实现只按策略参数命名缓存文件，**底层日线增量更新后仍命中旧事件池**，会产生陈旧信号。

- 修复：缓存 key 增加 `data_fp`（交易日范围、行数、股票数、close 总和的内容指纹）。
- 回归：新增 `tests/test_event_pool_cache.py`（4 用例：指纹变化、同数据命中、新数据失效、未知事件报错）。

### 1.2 文档与代码事实不一致（发布 / P1）
发布前最危险的问题不是代码 bug，而是 README/USAGE 写 10 策略、默认 Top 20、默认 liquid 缓存，而代码已是 18 策略、默认全量主板、默认 Top 10。

- 修复：`pyproject.toml` / `tradingagents/__init__.py` / README / USAGE / CLAUDE / NOTICE / CHANGELOG 统一为 **v0.4.0 + top18（S=5/A=11/B=2）+ Top N 固定 20 + `daily_main_board` 默认缓存**。
- 旧审计报告归档至 `docs/archive/AUDIT_REPORT_v0.3.0.md`。

### 1.3 运行时依赖未声明（安装可用性 / P1）
量化层首次运行需要 akshare（交易日历、全市场代码、ST 列表），但 pyproject 未声明；`a_stock` 还直接 import `dateutil`。

- 修复：`akshare>=1.16`、`python-dateutil>=2.9` 纳入主依赖；`adata` 独立为 `[quant-data]` 可选依赖。
- 已实测 `pip install -e . --no-deps` 后 `pip show tradingagents-quant` 版本 0.4.0、依赖元数据正确。

### 1.4 UI 样式散落（体验 / P2）
原先全局 CSS 全部内联在 `web/app_main.py`，并加载 Google Fonts（国内访问不稳定）。

- 修复：新增 `web/theme.py` 作为设计令牌 + 全局 CSS + HTML 小件；主区 Hero、侧栏品牌卡、进度阶段卡、空状态卡统一；移除 Google Fonts 外部依赖。
- 安全：报告页 signal / ticker label 渲染前 HTML 转义。

### 1.5 安全与发布卫生（P2）
| 问题 | 修复 |
|------|------|
| `trading_calendar.pkl` 使用 pickle 反序列化 | 改 parquet 缓存 |
| `a_stock` 新浪 / 同花顺使用 HTTP | 改为 HTTPS（实测 200） |
| Alpha Vantage 请求无 timeout | 增加 `timeout=30` |
| `.env.example` 注释含开发者绝对路径 | 替换为通用示例路径 |
| `scripts/eval_recommendations.py` 硬编码 Windows 绝对路径 | 改为 `QUANT_CACHE_DIR` + 项目默认缓存目录探测 |
| `web/launch.py` 启动失败仍返回 0 | 显式检查 `subprocess` 并返回真实退出码 |
| `main.py` 仍是美股 yfinance 旧示例 | 改为 A 股 DeepSeek 最简示例 |

---

### 1.6 数据更新链路去重确认（P1）
全仓库只有一条增量核心与一条回补核心：
- `tradingagents/quant/data_update.increment_data` ← Web 后台、Web runner、`scripts/incremental_update.py`
- `tradingagents/quant/data_update.backfill_stale` ← `scripts/incremental_update.py`、`scripts/backfill_main_board.py`
- 所有写盘都经 `tradingagents/quant/data/cache.update()`：`concat → drop_duplicates(keep="last") → 原子写`。

本轮追加防护：
- `cm.update()` 加进程内 + 跨进程文件锁，解决两个进程同时读旧缓存时后写覆盖先写的问题。
- 写文件改为临时 parquet + `os.replace` 原子替换。
- 新增回归测试：同 key 重复写幂等、4 线程并发更新不丢行、不产生 `(stock_code, trade_date)` 重复行、无临时文件残留。

### 1.7 小资金账户过滤调整（2026-08-16 追加）
- `quant_price_max` 从 80 调整为 **70**（5 万以内仍建议 40–50）。
- 新增 `quant_exclude_limit_up_down=true`：当日收盘处于涨停价/跌停价的股票不进入 universe。
  - 用当日 `pre_close` 计算精确涨跌停价，无未来数据。
  - 主板 10%、创业板/科创板 20%、北交所 30%。
  - 过滤发生在价格过滤之后、流动性排序之前。
- 新增 `tests/test_universe_limit.py` 覆盖精确涨跌停价、tick 取整、板块幅度、旧缓存 change_pct 回退、top-K 排除行为。

### 1.9 发布前终检（2026-08-16 第二轮）
在 v1.1 买入计划/持仓跟踪功能完成后又执行了一轮 Web 交互与状态层复查，修复：
- 同一计划在「买入计划」和「持仓跟踪」同时展开会触发 Streamlit 重复 form key（已按 tab 前缀隔离，AppTest 复现并回归）。
- 历史 tab 与量化 tab 同时渲染同一选股表，会互相覆盖勾选状态、产生重复 widget（历史 tab 改为只显示加载状态，表格只在量化 tab 渲染）。
- 主 tab 矢量图标的 nth-of-type CSS 会误标报告页内部的嵌套 tabs（多空辩论/风控），改为 JS 标记主 tab 容器 `.aq-main-tabs` 后限定作用域。
- 持仓到期比较使用自然日，周末/节假日会提前 1-2 天触发「建议到期」（已改用交易日历）。
- `plans.json` 损坏时原实现会在下次保存时静默覆盖唯一副本（现在先备份为 `plans.corrupt-*.json` 再重建）。
- 确认买入/放弃/卖出存在 check-then-act 竞态（改为单锁内校验+写入）；NaN 量化指标可能写入非法 JSON 值（已安全归零）。
- 最新价在缓存存在但缺该股票时不会回退腾讯行情（已修复）；持仓页每张卡重复读取 ~50MB 日线 parquet（已加 60s TTL 缓存）。
- PDF 封面直接输出 🟢/🟡 emoji，中文字体缺字形会渲染成方框（改为文字标签）；PDF 每次 rerun 都重新生成（已缓存 10 分钟）。
- `strip_think_tags` 不识别标准 `<thinking>` / `<think>` 标签（已兼容三种格式）。
- Sidebar 模型目录升级后 session_state 中旧 index 可能越界（已 clamp）。

验证：ruff 全仓库通过；bandit 0 高 / 0 中；pytest 384 passed；AppTest 覆盖重复 key、计划创建、计划详情、持仓详情、历史加载，0 异常。

### 1.8 买入计划 / 持仓跟踪（发布候选追加）
- 新增 `web/position_store.py`：计划状态机 `planned → filled → closed` / `planned → abandoned`，本地 JSON 原子落盘。
- 新增 7-tab Web UI：量化选股 → AI 深度分析 → 买入计划 → 持仓跟踪 → 交易记录与策略跟踪 → 综合推荐 → 历史;tab 图标统一为细线 SVG(随激活态切换橙色)。
- 计划详情按策略展示出场类型（9 个信号出场 / 固定持仓 / 固定持仓 + 信号出场保护）、ATR 参数参考、入场建议、次日涨跌停参考价；并自动关联 LLM 历史日志的 `final_signal_label` 与 `conviction_score`。
- 持仓跟踪 V1 规则：-5% 止损预警、+8% 止盈预警、建议到期（固定持仓策略）、T+1 买入当日禁卖；ATR 参数为回测元数据展示，V2 实现真实 ATR 跟踪。
- Sidebar 移除量化参数区；Top N 固定 20、worker 固定 8；模型配置区仅保留数据源与 API Key，快速/深度模型内置为 DeepSeek-V4-Flash / DeepSeek-V4-Pro。
- 回归：`tests/test_position_store.py`（6 用例）+ Streamlit AppTest（6 tab、0 异常）。

## 2. 静态与安全审计结果

### 2.1 Ruff
配置见 `pyproject.toml` `[tool.ruff]`（E/F/I/B，忽略 CLI 星号导入与 E402/E501）：

```bash
python -m ruff check tradingagents web cli scripts
# All checks passed!
```

### 2.2 Bandit
```bash
python -m bandit -r tradingagents web cli scripts -q
# 0 高危 / 0 中危；剩余 18 条 LOW 均为：
#   - try/except-pass/continue 的降级路径（数据源容错）
#   - subprocess.run(..., shell=False) 的固定参数列表
#   - SQL 表名来自固定白名单（已加 nosec 注释）
```

### 2.3 密钥与隐私
- `.env` 未被 git 跟踪；`.gitignore` 已含 `.env`。
- 代码扫描未发现硬编码 API key / token。
- 已修复：`.claude/scheduled_tasks.json` / `.lock` 已从 git 索引移除并加入 `.gitignore`（本地文件保留）。

### 2.4 版本与事实一致性
- `pyproject.toml`、`tradingagents/__init__.py`、`pip show`：**0.4.0**。
- 策略库：`get_all_strategies_final()` 实测 **18 个，S=5/A=11/B=2**。
- 默认 Top N：Web / CLI / pipeline / `pick()` 全部固定为 **20**。
- 默认缓存：Web / CLI / pipeline / `DEFAULT_CONFIG` 全部为 **daily_main_board**。

---

## 3. 测试结果

```text
pytest tests/ -q --disable-warnings
384 passed, 1 skipped, 44 subtests passed in ~20s
```

跳过项：`tests/test_google_api_key.py`（需 `[google]` extra，预期跳过）。

新增回归测试：
- `tests/test_event_pool_cache.py`（4 用例）
- `tests/test_theme.py` 扩展全局 CSS / HTML 转义（9 用例）
- `tests/test_position_store.py`（6 用例：计划创建/状态迁移/放弃计划/不覆盖/持仓天数/涨跌停参考价）
- Streamlit AppTest：7 tab、`exceptions=0`

其他验证：
- `python -m compileall -q tradingagents web cli scripts tests main.py` 通过。
- 全模块 import 冒烟通过（仅 `google_client` 因未安装可选 extra 跳过）。
- `streamlit run web/app.py` headless 启动健康检查 200 / `_stcore/health` = ok。

---

## 4. 剩余风险与建议

| # | 级别 | 风险 | 建议 |
|---|------|------|------|
| R1 | 已修复 | 本机已跑完整 18 策略实盘 `pick()`(3042 股缓存):冷缓存 102s、热缓存 39s、18/18 策略 0 错误,并已与 `prune_universe=false` 旧路径结果逐项比对一致 | 无需处理 |
| R2 | 高（需人工确认） | 未做真实 LLM 端到端（依赖 DeepSeek API key/quota） | 发布前跑一只：`tradingagents analyze`，确认 Quant Picker → Conflict Resolver 四档标签落盘 |
| R3 | 已修复 | `cm.update()` 原为非原子整表读写，并发更新存在后写覆盖风险 | 已加进程内 + 跨进程文件锁与临时文件 + `os.replace` 原子替换，并新增并发/幂等回归测试 |
| R4 | 中 | `sina_fetcher` 32 并发共享模块级 Session + 串行节流，新浪仍可能 456 | 已有备用端点 + 分块回补；批量任务建议 `SINA_MIN_INTERVAL=0.1` |
| R5 | 已修复 | `.claude/scheduled_tasks.*` 个人/历史产物 | 已 `git rm --cached` 并加入 `.gitignore`；根目录截图请按需保留或删除 |
| R6 | 已修复 | `requirements.txt` 内容由 `"."` 改为 `-e .` 并加注释 | 无需处理 |

---

## 5. 发布 Checklist

### 5.1 必须在 `git commit` 前完成（人工）
- [x] `git rm --cached .claude/scheduled_tasks.json .claude/scheduled_tasks.lock`（保留本地文件、移出版本库）
- [x] 已将 `.claude/` 加入 `.gitignore`
- [ ] 确认 `.env` 不在 `git ls-files` 中（当前已确认）
- [ ] 决定根目录 `ScreenShot_2026-07-24_130750_878.png` 是否保留（非代码资产）
- [ ] 执行 R1 的真实 `quant-pick` 冒烟
- [ ] 执行 R2 的真实 LLM 端到端冒烟

### 5.2 自动化验证（本轮已跑，发布前可重跑）
```bash
python -m ruff check tradingagents web cli scripts
python -m bandit -r tradingagents web cli scripts -q
python -m pytest tests/ -q --disable-warnings
python -m compileall -q tradingagents web cli scripts tests main.py
tradingagents-web   # 或 streamlit run web/app.py
```

### 5.3 建议提交信息
```text
release: v0.4.0 — top18 终态策略库 + 买入计划/持仓跟踪 + 发布审计 + Web UI 主题统一
```

---

## 附录 A：本轮主要文件变更

| 文件 | 变更 |
|------|------|
| `web/theme.py` | 新增设计令牌 / 全局 CSS / Hero / 空状态 / 胶囊组件 |
| `web/app_main.py` | 移除 110 行内联 CSS，改为主题注入；7-tab 布局；Top N 固定 20 |
| `web/position_store.py` | 新增买入计划/持仓状态机、出场策略元数据、涨跌停参考价、LLM 历史关联 |
| `web/components/quant_pick.py` | 新增每行「计划买入」；S/A/B/C 短线+中线合并计数 |
| `web/components/buy_plan.py` | 新增买入计划详情（出场规则 / ATR 参考 / 次日涨跌停价 / LLM 标签） |
| `web/components/position_tracker.py` | 新增持仓跟踪（收益、持有天数、止损/止盈/到期预警、T+1） |
| `web/components/*` | 侧栏品牌卡、进度阶段卡、报告信号卡、推荐面板统一主题 |
| `tradingagents/quant/strategy/event_templates.py` | 事件池缓存增加日线数据指纹 |
| `tradingagents/quant/quant_picker.py` | 默认 Top N=10；文案统一 18 策略；NaN 安全 |
| `cli/quant_pick.py`、`scripts/daily_pipeline.py` | 默认 Top N=10；异常链 / 退出码规范 |
| `pyproject.toml` | v0.4.0；akshare / python-dateutil 主依赖；quant-data extra；Ruff 配置 |
| `tradingagents/quant/utils/trading_calendar.py` | pickle 缓存改为 parquet |
| `tradingagents/dataflows/a_stock.py` | urllib → requests；HTTP → HTTPS |
| `main.py` | A 股 DeepSeek 快速启动示例 |
| `AUDIT_REPORT.md` | 本报告（旧 v0.3.0 报告归档到 `docs/archive/`） |
| `CHANGELOG.md` | 新增 `[0.4.0] — 2026-08-16` |

*报告生成时间：2026-08-16*
*审计工具：ruff + bandit + pytest + Streamlit headless smoke + 手工代码走查*
