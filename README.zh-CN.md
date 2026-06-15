# ActivityWatch AI Coach

**语言：** [English](README.md) | 简体中文

ActivityWatch AI Coach 是一个本地优先的 ActivityWatch 工作教练：规则 + 可选 LLM 分类、模式分析、主动检测、后台摘要与任务感知。

## 功能概览

- 读取本地 ActivityWatch 的窗口、AFK 和可选浏览器事件。
- 使用纯规则、混合 AI 或 OpenAI 兼容后端进行活动分类。
- 生成今日状态、日报和周报。
- 支持可选后台 daemon 和交互式 Web 仪表盘。
- 跟踪语义上下文、进程上下文、Git 上下文、任务信号和可选截图。
- 通过 inbox 与 policy gate 提供主动辅助。
- 支持 Windows Task Scheduler / Run key 自启动诊断。

## 环境要求

- Python 3.9 或更新版本。
- 本地已运行 ActivityWatch（`aw-server`、`aw-watcher-window`、`aw-watcher-afk`）。
- 可选：OpenAI 兼容 API key，用于 hybrid 或 LLM 功能。
- Windows 下可选：Git 和 PowerShell，用于 service/autostart 工作流。

## 开发安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ai,screenshot,web]"
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ai,screenshot,web]"
```

## 快速开始

```bash
aw-coach doctor          # 环境诊断
aw-coach status          # 今日概览
aw-coach state           # 实时语义状态（需 daemon）
aw-coach report          # 日报
aw-coach report --full   # 含 AI 建议
aw-coach inbox list      # 查看主动辅助消息
aw-coach task list       # 今日任务分布
aw-coach serve           # 交互式 Web 仪表盘
```

如果 `aw-coach` 不在 `PATH` 中：

```bash
PYTHONPATH=src python -m aw_coach.cli doctor
```

## 后台服务

直接运行 daemon：

```bash
aw-coach-daemon
```

Windows 自启动：

```powershell
aw-coach service install
aw-coach service start
aw-coach service status
aw-coach service logs --lines 50
```

安装器会先尝试 Windows Task Scheduler。如果普通用户权限无法注册计划任务，会回退到当前用户的 Run key。

## 配置

默认路径：

```text
~/.config/activitywatch/aw-watcher-ai-coach.toml
```

示例：

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
| --- | --- |
| `aw-coach inbox list/dismiss/accept` | 主动辅助收件箱 |
| `aw-coach task list/confirm/set/review` | 任务感知与校准 |
| `aw-coach serve` | 交互式 Web 仪表盘 |
| `aw-coach cost` | LLM 成本统计 |
| `aw-coach config show/set/path` | 配置管理 |
| `aw-coach service status/logs` | Windows 服务诊断 |

## 隐私说明

这个工具优先使用本地数据，但仍然会处理敏感的本机活动元数据。

- ActivityWatch 事件数据保留在本地 ActivityWatch 数据库中。
- AI 调用由配置中的后端决定。
- 截图分析是可选功能，默认关闭。
- 内置规则可以把敏感上下文标记为 `skip_screenshot`。
- 不要提交本地数据库、报告、截图、日志或密钥。

## 开发检查

```bash
python -m ruff check .
PYTHONPATH=src python -m pytest tests/ -p no:anyio -q
```

## GitHub 工作流

- `main` 是稳定默认分支。
- 功能开发放在 topic branch。
- 大改动使用 Draft PR。

详见 `AGENT.md` 与 `doc/` 中的设计文档。
