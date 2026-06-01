# AGENT.md — aw-watcher-ai-coach 项目指南

> 本文件供 AI 助手快速理解项目全貌，接手开发。

## 项目定位

ActivityWatch 的本地优先智能解释层。不替代 ActivityWatch 的数据采集，而是把原始行为事件转化为分类、评分、洞察、通知和可持续改进的规则系统。

```
ActivityWatch (数据采集) → aw-coach (分类 + 评分 + 洞察 + 通知 + 规则自进化)
```

## 技术栈

- Python 3.9+（开发用 3.10）
- 依赖：click, pydantic, pyyaml, tomli, aw-client
- 可选依赖：openai (AI 后端), mss+pillow (截图), flask+jinja2 (Web)
- 测试：pytest (133 tests)
- 运行：systemd user service，常驻 daemon

## 项目结构

```
aw-watcher-ai-coach/
├── pyproject.toml                 # 构建配置 + 依赖 + entry points
├── contrib/
│   ├── aw-coach.service           # systemd user service
│   └── aw-coach.desktop           # XDG autostart
├── src/aw_coach/
│   ├── __init__.py                # 版本号 (0.1.0)
│   ├── __main__.py                # python -m aw_coach (daemon 入口)
│   ├── cli.py                     # 所有 CLI 命令 (~850 行, 14 个命令)
│   ├── config.py                  # TOML 配置 + pydantic models
│   ├── collector.py               # DataCollector + merge_events + web URL 合并
│   ├── rules/
│   │   ├── engine.py              # RuleEngine + RuleResult + DEFAULT_WEIGHTS
│   │   ├── loader.py              # YAML 加载 + 多目录合并
│   │   └── builtin/
│   │       ├── global.yml         # 全局规则 (IDE/浏览器/通信/设计/敏感/Linux)
│   │       └── cn.yml             # 中文应用 (飞书/钉钉/微信/WPS)
│   ├── analyzer.py                # PatternAnalyzer (focus/productivity/death_loop)
│   ├── report.py                  # ReportGenerator (日报/周报/建议)
│   ├── notify.py                  # 跨平台系统通知 (notify-send/osascript)
│   ├── scheduler.py               # 后台 daemon 主循环 (hourly分析/通知/日报)
│   ├── storage.py                 # SQLite (cost_log/batch_queue/corrections)
│   ├── daemon.py                  # systemd entry point
│   └── ai/
│       ├── base.py                # AIBackend ABC + ClassificationResult
│       ├── cost.py                # CostController + PRICING + alert_thresholds
│       ├── openai_backend.py      # OpenAI/DeepSeek API 调用 + prompt 构建
│       ├── hybrid.py              # HybridBackend (rule→LLM fallback→cost gate)
│       └── suggestions.py         # AI 生成建议
├── tests/                         # 13 个测试文件, 133 tests
│   ├── test_analyzer.py           # 专注度/深度工作/切换/death loop
│   ├── test_cli.py                # CLI 命令基础测试
│   ├── test_collector.py          # 事件合并/heartbeat/AFK/web URL
│   ├── test_config.py             # 配置加载/默认值/校验
│   ├── test_rules.py              # 规则匹配/中文/浏览器/敏感
│   ├── test_report.py             # 日报格式/建议生成
│   ├── test_storage.py            # SQLite CRUD
│   ├── test_ai.py                 # CostController/OpenAI/Hybrid
│   ├── test_e2e.py                # 端到端 hybrid 流程
│   ├── test_notify.py             # 通知 mock
│   ├── test_web_integration.py    # aw-watcher-web URL 合并
│   ├── test_phase4.py             # weight/death loop/AI agent
│   └── test_calibrate.py          # calibrate/reclassify 命令
└── doc/
    ├── active-agent-roadmap.md    # 主动 Agent 架构设计 (L1-L6)
    └── active-agent-analysis.md   # 当前系统诊断 + 6 周路线图
```

## 运行方式

```bash
# 测试
cd aw-watcher-ai-coach
PYTHONPATH=src python3 -m pytest tests/ -p no:anyio -q

# CLI（通过 wrapper 脚本）
export PATH="$HOME/.local/bin:$PATH"
aw-coach status
aw-coach report
aw-coach doctor

# 后台 daemon（systemd 管理）
systemctl --user status aw-coach
journalctl --user -u aw-coach -f
```

注意：由于系统 setuptools 版本过低无法 `pip install -e .`，当前通过 `PYTHONPATH=src` 方式运行。`~/.local/bin/aw-coach` 是 wrapper 脚本。

## CLI 命令清单

| 命令 | 用途 |
|------|------|
| `status` | 实时工作状态（优先读 bucket，fallback 到原始数据） |
| `report [date]` | 日报（--full 调用 LLM，--dry-run 输出 prompt） |
| `weekly` | 周报（过去 7 天汇总） |
| `open` | 生成 HTML 仪表盘并用浏览器打开 |
| `doctor` | 环境诊断（aw-server/bucket/rules/platform/cost） |
| `calibrate` | 扫描未知 app，交互式分类并写入 user.yml |
| `reclassify --from DATE` | 用最新规则重新分析历史数据 |
| `rule-test --app X --title Y` | 测试规则匹配结果 |
| `rule-suggest` | 从纠正历史自动生成规则建议 |
| `correct [--last/--time/--interactive]` | 纠正分类结果 |
| `cost` | AI API 成本监控 |
| `notify-test` | 测试系统通知 |

## 核心架构

```
aw-server (localhost:5600)
    ↓ REST API (aw-client)
DataCollector
    ↓ heartbeat 合并 + AFK 过滤 + web URL join
    ↓ → ActivitySlice[]
RuleEngine (42 条内置规则 + user.yml)
    ↓ confidence >= 0.85 → 直接输出
    ↓ confidence < 0.85 → HybridBackend → CostController → OpenAI/DeepSeek
    ↓ → RuleResult (activity_type, confidence, weight)
PatternAnalyzer
    ↓ → AnalysisResult (focus_score, productivity_score, death_loops, ...)
Scheduler (systemd daemon, hourly loop)
    ↓ 写入 ai-coach_{hostname} bucket
    ↓ 即时摘要通知 (每 2h)
    ↓ 日报生成 (21:00)
CLI
    ↓ 优先从 bucket 读取已分析结果
    ↓ fallback 到实时计算
```

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 分类策略 | 规则优先，LLM fallback | 规则覆盖 80%+，LLM 只处理 edge case，月成本 <$5 |
| 数据存储 | aw-server bucket + SQLite | bucket 存分析结果，SQLite 存成本/纠正/队列 |
| CLI vs daemon | 分离（不同 client name） | daemon="aw-coach", CLI="aw-coach-cli"，避免单实例锁冲突 |
| heartbeat 合并 | 同 app 间隔 <2min 合并 | AW 原始数据中位数 4s，不合并则分析无意义 |
| 切换去抖动 | <30s 的类型闪烁不计数 | 避免切换次数虚高 (604 → 122) |
| 通知控制 | notification_method 配置 | "both"/"cli_only"/"notification"，respect 用户意愿 |
| 规则加载 | builtin + user 目录合并 | with_all_rules() 同时加载，sub_rules 合并不覆盖 |
| 截图 | 默认关闭，配置级授权 | 跨平台通知回调不可靠，不逐次弹窗 |
| Weight 评分 | DEFAULT_WEIGHTS 按 activity_type | programming=1.0, entertainment=-0.5，规则可覆盖 |

## 数据流关键路径

### Scheduler hourly 分析
```python
scheduler._hourly_analyze(hour_start, hour_end)
  → collector.fetch_range()          # 拉取原始数据 + web URL
  → engine.classify() for each slice # 规则分类
  → analyzer.analyze()               # 计算 focus/productivity/loops
  → client.insert_event(bucket)      # 写入 aw-server bucket
```

### CLI status/report
```python
_get_analysis(date)
  → _try_read_from_bucket()          # 优先读已分析结果
  → fallback: collector + engine + analyzer  # 实时计算
```

### 反馈闭环
```
correct → SQLite corrections → rule-suggest → user.yml → 下次分析更准
calibrate → 扫描 unknown → 用户分类 → user.yml → reclassify 历史
```

## 配置文件

路径：`~/.config/activitywatch/aw-watcher-ai-coach.toml`

不存在时使用全部默认值（rule_only 模式，零配置可用）。

关键配置项：
```toml
[ai]
backend = "rule_only"  # rule_only / openai / hybrid

[ai.openai]
api_key = "sk-..."
base_url = "https://api.deepseek.com"  # 兼容 OpenAI API 的任何服务
model = "deepseek-v4-flash"

[cost]
monthly_budget_usd = 5.0

[report]
daily_report_time = "21:00"
notification_method = "both"
```

## 本地数据路径

```
~/.local/share/activitywatch/aw-watcher-ai-coach/
├── aw-coach.db          # SQLite (cost_log, corrections, batch_queue)
├── rules/
│   └── user.yml         # 用户自定义规则 (calibrate/rule-suggest 生成)
└── reports/
    ├── daily/*.md       # 日报 Markdown
    ├── weekly/*.md      # 周报 Markdown
    └── web/index.html   # HTML 仪表盘
```

## 测试约定

```bash
# 运行全部测试
PYTHONPATH=src python3 -m pytest tests/ -p no:anyio -q

# 运行单个文件
PYTHONPATH=src python3 -m pytest tests/test_rules.py -v -p no:anyio

# 需要 -p no:anyio 因为系统 anyio 插件与 pytest 版本不兼容
```

测试不依赖真实 aw-server。DataCollector 通过 `patch.object(DataCollector, "__init__")` + `patch.object(DataCollector, "fetch_range")` mock。

## 当前已知问题

1. `aw-coach open` 依赖 Chart.js CDN，离线时图表空白
2. `report --full` 在 rule_only 模式下只打印提示，不调用 LLM
3. `correct --interactive` 需要 aw-server 运行
4. deep_work_threshold 默认 25min 对碎片化工作模式偏高，可通过配置调低

## 下一步路线（Phase 5: 主动 Agent）

当前系统是 **L2 级**（分类 + 总结）。下一步目标是升级到 **L4 级**（主动建议动作）。

核心缺失模块（按优先级）：

1. **State Model** (`state.py`) — UserWorkState 实时状态，每分钟更新
2. **Detector 层** (`detector.py`) — unknown/high_switch/ai_coding/focus_block
3. **Policy Engine** (`policy.py`) — interrupt budget + quiet hours + focus protect
4. **Agent Inbox** (`inbox.py`) — 建议收件箱 + accept/dismiss
5. **Feedback Memory** (`profile.py`) — user_profile + 学习衰减

详细设计见 `doc/active-agent-roadmap.md` 和 `doc/active-agent-analysis.md`。

## 贡献约定

- TDD：先写测试，再写实现
- 类型注解：`from __future__ import annotations` + 完整类型标注
- 无注释优先：只在 WHY 非显而易见时写注释
- 零配置优先：新功能必须有合理默认值
- 本地优先：不默认调用外部 API
- 每次修改后运行全量测试确认无回归
