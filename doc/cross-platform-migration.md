# aw-coach 跨系统迁移指南

> 本文档分析将 aw-coach 从当前 Ubuntu 22.04 开发环境迁移到其他操作系统（macOS / Windows / 其他 Linux）所需的工作。

---

## 一、总体评估

| 目标平台 | 迁移难度 | 主要工作量 | 推荐优先级 |
|---------|---------|-----------|-----------|
| **其他 Linux 发行版** | 🟢 低 | 几乎无改动 | P0 |
| **macOS** | 🟡 中 | 通知系统、路径、服务管理、OCR/VLM | P1 |
| **Windows** | 🔴 高 | 通知、路径、服务、截图、OCR、终端编码 | P2 |

**核心结论：**

> aw-coach 的**业务逻辑层**（分析、规则、分类、报告）是**纯 Python**，跨平台无压力。  
> 真正的迁移成本集中在**系统交互层**（通知、截图、服务管理、路径规范、OCR/VLM）。

---

## 二、逐层迁移分析

### 2.1 Python 环境与核心依赖 ✅ 已跨平台

**当前依赖（`pyproject.toml`）：**

```toml
dependencies = [
    "aw-client>=0.5.0",
    "click>=8.0",
    "pydantic>=2.0",
    "tomli>=2.0; python_version < '3.11'",
    "pyyaml>=6.0",
]
```

**评估：**

| 包 | Linux | macOS | Windows | 说明 |
|----|-------|-------|---------|------|
| aw-client | ✅ | ✅ | ✅ | ActivityWatch 官方支持全平台 |
| click | ✅ | ✅ | ✅ | 纯 Python |
| pydantic | ✅ | ✅ | ✅ | 纯 Python |
| tomli | ✅ | ✅ | ✅ | 纯 Python |
| pyyaml | ✅ | ✅ | ✅ | 有 wheel，无需编译 |
| openai | ✅ | ✅ | ✅ | 可选依赖，HTTP API |
| httpx | ✅ | ✅ | ✅ | 可选依赖 |

**迁移成本：零。** 核心依赖全平台可用。

---

### 2.2 配置文件路径 ⚠️ 需要适配

**当前实现（`config.py`）：**

```python
DEFAULT_CONFIG_PATH = Path("~/.config/activitywatch/aw-watcher-ai-coach.toml").expanduser()
DEFAULT_DATA_DIR = Path("~/.local/share/activitywatch/aw-watcher-ai-coach").expanduser()
```

**问题：** 这是 **XDG Base Directory** 规范，Linux 和 macOS 通用，但 **Windows 不遵循 XDG**。

**建议修改：** 使用 `platformdirs` 库自动适配。

```python
from platformdirs import user_config_dir, user_data_dir

DEFAULT_CONFIG_DIR = Path(user_config_dir("aw-watcher-ai-coach", "activitywatch"))
DEFAULT_DATA_DIR = Path(user_data_dir("aw-watcher-ai-coach", "activitywatch"))

# 自动映射：
# Linux   -> ~/.config/activitywatch/aw-watcher-ai-coach/
# macOS   -> ~/Library/Application Support/activitywatch/aw-watcher-ai-coach/
# Windows -> C:\Users\<user>\AppData\Local\activitywatch\aw-watcher-ai-coach\
```

**需要修改的文件：**
- `src/aw_coach/config.py` — 替换硬编码 XDG 路径
- `src/aw_coach/rules/engine.py` — user rules 目录跟随 DATA_DIR

**工作量：小（半天）**

---

### 2.3 通知系统 🟡 已三分支，但需完善

**当前实现（`notify.py`）：**

```python
def send_notification(title, body, detail_url=None):
    system = platform.system()
    if system == "Darwin":      # macOS: osascript
    elif system == "Windows":   # Windows: PowerShell Toast
    elif system == "Linux":     # Linux: dbus + notify-send
```

**现状评估：**

| 平台 | 当前实现 | 问题 | 建议 |
|------|---------|------|------|
| Linux | dbus action + notify-send fallback | ✅ 已完善 | 无需改动 |
| macOS | osascript `display notification` | 🟡 无点击支持 | 可升级为 `pync` 或 `osascript` with callback |
| Windows | PowerShell ToastNotification | 🟡 无点击支持 | 可升级为 `win10toast` 或 `windows-toasts` |

**macOS 升级建议：**

当前 macOS 分支只支持基础通知，无点击打开 URL 功能。建议：

```python
# 方案 A: 使用 pync 库（pip install pync）
from pync import Notifier
Notifier.notify(body, title=title, open=detail_url)

# 方案 B: 保持 osascript，但增加 URL 支持
script = f'display notification "{body}" with title "{title}"'
# macOS Notification Center 点击行为受系统限制，
# 最佳实践是通知内容中包含 actionable 提示
```

**Windows 升级建议：**

```python
# 方案: 使用 windows-toasts 库（pip install windows-toasts）
from windows_toasts import WindowsToaster, ToastText2
toaster = WindowsToaster("aw-coach")
toast = ToastText2()
toast.SetHeadline(title)
toast.SetBody(body)
# Windows Toast 支持按钮，可添加"查看详情"按钮
toast.AddAction(ToastButton("查看详情", detail_url))
toaster.show_toast(toast)
```

**工作量：小（1 天）**

---

### 2.4 后台服务管理 🔴 差异最大

**当前实现：** systemd user service + XDG autostart

```bash
# Linux
~/.config/systemd/user/aw-coach.service
~/.config/autostart/aw-coach.desktop
```

**各平台替代方案：**

#### Linux（其他发行版）— ✅ 无改动

systemd 是现代 Linux 标准，Fedora/Arch/openSUSE 均支持。  
非 systemd 发行版（如 Alpine/Slackware/Gentoo OpenRC）需要额外适配。

#### macOS — 🟡 需要新实现

```bash
# macOS 使用 launchd
~/Library/LaunchAgents/com.activitywatch.aw-coach.plist
```

示例 plist：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.activitywatch.aw-coach</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/aw-coach-daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>~/Library/Logs/aw-coach.log</string>
    <key>StandardErrorPath</key>
    <string>~/Library/Logs/aw-coach.error.log</string>
</dict>
</plist>
```

**CLI 命令适配：**

```bash
aw-coach install-service    # 根据平台自动选择 systemd / launchd / Windows Service
aw-coach uninstall-service
aw-coach start-service
aw-coach stop-service
```

#### Windows — 🔴 需要新实现

Windows 没有 systemd/launchd，选项：

1. **Windows Service**（最正式，但 Python 服务需要 pywin32）
2. **任务计划程序**（Task Scheduler，推荐，最简单）
3. **启动文件夹**（最简单，但用户注销即停止）

推荐方案：**任务计划程序 + 启动文件夹双保险**

```powershell
# PowerShell 脚本安装任务计划
$action = New-ScheduledTaskAction -Execute "pythonw" -Argument "-m aw_coach.daemon"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "aw-coach" -Action $action -Trigger $trigger -Settings $settings
```

**工作量：中（2-3 天，需测试各平台）**

---

### 2.5 ActivityWatch 生态依赖 ✅ 已跨平台

ActivityWatch 官方支持：

| 组件 | Linux | macOS | Windows |
|------|-------|-------|---------|
| aw-server | ✅ | ✅ | ✅ |
| aw-watcher-window | ✅ | ✅ | ✅ |
| aw-watcher-afk | ✅ | ✅ | ✅ |
| aw-watcher-web | ✅ | ✅ | ⚠️ Chrome only |
| aw-watcher-input | ✅ | ⚠️ 有限 | ⚠️ 有限 |

**注意：**
- macOS 上 `aw-watcher-window` 需要**辅助功能权限**（Accessibility）
- Windows 上 `aw-watcher-window` 需要**窗口钩子权限**
- `aw-watcher-web` 在 Windows 上仅支持 Chrome 扩展（Firefox 支持实验性）

**迁移成本：低。** ActivityWatch 本身已经处理好跨平台，aw-coach 只需确保能连接到 aw-server（localhost:5600 通用）。

---

### 2.6 截图与 OCR 🔴 平台差异大

这是迁移成本最高的模块。

#### 截图工具

| 平台 | 当前工具 | 替代方案 | 备注 |
|------|---------|---------|------|
| Linux | `mss` / `grim` | 相同 | `mss` 是纯 Python，跨平台 |
| macOS | — | `mss` 或 `screencapture` | `mss` 支持 macOS |
| Windows | — | `mss` 或 `PIL.ImageGrab` | `mss` 支持 Windows |

**好消息：** `mss` 库是纯 Python，全平台支持。

```python
import mss
with mss.mss() as sct:
    screenshot = sct.grab(sct.monitors[1])  # 全平台通用
```

#### OCR 引擎

| 平台 | 推荐 OCR | GPU 加速 | 备注 |
|------|---------|---------|------|
| Linux | PaddleOCR / RapidOCR | CUDA | 当前方案 |
| macOS | **RapidOCR** / PaddleOCR CPU | ⚠️ MPS (Metal) | PaddleOCR GPU 版不支持 macOS Metal |
| Windows | PaddleOCR / RapidOCR | CUDA / DirectML | 同 Linux |

**macOS 关键问题：**

- **PaddleOCR GPU 版不支持 Apple Silicon (M1/M2/M3) 的 Metal Performance Shaders (MPS)**
- PaddleOCR 在 macOS 上只能跑 **CPU 版**
- RapidOCR (ONNXRuntime) 在 macOS 上也只能跑 CPU
- 但 Apple Silicon 的 CPU 性能很强，RapidOCR CPU 速度可接受

**macOS 截图 OCR 推荐方案：**

```yaml
# macOS 配置
screen_understanding:
  ocr:
    engine: rapidocr          # 更轻量，CPU 足够快
    use_gpu: false            # macOS 无 CUDA
    # 未来可选: Apple Vision Framework (原生 OCR，速度极快)
```

**Windows 截图 OCR：**

```yaml
# Windows 配置
screen_understanding:
  ocr:
    engine: paddleocr
    use_gpu: true             # NVIDIA CUDA 可用
    # 或 AMD DirectML (Paddle 2.5+ 支持)
```

#### VLM (本地视觉模型)

| 平台 | 推荐方案 | 显存/内存需求 | 备注 |
|------|---------|-------------|------|
| Linux + NVIDIA | Ollama + Qwen2.5-VL 7B INT4 | 6 GB VRAM | 当前方案 |
| macOS + Apple Silicon | Ollama + Qwen2.5-VL 7B INT4 | 6 GB Unified Memory | ✅ 可用，M1 Pro/Max/Ultra 更快 |
| macOS + Intel | Ollama CPU | 6 GB RAM | ⚠️ 慢 |
| Windows + NVIDIA | Ollama + Qwen2.5-VL 7B INT4 | 6 GB VRAM | 同 Linux |
| Windows + AMD | Ollama CPU / DirectML | 6 GB RAM | ⚠️ DirectML 支持有限 |

**好消息：** Ollama 是全平台工具，macOS Apple Silicon 版本优化很好。

**工作量：中（截图工具通用，OCR 需平台适配，VLM 用 Ollama 统一）**

---

### 2.7 终端与编码 ⚠️ Windows 特有

**潜在问题：**

| 问题 | 影响 | 解决方案 |
|------|------|---------|
| Windows 终端默认 GBK 编码 | 中文输出乱码 | `chcp 65001` 或 `PYTHONIOENCODING=utf-8` |
| Windows 路径分隔符 | `Path` 对象处理 | 始终使用 `pathlib.Path` |
| Windows 无 fork() | 多进程 | 使用 `multiprocessing.spawn` |
| Windows 无 SIGTERM | 信号处理 | 用 `CTRL_C_EVENT` / `CTRL_BREAK_EVENT` |
| Windows PowerShell 执行策略 | 脚本无法运行 | `Set-ExecutionPolicy RemoteSigned` |

**代码中需要检查的点：**

```python
# notify.py 中的 PowerShell 脚本
# Windows 可能需要调整 ExecutionPolicy

# daemon.py 中的信号处理
# Windows 不支持 SIGTERM，需要条件编译

if sys.platform == "win32":
    import signal
    signal.signal(signal.SIGBREAK, self._shutdown)  # Ctrl+Break
else:
    signal.signal(signal.SIGTERM, self._shutdown)
```

**工作量：小（1 天）**

---

### 2.8 时区处理 ✅ 已完善

当前代码已经处理了时区问题：

```python
# collector.py
# 查询 aw-server 前转 UTC，返回前转本地时间
```

各平台时区处理一致（Python `datetime` + `zoneinfo`），无需改动。

---

## 三、迁移清单

### 必须修改的文件

| 文件 | 修改内容 | 所有平台 | 仅 macOS | 仅 Windows |
|------|---------|---------|---------|-----------|
| `config.py` | 使用 `platformdirs` 替代硬编码 XDG | ✅ | ✅ | ✅ |
| `notify.py` | macOS 点击支持 / Windows Toast 按钮 | | ✅ | ✅ |
| `daemon.py` | 信号处理兼容 Windows | | | ✅ |
| `cli.py` | `doctor` 命令增加 macOS/Windows 检查 | | ✅ | ✅ |
| `cli.py` | 新增 `install-service` 命令 | ✅ | ✅ | ✅ |

### 新增的文件

| 文件 | 说明 |
|------|------|
| `src/aw_coach/platform.py` | 平台检测、路径适配、服务管理抽象 |
| `src/aw_coach/service_installer.py` | systemd / launchd / Task Scheduler 安装器 |
| `scripts/install-service.sh` | Linux/macOS 服务安装脚本 |
| `scripts/install-service.ps1` | Windows 服务安装脚本 |

---

## 四、分平台实施建议

### 场景 A：迁移到另一台 Linux（如 Fedora/Arch）

**工作量：极小（< 1 天）**

```bash
# 1. 安装 Python 3.9+
sudo dnf install python3 python3-pip  # Fedora
sudo pacman -S python python-pip      # Arch

# 2. 安装 aw-coach
pip install aw-watcher-ai-coach

# 3. 安装 ActivityWatch
# 下载对应发行版的 AppImage / 二进制包

# 4. 安装服务
aw-coach install-service  # 自动使用 systemd

# 5. 启动
systemctl --user start aw-coach
```

**可能需要微调：**
- 通知守护进程名称（GNOME/KDE/XFCE 的 notify-send 都兼容）
- 截图工具（`mss` 纯 Python，无需额外依赖）

---

### 场景 B：迁移到 macOS

**工作量：中等（3-5 天）**

```bash
# 1. 安装依赖
brew install python@3.11 ollama

# 2. 安装 aw-coach
pip install aw-watcher-ai-coach

# 3. ActivityWatch
# 下载 macOS .dmg 或 brew install --cask activitywatch

# 4. 给 ActivityWatch 授权
# 系统设置 -> 隐私与安全性 -> 辅助功能 -> 允许 aw-watcher-window

# 5. 安装服务
aw-coach install-service  # 自动使用 launchd

# 6. OCR 配置调整
# 编辑 ~/.config/activitywatch/aw-watcher-ai-coach.toml
[screen_understanding]
ocr_engine = "rapidocr"  # macOS 用 CPU 版
use_gpu = false
```

**必须处理的事项：**

| # | 事项 | 方案 |
|---|------|------|
| 1 | 配置文件路径 | `platformdirs` 自动映射到 `~/Library/Application Support/` |
| 2 | 服务管理 | 实现 launchd plist 生成 |
| 3 | 通知点击 | 使用 `pync` 或限制为纯文本通知 |
| 4 | OCR GPU | PaddleOCR 在 macOS 只能 CPU，建议 RapidOCR |
| 5 | VLM | Ollama macOS 版支持 Apple Silicon GPU 加速 |
| 6 | ActivityWatch 权限 | 需手动授予辅助功能权限 |

---

### 场景 C：迁移到 Windows

**工作量：高（1-2 周）**

```powershell
# 1. 安装 Python
# 从 python.org 下载安装，勾选 "Add to PATH"

# 2. 安装 aw-coach
pip install aw-watcher-ai-coach

# 3. ActivityWatch
# 下载 Windows installer (.exe)

# 4. 安装服务
aw-coach install-service  # 自动使用 Task Scheduler

# 5. 设置 UTF-8
[Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "User")
```

**必须处理的事项：**

| # | 事项 | 方案 |
|---|------|------|
| 1 | 配置文件路径 | `platformdirs` 映射到 `%LOCALAPPDATA%` |
| 2 | 服务管理 | 实现 Task Scheduler 脚本 + 启动文件夹 |
| 3 | 通知点击 | 使用 `windows-toasts` 库 |
| 4 | 终端编码 | 默认 UTF-8，或自动 `chcp 65001` |
| 5 | 路径分隔符 | 全部使用 `pathlib.Path` |
| 6 | 信号处理 | Windows 无 SIGTERM，改用 SIGBREAK |
| 7 | PowerShell 策略 | 安装脚本需提示用户调整 ExecutionPolicy |
| 8 | OCR | PaddleOCR Windows GPU 版可用，但 CUDA 安装复杂 |
| 9 | VLM | Ollama Windows 版可用 |
| 10 | aw-watcher-web | 仅支持 Chrome 扩展 |

---

## 五、推荐的跨平台抽象设计

建议新增 `src/aw_coach/platform.py`，统一封装平台差异：

```python
"""Cross-platform abstraction layer."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Optional

SYSTEM = platform.system()  # 'Linux', 'Darwin', 'Windows'


class PlatformPaths:
    """Unified config/data/log paths across platforms."""

    @staticmethod
    def config_dir() -> Path:
        from platformdirs import user_config_dir
        return Path(user_config_dir("aw-watcher-ai-coach", "activitywatch"))

    @staticmethod
    def data_dir() -> Path:
        from platformdirs import user_data_dir
        return Path(user_data_dir("aw-watcher-ai-coach", "activitywatch"))

    @staticmethod
    def log_dir() -> Path:
        from platformdirs import user_log_dir
        return Path(user_log_dir("aw-watcher-ai-coach", "activitywatch"))


class ServiceManager:
    """Install/start/stop background service."""

    @classmethod
    def install(cls) -> None:
        if SYSTEM == "Linux":
            cls._install_systemd()
        elif SYSTEM == "Darwin":
            cls._install_launchd()
        elif SYSTEM == "Windows":
            cls._install_task_scheduler()

    @classmethod
    def uninstall(cls) -> None:
        ...

    @classmethod
    def start(cls) -> None:
        ...

    @classmethod
    def stop(cls) -> None:
        ...

    @staticmethod
    def _install_systemd() -> None:
        # 生成 ~/.config/systemd/user/aw-coach.service
        ...

    @staticmethod
    def _install_launchd() -> None:
        # 生成 ~/Library/LaunchAgents/com.activitywatch.aw-coach.plist
        ...

    @staticmethod
    def _install_task_scheduler() -> None:
        # 调用 PowerShell 注册任务计划
        ...
```

---

## 六、总结

| 模块 | Linux→Linux | Linux→macOS | Linux→Windows |
|------|------------|-------------|---------------|
| Python 依赖 | ✅ 零改动 | ✅ 零改动 | ✅ 零改动 |
| 业务逻辑 | ✅ 零改动 | ✅ 零改动 | ✅ 零改动 |
| 配置文件路径 | ✅ XDG 通用 | ⚠️ 需 `platformdirs` | 🔴 必须改 |
| 通知系统 | ✅ dbus 完善 | 🟡 需增强点击 | 🟡 需增强点击 |
| 后台服务 | ✅ systemd | 🟡 需 launchd | 🔴 需 Task Scheduler |
| ActivityWatch | ✅ | 🟡 需辅助功能权限 | 🟡 需窗口钩子 |
| 截图 | ✅ mss 通用 | ✅ mss 通用 | ✅ mss 通用 |
| OCR | ✅ PaddleOCR GPU | 🟡 只能 CPU | ✅ PaddleOCR GPU |
| VLM | ✅ Ollama CUDA | ✅ Ollama Metal | ✅ Ollama CUDA |
| 终端编码 | ✅ | ✅ | 🔴 需 UTF-8 |

**最终建议：**

> **如果目标是"让 aw-coach 可运行在其他平台"，核心工作量约 1 周（主要是路径 + 服务管理 + 通知完善）。**  
> **如果目标是"全平台功能对等"（含 OCR/VLM 优化），工作量约 2-3 周。**
>
> 当前代码架构良好，业务逻辑完全跨平台，真正的成本只在"系统胶水层"。
