# ActivityWatch AI Coach

**语言：** [English](README.md) | 简体中文

ActivityWatch AI Coach 会分析本地 ActivityWatch 数据，并生成工作模式摘要、报告和轻量级的自我管理诊断。

当前项目主要面向本地优先的 Windows 使用场景，并假设 ActivityWatch 已经在本机运行。

## 功能概览

- 读取本地 ActivityWatch 事件。
- 使用规则模式或混合 AI 后端分类工作活动。
- 生成实时状态、日报和周报。
- 支持可选后台 daemon。
- 提供 Windows 自启动诊断，包括 service 状态、heartbeat 和日志查看。
- 支持可选的语义上下文、进程上下文、Git 上下文和截图信号。

## 环境要求

- Python 3.9 或更新版本。
- 本地已运行 ActivityWatch。
- Windows 下需要 Git 和 PowerShell。
- 可选：GitHub CLI，用于仓库和 PR 工作流。

## 开发安装

在仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ai,screenshot,web]"
```

检查 CLI：

```powershell
aw-coach --help
aw-coach doctor
```

如果 `aw-coach` 不在 PATH 中，可以使用：

```powershell
$env:PYTHONPATH='src'
python -m aw_coach.cli doctor
```

## Windows 后台服务

安装自启动：

```powershell
aw-coach service install
```

安装器会先尝试使用 Windows Task Scheduler。如果普通用户权限无法注册计划任务，会回退到当前用户的 Run key。

启动或停止 daemon：

```powershell
aw-coach service start
aw-coach service stop
```

查看服务健康状态：

```powershell
aw-coach service status
```

状态输出包含：

- 安装后端，例如 `Ready`、`Running`、`RunKey` 或 `NotInstalled`
- daemon 进程 ID
- scheduler heartbeat 新鲜度
- 最近一次 daemon 错误
- stdout/stderr 日志路径

查看 daemon 最新日志：

```powershell
aw-coach service logs --lines 50
```

运行完整诊断：

```powershell
aw-coach doctor
```

`doctor` 会包含一行非阻塞的 `service/autostart` 状态。即使 service 检查失败，也不会阻止其他健康检查继续运行。

## Windows 常见排查

如果 `service status` 显示 `RunKey`，通常表示 Task Scheduler 注册被拒绝，当前正在使用 Run key 回退自启动。

如果 heartbeat 变成 stale：

```powershell
aw-coach service logs --lines 100
aw-coach service stop
aw-coach service start
aw-coach service status
```

如果 ActivityWatch 不可用，先启动 ActivityWatch，然后重新运行：

```powershell
aw-coach doctor
```

## 隐私说明

这个工具优先使用本地数据，但仍然会处理敏感的本机活动元数据。

- ActivityWatch 事件数据保留在本地 ActivityWatch 数据库中。
- AI 调用由配置中的后端决定。
- 截图分析是可选功能，应按敏感数据处理。
- 内置规则可以把敏感上下文标记为 `skip_screenshot`。
- 启用截图或混合 AI 功能前，应先审查配置。
- 不要提交本地数据库、报告、截图、日志或密钥。

## 开发检查

运行 lint：

```powershell
python -m ruff check .
```

运行测试：

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/ -p no:anyio -q
```

推送改动前应运行以上两项。

## GitHub 工作流

当前仓库约定：

- `main` 是稳定默认分支。
- 功能开发放在 feature 分支。
- GitHub Actions CI 会运行 lint 和测试。
- 大改动使用 Draft PR。

推荐的 PR 验证命令：

```powershell
python -m ruff check .
$env:PYTHONPATH='src'; python -m pytest tests/ -p no:anyio -q
aw-coach service status
aw-coach service logs --lines 20
aw-coach doctor
```
