# aw-watcher-ai-coach

ActivityWatch 的本地优先 AI 工作教练：规则 + 可选 LLM 分类、模式分析、主动检测、后台摘要与任务感知。

## 安装

```bash
pip install -e ".[ai,dev]"
```

确保 ActivityWatch（`aw-server` + `aw-watcher-window` + `aw-watcher-afk`）已运行。

## 快速开始

```bash
aw-coach doctor          # 环境诊断
aw-coach status          # 今日概览
aw-coach state           # 实时语义状态（需 daemon）
aw-coach report          # 日报
aw-coach report --full   # 含 AI 建议
aw-coach inbox list      # 查看主动辅助消息
aw-coach task list       # 今日任务分布
```

## 后台服务

```bash
aw-coach-daemon          # Web 仪表盘 + 定时分析
# 或
systemctl --user start aw-coach   # 见 contrib/aw-coach.service
```

## 配置

`~/.config/activitywatch/aw-watcher-ai-coach.toml`

```toml
[ai]
backend = "hybrid"   # rule_only | hybrid | openai

[ai.openai]
api_key = "${DEEPSEEK_API_KEY}"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/v1"

[policy]
quiet_hours_enabled = true
quiet_hours_start = "22:00"
quiet_hours_end = "08:00"

[report]
instant_summary_interval_hours = 2
background_ai_summary = false   # 灰度开启后台 LLM 叙事摘要
morning_brief_time = "09:00"

[tasks]
enabled = true
project_roots = ["~/projects", "~/下载/activitywatch"]

[screenshot]
enabled = false    # 默认关闭，保护隐私
```

## CLI 速查

| 命令 | 说明 |
|------|------|
| `aw-coach inbox list/dismiss/accept` | 主动辅助收件箱 |
| `aw-coach task list/confirm/set/review` | 任务感知与校准 |
| `aw-coach serve` | 交互式 Web 仪表盘 |
| `aw-coach cost` | LLM 成本统计 |
| `aw-coach config show/set/path` | 配置管理 |

详见 `AGENT.md` 与设计文档 `doc/`。
