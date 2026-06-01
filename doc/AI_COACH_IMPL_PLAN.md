# ActivityWatch AI Coach - 实现计划

> 版本：v1.1（修订版）  
> 日期：2026-05-30  
> 对应设计文档：AI_COACH_DESIGN.md v2.0 + AI_COACH_CLI_DESIGN.md v1.0

---

## 0. 前置决策

### 0.1 项目定位

独立 Python 项目 `aw-watcher-ai-coach`，不修改 ActivityWatch 上游代码。通过 REST API 与 aw-server 交互。

### 0.2 开发环境要求

- Python 3.11+（开发用，兼容运行 3.9+）
- 本地 aw-server 运行在 `localhost:5600`
- 至少有 aw-watcher-window + aw-watcher-afk 产生数据

### 0.3 项目结构

```
aw-watcher-ai-coach/
├── pyproject.toml           # 项目元数据、依赖、构建配置
├── README.md
├── src/
│   └── aw_coach/
│       ├── __init__.py
│       ├── __main__.py      # python -m aw_coach 入口
│       ├── cli.py           # aw-coach CLI（click/typer）
│       ├── config.py        # 配置加载（TOML + pydantic）
│       ├── collector.py     # DataCollector
│       ├── rules/
│       │   ├── __init__.py
│       │   ├── engine.py    # RuleEngine 核心逻辑
│       │   ├── loader.py    # YAML 规则加载 + 热重载
│       │   └── builtin/     # 内置规则 YAML
│       │       ├── global.yml
│       │       └── cn.yml
│       ├── analyzer.py      # PatternAnalyzer（专注度、时间分布）
│       ├── report.py        # ReportGenerator（Markdown 生成）
│       ├── notify.py        # 系统通知抽象层
│       ├── scheduler.py     # APScheduler 调度
│       ├── ai/
│       │   ├── __init__.py
│       │   ├── base.py      # AIBackend ABC
│       │   ├── openai_backend.py
│       │   ├── local_backend.py
│       │   ├── hybrid.py    # HybridBackend
│       │   ├── batch.py     # BatchQueue + 批量分类
│       │   └── cost.py      # CostController
│       ├── screenshot.py    # 按需截图
│       ├── correction.py    # 用户纠正管理
│       ├── storage.py       # SQLite 本地状态
│       └── web/             # 独立 Web 页面（阶段三）
│           ├── __init__.py
│           ├── server.py
│           ├── templates/
│           └── static/
├── tests/
│   ├── test_rules.py
│   ├── test_analyzer.py
│   ├── test_collector.py
│   ├── test_report.py
│   └── test_cost.py
└── rules/                   # 用户自定义规则目录（运行时）
    └── README.md
```

---

## 1. 阶段一：MVP（零配置可用）

**目标**：安装后 `aw-coach status` 立即有输出，无需任何配置或外部依赖。

**工期**：3-4 周，按天拆分如下。

---

### Week 1：项目骨架 + 数据层

#### Day 1-2：项目初始化

**任务**：
1. 创建 `pyproject.toml`，配置 `[project.scripts]` 注册 `aw-coach` 命令。
2. 选择依赖版本并锁定：

```toml
[project]
name = "aw-watcher-ai-coach"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "aw-client>=0.5.0",
    "click>=8.0",
    "pydantic>=2.0",
    "toml>=0.10",
    "apscheduler>=3.10",
    "pyyaml>=6.0",
]

[project.scripts]
aw-coach = "aw_coach.cli:main"

[project.optional-dependencies]
ai = ["openai>=1.0", "httpx>=0.25"]
screenshot = ["mss>=9.0", "pillow>=10.0"]
web = ["flask>=3.0", "jinja2>=3.1"]
dev = ["pytest>=7.0", "ruff>=0.1", "mypy>=1.5", "responses>=0.25"]
```

3. 配置 `ruff`（lint）+ `mypy`（类型检查）。
4. 创建 `src/aw_coach/__init__.py` 和 `__main__.py`。
5. 验证：`pip install -e .` 后 `aw-coach --version` 能输出版本号。

**产出**：可 `pip install -e .` 的空项目骨架。

#### Day 3-4：配置系统

**任务**：

实现 `config.py`：

```python
from pathlib import Path
from pydantic import BaseModel, Field
import toml

CONFIG_PATH = Path("~/.config/activitywatch/aw-watcher-ai-coach.toml").expanduser()

class AnalysisConfig(BaseModel):
    deep_work_threshold_minutes: int = 25
    distraction_apps: list[str] = ["youtube", "bilibili", "twitter", "reddit", "tiktok"]
    social_apps: list[str] = ["wechat", "qq", "slack", "telegram", "discord"]
    work_hours_start: str = "09:00"
    work_hours_end: str = "18:00"
    work_days: list[int] = [1, 2, 3, 4, 5]

class ReportConfig(BaseModel):
    daily_report_time: str = "21:00"
    instant_summary_interval_hours: int = 2
    notification_method: str = "both"  # "notification" / "cli_only" / "both"

class AIConfig(BaseModel):
    backend: str = "rule_only"  # rule_only / local / openai / hybrid

class CostConfig(BaseModel):
    monthly_budget_usd: float = 5.0
    alert_thresholds: list[float] = [0.5, 0.8, 1.0]

class Config(BaseModel):
    analysis: AnalysisConfig = AnalysisConfig()
    report: ReportConfig = ReportConfig()
    ai: AIConfig = AIConfig()
    cost: CostConfig = CostConfig()

def load_config() -> Config:
    if CONFIG_PATH.exists():
        raw = toml.loads(CONFIG_PATH.read_text())
        return Config(**raw)
    return Config()  # 零配置默认
```

**关键点**：
- 配置文件不存在时使用全部默认值——零配置可用。
- 使用 pydantic 做类型校验，配置项有拼写错误时立即报错。

**产出**：`load_config()` 函数通过单元测试（无配置文件 / 部分配置 / 完整配置）。

#### Day 5：DataCollector

**任务**：

实现 `collector.py`：

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from aw_client import ActivityWatchClient

@dataclass
class ActivitySlice:
    start: datetime
    end: datetime
    duration: float  # seconds
    is_afk: bool
    primary_app: str
    primary_title: str
    web_url: str | None = None

class DataCollector:
    def __init__(self):
        self.client = ActivityWatchClient("aw-coach")
        self.hostname = self._detect_hostname()

    def _detect_hostname(self) -> str:
        buckets = self.client.get_buckets()
        for bid in buckets:
            if bid.startswith("aw-watcher-window_"):
                return bid.removeprefix("aw-watcher-window_")
        raise RuntimeError("No aw-watcher-window bucket found. Is ActivityWatch running?")

    def fetch_range(self, start: datetime, end: datetime) -> list[ActivitySlice]:
        windows = self.client.get_events(
            f"aw-watcher-window_{self.hostname}", start=start, end=end
        )
        afk = self.client.get_events(
            f"aw-watcher-afk_{self.hostname}", start=start, end=end
        )
        return self._merge(windows, afk, start, end)

    def fetch_today(self) -> list[ActivitySlice]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.fetch_range(today, datetime.now())

    def _merge(self, windows, afk, start, end) -> list[ActivitySlice]:
        """将 window 事件与 afk 状态按 1 分钟粒度对齐，合并连续状态"""
        from collections import defaultdict
        minute_map: dict[datetime, dict] = defaultdict(
            lambda: {"app": "unknown", "title": "", "url": None, "afk": True}
        )

        for event in windows:
            dur = event.duration
            dur_sec = dur.total_seconds() if hasattr(dur, "total_seconds") else dur
            for i in range(int(dur_sec / 60) + 1):
                m = event.timestamp + timedelta(minutes=i)
                if start <= m < end:
                    minute_map[m].update({
                        "app": event.data.get("app", "unknown"),
                        "title": event.data.get("title", ""),
                        "url": event.data.get("url"),
                    })

        for event in afk:
            dur = event.duration
            dur_sec = dur.total_seconds() if hasattr(dur, "total_seconds") else dur
            status = event.data.get("status", "afk")
            for i in range(int(dur_sec / 60) + 1):
                m = event.timestamp + timedelta(minutes=i)
                if start <= m < end:
                    minute_map[m]["afk"] = (status == "afk")

        slices = []
        sorted_m = sorted(minute_map.keys())
        if not sorted_m:
            return slices

        cur = {"start": sorted_m[0], **minute_map[sorted_m[0]], "count": 1}
        for m in sorted_m[1:]:
            if minute_map[m]["app"] == cur["app"] and minute_map[m]["afk"] == cur["afk"]:
                cur["count"] += 1
            else:
                slices.append(ActivitySlice(
                    start=cur["start"], end=m, duration=cur["count"] * 60,
                    is_afk=cur["afk"], primary_app=cur["app"],
                    primary_title=cur["title"], web_url=cur["url"],
                ))
                cur = {"start": m, **minute_map[m], "count": 1}

        slices.append(ActivitySlice(
            start=cur["start"], end=sorted_m[-1] + timedelta(minutes=1),
            duration=cur["count"] * 60, is_afk=cur["afk"],
            primary_app=cur["app"], primary_title=cur["title"], web_url=cur["url"],
        ))
        return slices
```

**关键决策**：
- 使用官方 `aw-client` 库，不自己实现 HTTP 调用。
- `_detect_hostname()` 自动发现 bucket 名，用户无需配置 hostname。
- 如果 aw-server 未运行，在 CLI 层面友好提示（不在 collector 里 catch 全部异常）。

**测试**：
- mock `aw-client` 返回值，验证 merge 逻辑。
- 集成测试：连接本地 aw-server，验证能拉到数据。

**产出**：`DataCollector.fetch_today()` 返回结构化切片列表。

---

### Week 2：规则引擎 + 分析器

#### Day 6-8：规则引擎

**任务**：

1. 设计 YAML 规则格式，创建 `rules/builtin/global.yml` 和 `rules/builtin/cn.yml`。

2. 实现 `rules/loader.py`（加载 + 合并内置规则与用户自定义规则）。

3. 实现 `rules/engine.py`（匹配逻辑）。

**内置规则库 MVP 范围**（各 50 款，共覆盖 ~100 款应用）：

`global.yml` 重点覆盖：
| 类别 | 应用 |
|------|------|
| IDE/编辑器 | vscode, cursor, intellij, vim/neovim, sublime, emacs, android studio, xcode |
| 终端 | terminal, iterm, konsole, alacritty, kitty, wezterm, gnome-terminal |
| 浏览器 | chrome, firefox, safari, edge, arc, brave |
| 通信 | zoom, teams, slack, discord |
| 设计 | figma, sketch, photoshop, illustrator, blender |
| 文档 | word, pages, google docs(通过浏览器标题), notion, obsidian |
| 娱乐 | 浏览器标题匹配 youtube/netflix/twitch/reddit/twitter |
| 敏感 | 1password, keepassxc, lastpass, bitwarden |

`cn.yml` 重点覆盖：
| 类别 | 应用 |
|------|------|
| 通信 | 飞书(Lark), 钉钉(DingTalk), 企业微信(WXWork), 微信(WeChat), QQ |
| 办公 | WPS Office, 腾讯文档, 语雀, 石墨文档, 幕布 |
| 会议 | 腾讯会议(wemeet), 飞书会议, 钉钉会议 |
| 娱乐 | bilibili(浏览器标题), 抖音, 知乎(标题匹配) |
| 开发 | HBuilderX |

**规则匹配优先级**：
1. 精确应用名匹配（进程名，大小写不敏感）
2. 应用名模糊匹配（contains）
3. 子规则：窗口标题关键词
4. 子规则：URL 域名
5. 通用标题关键词（跨应用）

```python
@dataclass
class RuleResult:
    activity_type: str
    confidence: float
    method: str  # "rule_app_exact" / "rule_sub_title" / "rule_sub_url" / "rule_keyword"
    rule_name: str | None = None

class RuleEngine:
    def __init__(self, rules_dirs: list[Path]):
        self.rules = RuleLoader(rules_dirs).load_all()

    def classify(self, app: str, title: str, url: str | None = None) -> RuleResult:
        app_lower = app.lower()

        # 1. 精确匹配（应用名在规则列表中）
        for rule in self.rules:
            match_apps_lower = [m.lower() for m in rule.match_apps]
            if app_lower in match_apps_lower:
                if rule.confidence >= 0.85:
                    return RuleResult(rule.default_type, rule.confidence, "rule_app_exact", rule.name)
                # 2. 子规则细化
                if rule.sub_rules:
                    for sub in rule.sub_rules:
                        if sub.matches(title, url):
                            return RuleResult(sub.type, sub.confidence, "rule_sub", rule.name)
                return RuleResult(rule.default_type, rule.confidence, "rule_app_fuzzy", rule.name)

        # 2. 子串匹配（如 "terminal" 匹配 "gnome-terminal"）
        for rule in self.rules:
            if any(app_lower in m.lower() for m in rule.match_apps):
                return RuleResult(rule.default_type, rule.confidence, "rule_app_contains", rule.name)

        # 3. 标题关键词
        for rule in self.keyword_rules:
            if rule.matches_title(title):
                return RuleResult(rule.type, rule.confidence, "rule_keyword", rule.name)

        return RuleResult("unknown", 0.0, "rule_miss", None)
```

**测试用例**：
- `("Code", "main.py - myproject", None)` → `programming, 0.90`
- `("chrome", "YouTube - Watching", "youtube.com/watch")` → `entertainment, 0.95`
- `("Lark", "飞书会议 - Sprint Planning", None)` → `meeting, 0.90`
- `("unknown-bin", "MainWindow", None)` → `unknown, 0.0`

**产出**：规则引擎通过上述测试，覆盖 100 款应用。

#### Day 9-10：Pattern Analyzer

**任务**：

实现 `analyzer.py`：

```python
@dataclass
class AnalysisResult:
    total_hours: float
    effective_hours: float  # 非 AFK 非娱乐
    deep_work_hours: float
    focus_score: int  # 0-100
    switch_count: int
    activity_breakdown: dict[str, float]  # type -> hours
    hourly_scores: list[tuple[int, int]]  # (hour, score)

class PatternAnalyzer:
    def __init__(self, config: AnalysisConfig):
        self.config = config

    def analyze(self, slices: list[ActivitySlice], rules: list[RuleResult]) -> AnalysisResult:
        # 1. 按 activity_type 聚合
        breakdown = self._aggregate_by_type(slices, rules)

        # 2. 计算有效工作时长
        effective = self._effective_hours(slices, rules)

        # 3. 计算深度工作时长
        deep_work = self._deep_work_hours(slices, rules)

        # 4. 计算专注度
        focus = self._focus_score(slices, rules)

        # 5. 任务切换次数
        switches = self._count_switches(slices, rules)

        # 6. 每小时得分
        hourly = self._hourly_scores(slices, rules)

        return AnalysisResult(
            total_hours=sum(s.duration for s in slices if not s.is_afk) / 3600,
            effective_hours=effective,
            deep_work_hours=deep_work,
            focus_score=focus,
            switch_count=switches,
            activity_breakdown=breakdown,
            hourly_scores=hourly,
        )
```

**深度工作判定逻辑**：
- 连续 ≥25 分钟（可配置）在同一 activity_type 上（programming/writing/design/research）。
- "连续"允许最多 2 分钟的 AFK 间隔（上厕所不算中断）。

**专注度得分**：
- 基础分 60。
- 深度工作每 30 分钟 +10（上限 +30）。
- 每次切换 -3（上限 -30）。
- 娱乐占比每 10% -10。
- 最终 clamp 到 [0, 100]。

**产出**：`PatternAnalyzer.analyze()` 通过单元测试（构造各种切片组合验证算分逻辑）。

---

### Week 3：报告 + CLI + 通知

#### Day 11-12：Report Generator

**任务**：

实现 `report.py`，生成 Markdown 日报：

```python
class ReportGenerator:
    def generate_daily(self, date: date, analysis: AnalysisResult, rules: list[RuleResult]) -> str:
        """生成 Markdown 格式日报"""
        # 使用 f-string 或简单模板，不引入 Jinja2（MVP 阶段）
        ...

    def _append_correction_prompt(self, date: date, rules: list[RuleResult]) -> str:
        """日报末尾追加低置信度纠正引导"""
        uncertain = [r for r in rules if r.confidence < 0.70 or r.activity_type == "unknown"]
        if not uncertain:
            return ""
        return f"""

---

## 🤔 需要纠正？

今日有 **{len(uncertain)}** 个低置信度分类。

运行以下命令确认或纠正：
```bash
aw-coach correct --review
```
"""

    def generate_status(self, analysis: AnalysisResult) -> str:
        """生成 CLI status 的终端输出（带颜色和进度条）"""
        ...

    def save_daily(self, date: date, content: str) -> Path:
        """保存到 ~/.local/share/activitywatch/reports/daily/{date}.md"""
        ...
```

**日报模板**：

```
# 工作效率日报 - {date}

## 今日概览
| 指标 | 数值 |
|------|------|
| 有效工作时长 | {effective_hours} |
| 深度工作时长 | {deep_work_hours} |
| 专注得分 | {focus_score}/100 |
| 任务切换 | {switch_count} 次 |

## 时间分布
{按时长降序排列的 activity_type 列表，带 ASCII 进度条}

## 精力曲线
{每小时得分，用 emoji 标记高/中/低}

## 建议
{基于规则的简单建议，无需 LLM}
```

**建议生成逻辑（纯规则，无 LLM）**：

```python
def generate_rule_suggestions(analysis: AnalysisResult) -> list[str]:
    suggestions = []

    if analysis.switch_count > 20:
        suggestions.append("今日任务切换较频繁，建议使用番茄工作法减少中断。")

    if analysis.deep_work_hours < 1.0:
        suggestions.append("深度工作时长不足 1 小时，尝试划出一段无打扰时间。")

    if analysis.hourly_scores:
        best_hour = max(analysis.hourly_scores, key=lambda x: x[1])
        suggestions.append(f"你在 {best_hour[0]}:00 左右效率最高，建议安排重要任务。")

    entertainment = analysis.activity_breakdown.get("entertainment", 0)
    if entertainment > 2.0:
        suggestions.append(f"今日娱乐时间 {entertainment:.1f}h，注意平衡。")

    return suggestions[:5]  # 最多 5 条
```

**产出**：给定 AnalysisResult 能生成格式正确的 Markdown 日报。

#### Day 13-15：CLI 实现

**任务**：

实现 `cli.py`，MVP 阶段支持以下命令：

```python
import click

@click.group()
@click.version_option()
@click.option("-v", "--verbose", is_flag=True, help="详细输出（等同于 LOG_LEVEL=debug）")
@click.option("-q", "--quiet", is_flag=True, help="静默模式（等同于 LOG_LEVEL=warning）")
@click.pass_context
def main(ctx, verbose, quiet):
    """ActivityWatch AI Coach - 工作效率分析工具"""
    import logging
    if verbose:
        logging.getLogger("aw_coach").setLevel(logging.DEBUG)
    elif quiet:
        logging.getLogger("aw_coach").setLevel(logging.WARNING)
    else:
        logging.getLogger("aw_coach").setLevel(logging.INFO)
    pass

@main.command()
@click.option("--full", is_flag=True, help="显示完整报告（可能调用 LLM）")
@click.option("--dry-run", is_flag=True, help="仅输出 prompt，不实际调用 API")
@click.argument("date", default="today")
def report(date, full, dry_run):
    """查看日报。基于后台服务已写入 ai-coach bucket 的分析结果，不重新做分类。"""
    # 1. 解析日期（today/yesterday/YYYY-MM-DD）
    # 2. 从 ai-coach bucket 读取该日期的分析结果（或本地 SQLite）
    # 3. 调用 ReportGenerator 渲染
    # 4. 若 --full：基于已有分析结果调用 LLM 生成个性化建议文本
    # 5. 若 --dry-run：输出 LLM prompt 到终端，不调用 API

@main.command()
def status():
    """实时状态"""
    # 1. 从 ai-coach bucket 读取今日已分析数据
    # 2. 若无数据，回退到 DataCollector 直接拉取原始数据并即时分析
    # 3. 渲染终端输出

@main.command()
def doctor():
    """诊断运行状态"""
    # 检查 aw-server 连通性
    # 检查 bucket 是否存在
    # 检查规则库加载
    # 检查截图权限（如果启用）
    # 检查 AI 后端配置
    # 检查本地 LLM（Ollama）可用性
    # 检查 SQLite 数据库状态

@main.command()
@click.option("--last", is_flag=True, help="纠正最近一次分类")
@click.option("--time", help="纠正指定时段（如 14:00-15:00）")
@click.option("--interactive", is_flag=True, help="交互式逐条确认低置信度分类")
@click.argument("activity_type", required=False)
def correct(last, time, interactive, activity_type):
    """纠正 AI 分类结果"""
    # 写入本地 SQLite corrections 表，关联到 ai-coach bucket 中的分析 event

@main.command()
@click.option("--app", required=True, help="应用名称")
@click.option("--title", default="", help="窗口标题")
@click.option("--url", default=None, help="浏览器 URL")
def rule_test(app, title, url):
    """测试规则匹配结果"""
    # 调用 RuleEngine.classify()，输出匹配过程和置信度

@main.command()
def cost():
    """查看 AI API 成本使用情况"""
    # 从 SQLite cost_log 表读取本月用量

@main.command()
def open():
    """用浏览器打开 AI Coach 报告面板（静态 HTML）"""
    # 生成/刷新静态 HTML，调用系统默认浏览器打开

@main.command()
@click.option("--port", default=5601, help="临时服务器端口")
def serve(port):
    """启动临时 Web 服务器（支持交互式纠正）"""
    # 启动 Flask，提供 dashboard + correction API

@main.group()
def config():
    """配置管理（MVP 阶段直接编辑 TOML，此命令为提示用）"""
    pass

@config.command("list")
def config_list():
    """列出当前有效配置"""
    click.echo("当前配置来自 ~/.config/activitywatch/aw-watcher-ai-coach.toml")
    click.echo("MVP 阶段请直接编辑该文件。")
    click.echo("常用配置项：")
    click.echo("  ai.backend = "rule_only" | "hybrid" | "openai" | "local"")
    click.echo("  cost.monthly_budget_usd = 5.0")
    click.echo("  screenshot.enabled = false")
```

**CLI 输出美化**：
- 使用 `click.style()` 做颜色高亮。
- 进度条用 Unicode 字符：`█` 和 `░`。
- 不引入 `rich` 库（减少依赖），如果后续需要再加。

**无参数运行引导**（`aw-coach` 不带任何子命令）：
```python
@main.command(name="summary", invoke_without_command=True)
@click.pass_context
def summary(ctx):
    """无参数运行时显示今日摘要 + 常用命令引导"""
    if ctx.invoked_subcommand is None:
        # 显示今日快速摘要
        click.echo("🧠 AI Coach - 今日摘要")
        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        # 从 ai-coach bucket 读取今日数据并渲染...
        click.echo("")
        click.echo("💡 常用命令:")
        click.echo("  aw-coach status      实时状态")
        click.echo("  aw-coach report      查看日报")
        click.echo("  aw-coach doctor      诊断环境")
        click.echo("  aw-coach --help      查看全部命令")
```

**doctor 命令检查项**：

```
aw-coach doctor

✅ aw-server: reachable at localhost:5600
✅ window bucket: aw-watcher-window_myhost (12,345 events today)
✅ afk bucket: aw-watcher-afk_myhost
✅ rule engine: loaded 142 rules (global: 92, cn: 50)
⚠️  platform: Wayland detected, screenshot requires xdg-desktop-portal
ℹ️  ai backend: rule_only (no API key configured)
ℹ️  cost: $0.00 / $5.00 this month
```

**产出**：`aw-coach status`、`aw-coach report`、`aw-coach doctor` 可用。

#### Day 16：系统通知 + 调度

**任务**：

1. 实现 `notify.py`：

```python
import platform
import subprocess

def send_notification(title: str, body: str):
    system = platform.system()
    if system == "Linux":
        subprocess.run(["notify-send", title, body], check=False)
    elif system == "Darwin":
        script = f'display notification "{body}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], check=False)
    elif system == "Windows":
        # powershell toast (可选, MVP 先跳过)
        pass
```

2. 实现 `scheduler.py`（MVP 仅启用日报定时任务）：

```python
from apscheduler.schedulers.background import BackgroundScheduler

class CoachScheduler:
    def __init__(self, config: Config):
        self.scheduler = BackgroundScheduler()
        self.config = config

    def start(self):
        # 每小时批量分析（核心：拉取 → 分类 → 分析 → 写入 bucket）
        self.scheduler.add_job(
            self.hourly_batch_analyze,
            "cron", minute=0  # 每小时的第 0 分钟执行
        )
        # 即时摘要
        self.scheduler.add_job(
            self.send_instant_summary,
            "cron", minute="0,30"  # 每 2 小时（简化：每 30 分钟检查，实际按配置间隔）
        )
        # 日报生成
        hour, minute = map(int, self.config.report.daily_report_time.split(":"))
        self.scheduler.add_job(
            self.generate_and_notify_daily,
            "cron", hour=hour, minute=minute
        )
        self.scheduler.start()

    def hourly_batch_analyze(self):
        """每小时核心流程：拉取原始数据 → 分类 → 分析 → 写入 ai-coach bucket"""
        from datetime import datetime, timedelta
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)

        # 1. 拉取数据
        slices = self.collector.fetch_range(hour_ago, now)
        if not slices:
            return

        # 2. 规则引擎分类
        results = []
        uncertain = []
        for s in slices:
            r = self.rule_engine.classify(s.primary_app, s.primary_title, s.web_url)
            results.append(r)
            if r.confidence < self.config.ai.hybrid.rule_confidence_threshold:
                uncertain.append(s)

        # 3. 批量 LLM（如果启用且有预算）
        if uncertain and self.config.ai.backend != "rule_only":
            self.batch_queue.enqueue(uncertain)
            pending = self.batch_queue.get_pending(limit=8)
            if pending:
                est_cost = self.llm.estimate_cost("batch_classify", len(pending))
                if self.cost.can_use_llm(est_cost):
                    llm_results = self.llm.batch_classify(pending)
                    self.cost.track_call(...)
                    # 回填 results
                    # ...

        # 4. 模式分析
        analysis = self.analyzer.analyze(slices, results)

        # 5. 写入 aw-server bucket（每小时一个 event）
        self._write_hourly_event(hour_ago, analysis, results)

    def _write_hourly_event(self, hour_start: datetime, analysis, results):
        """将分析结果写入 ai-coach bucket"""
        from aw_core.models import Event
        event = Event(
            timestamp=hour_start,
            duration=3600,
            data={
                "activity_type": max(set(r.activity_type for r in results), key=lambda t: sum(1 for r in results if r.activity_type == t)),
                "confidence": sum(r.confidence for r in results) / len(results),
                "classification_method": "rule" if all(r.method.startswith("rule") for r in results) else "hybrid",
                "focus_score": analysis.focus_score,
                "switch_count": analysis.switch_count,
                "deep_work_minutes": analysis.deep_work_hours * 60,
                "slice_count": len(results),
            }
        )
        self.collector.client.insert_event(f"ai-coach_{self.collector.hostname}", event)
```

3. 实现 `__main__.py`（作为 watcher 后台运行）：

```python
"""python -m aw_coach 作为后台服务运行"""
from aw_coach.scheduler import CoachScheduler
from aw_coach.config import load_config

def run():
    config = load_config()
    scheduler = CoachScheduler(config)
    scheduler.start()
    # 保持进程活跃
    ...

if __name__ == "__main__":
    run()
```

**产出**：后台进程可定时生成日报并发送系统通知。

---

### Week 4：集成测试 + 首次体验优化

#### Day 17-18：集成测试

**任务**：

1. 端到端测试：启动 aw-server（测试实例）→ 注入模拟数据 → 运行 `aw-coach report` → 验证输出。
2. 边界条件：
   - 当天无数据时的友好提示。
   - 只有 30 分钟数据时不显示"精力曲线"（数据不足）。
   - aw-server 未运行时的错误信息。

3. 测试策略补充：

**Mock aw-server：**
```python
# conftest.py
import responses

@pytest.fixture
def mock_aw_server():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "http://localhost:5600/api/0/info",
                 json={"version": "0.12.0"})
        rsps.add(responses.GET, "http://localhost:5600/api/0/buckets",
                 json={"aw-watcher-window_test": {"type": "currentwindow"}})
        rsps.add(responses.GET, responses.GET,
                 url_pattern=r"http://localhost:5600/api/0/buckets/.*/events",
                 json=[])
        yield rsps
```

**覆盖率目标**：单元测试 ≥ 70%，核心模块（rules, analyzer, cost）≥ 80%。

**关键测试场景**：
- `rule_only` 模式全天运行：零 API 调用。
- `hybrid` 模式：模拟规则覆盖率 80%，验证每日 API 调用 ≤ 12 次。
- 超预算降级：设 budget=$0.01，验证首次调用后即降级。
- JSON 解析失败：LLM 返回非法 JSON 时标记 unknown，不 crash。
- 时间轴对齐：window + afk 事件重叠/间隙时的 merge 正确性。
- 首次运行：无历史数据时的友好提示；有历史数据时立即生成报告。

#### Day 19：首次运行体验

**任务**：

解决"首次安装当天无数据"的空白体验问题：

```python
def first_run_check(collector: DataCollector, analyzer: PatternAnalyzer, reporter: ReportGenerator):
    """首次运行：立即分析历史数据，让用户立即看到价值"""
    today_slices = collector.fetch_today()
    if len(today_slices) < 5:
        week_ago = datetime.now() - timedelta(days=7)
        history = collector.fetch_range(week_ago, datetime.now())
        if history:
            click.echo("🎉 发现过去 7 天的历史数据，正在生成首份回顾报告...")
            # 直接分析历史数据，无需等待后台服务积累
            rules = [rule_engine.classify(s.primary_app, s.primary_title, s.web_url) for s in history]
            analysis = analyzer.analyze(history, rules)
            report_path = reporter.save_weekly_review(week_ago, datetime.now(), analysis)
            click.echo(f"✅ 首份报告已生成: {report_path}")
            click.echo("运行 `aw-coach report` 随时查看最新日报。")
        else:
            click.echo("⏳ 数据积累中。ActivityWatch 运行数小时后，再来查看报告。")
            click.echo("提示: 运行 `aw-coach doctor` 确认一切正常。")
```

#### Day 20-21：文档 + 打包

**任务**：

1. 编写 README.md（安装步骤、快速开始、配置说明）。
2. 确保 `pip install aw-watcher-ai-coach` 后 `aw-coach` 命令可用。
3. 验证在 Python 3.9/3.10/3.11/3.12 下均能运行。
4. 可选：创建 `aw-watcher-ai-coach.desktop` 和 systemd user service。

**阶段一完成标准**：

- [ ] `pip install aw-watcher-ai-coach` 一键安装
- [ ] `aw-coach status` 显示今日实时统计（优先读取 ai-coach bucket）
- [ ] `aw-coach report` 显示完整日报（规则分类 + 分析 + 建议）
- [ ] `aw-coach report --full` 基于已有分析结果生成 LLM 建议（dry-run 可用）
- [ ] `aw-coach doctor` 诊断环境
- [ ] `aw-coach rule test --app X --title Y` 测试规则匹配
- [ ] `aw-coach correct --last <type>` 快速纠正最近一次分类
- [ ] `aw-coach correct --review` 交互式逐条确认低置信度分类
- [ ] `aw-coach cost` 显示成本使用情况（零成本时显示 $0.00）
- [ ] 后台服务每小时批量分析并写入 ai-coach bucket
- [ ] 后台服务每天定时生成日报并通知
- [ ] `aw-coach open` 打开静态 HTML 报告面板
- [ ] 零配置、零外部依赖（不需要 LLM）
- [ ] 规则引擎覆盖 100 款常见应用

---

## 2. 阶段二：智能增强

**目标**：引入批量 LLM、按需截图、成本控制。  
**工期**：3-4 周

---

### Week 5：SQLite 存储 + 成本控制

#### Day 22-23：SQLite 本地状态

**任务**：

实现 `storage.py`，统一管理本地状态：

```python
import sqlite3
from pathlib import Path

DB_PATH = Path("~/.local/share/activitywatch/aw-coach.db").expanduser()

class Storage:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self._migrate()

    def _migrate(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                app TEXT,
                title TEXT,
                original_type TEXT,
                corrected_type TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cost_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cost_usd REAL,
                operation TEXT
            );
            CREATE TABLE IF NOT EXISTS batch_queue (
                id INTEGER PRIMARY KEY,
                slice_start TEXT,
                slice_end TEXT,
                app TEXT,
                title TEXT,
                url TEXT,
                rule_confidence REAL,
                processed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
```

**设计理由**：
- 纠正记录、成本日志、批量队列都需要高效查询（如"本月总成本"、"某 app 被纠正过几次"）。
- SQLite 单文件、无进程依赖、与 ActivityWatch 自身方案一致。

#### Day 24-25：CostController

**任务**：

实现 `ai/cost.py`：

```python
class CostController:
    # 默认价格表（USD per 1K tokens）
    # 用户可通过 TOML 配置覆盖，支持 OpenAI 调价时无需升级代码
    DEFAULT_PRICING = {
        "gpt-4o": {"input": 0.0025, "output": 0.01},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    }

    def __init__(self, config: CostConfig, storage: Storage):
        self.budget = config.monthly_budget_usd
        self.storage = storage
        self.alerts_sent: set[float] = set()
        # 支持配置覆盖默认价格
        self.pricing = {**self.DEFAULT_PRICING, **getattr(config, "pricing_override", {})}

    def __init__(self, config: CostConfig, storage: Storage):
        self.budget = config.monthly_budget_usd
        self.storage = storage
        self.alerts_sent: set[float] = set()

    def this_month_total(self) -> float:
        return self.storage.get_monthly_cost()

    def can_use_llm(self, estimated_cost: float) -> bool:
        if self.this_month_total() + estimated_cost > self.budget:
            self._notify_budget_exceeded()
            return False
        self._check_alert_thresholds()
        return True

    def track_call(self, model: str, input_tokens: int, output_tokens: int, operation: str):
        pricing = self.pricing.get(model, self.DEFAULT_PRICING["gpt-4o-mini"])
        cost = (input_tokens / 1000 * pricing["input"] +
                output_tokens / 1000 * pricing["output"])
        self.storage.record_cost(model, input_tokens, output_tokens, cost, operation)

    def _notify_budget_exceeded(self):
        send_notification(
            "AI Coach 预算提醒",
            f"本月 AI 预算 ${self.budget} 已用完，已切换到规则模式。下月自动恢复。"
        )
```

**产出**：成本控制器通过单元测试（正常/超预算/阈值告警）。

---

### Week 6：批量 LLM 分类

#### Day 26-28：BatchQueue + OpenAI Backend

**任务**：

1. 实现 `ai/batch.py`（批量队列）：

```python
class BatchQueue:
    def __init__(self, storage: Storage):
        self.storage = storage

    def enqueue(self, slices: list[ActivitySlice]):
        """将规则未确定的切片加入队列"""
        for s in slices:
            self.storage.add_to_batch_queue(s)

    def get_pending(self, limit: int = 8) -> list[ActivitySlice]:
        """获取待处理切片（最多 8 个，防止 LLM 输出不稳定）"""
        return self.storage.get_pending_batch(limit=limit)

    def mark_processed(self, ids: list[int]):
        self.storage.mark_batch_processed(ids)
```

2. 实现 `ai/openai_backend.py`：

```python
import openai
import json

class OpenAIBackend(AIBackend):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def batch_classify(self, slices: list[ActivitySlice]) -> list[ClassificationResult]:
        prompt = self._build_batch_prompt(slices)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        raw = response.choices[0].message.content
        results = self._parse_response(raw, len(slices))
        return results

    def _parse_response(self, raw: str, expected_count: int) -> list[ClassificationResult]:
        """健壮的解析：schema 校验 + 缺失填充"""
        try:
            data = json.loads(raw)
            items = data.get("classifications", data.get("results", []))
        except json.JSONDecodeError:
            # JSON 解析失败，全部标记为 unknown，不重试
            logger.warning("LLM returned invalid JSON, falling back to unknown")
            return [ClassificationResult("unknown", 0.0, "llm_parse_error")] * expected_count

        results = []
        for i in range(expected_count):
            if i < len(items):
                item = items[i]
                results.append(ClassificationResult(
                    activity_type=item.get("activity_type", "unknown"),
                    confidence=item.get("confidence", 0.5),
                    method="llm_batch",
                ))
            else:
                # LLM 漏掉了某些切片
                results.append(ClassificationResult("unknown", 0.0, "llm_missing"))

        return results

    def estimate_cost(self, operation: str, count: int) -> float:
        if operation == "batch_classify":
            # 估算：每切片约 50 tokens input + 30 tokens output
            input_tokens = 200 + count * 50  # prompt 开头 + 每切片
            output_tokens = count * 30
            pricing = CostController.PRICING.get(self.model, {})
            return (input_tokens / 1000 * pricing.get("input", 0.001) +
                    output_tokens / 1000 * pricing.get("output", 0.002))
        return 0.02  # 默认估算
```

3. 实现 `ai/hybrid.py`（如设计文档 5.3 节所述）。

**关键健壮性措施**：
- JSON 解析失败不重试（避免双倍成本），直接 fallback。
- 单次批量最多 8 个切片。
- 返回结果数量不匹配时，缺失部分填充 `unknown`。

#### [移至阶段三 Week 11] Ollama 本地后端

> **优先级调整**：Ollama 支持移至阶段三，阶段二专注完成 hybrid（规则 + OpenAI）核心路径。
>
> 理由：
> - Ollama 用户基数较小（需要本地 GPU/大内存）。
> - OpenAI gpt-4o-mini 成本极低（$0.15/M tokens），hybrid 模式下月成本 < $5。
> - 优先保证 hybrid 路径稳定，再扩展本地 LLM。

**任务**：

实现 `ai/local_backend.py`：

```python
import httpx

class LocalLLMBackend(AIBackend):
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3"):
        self.base_url = base_url
        self.model = model

    def batch_classify(self, slices: list[ActivitySlice]) -> list[ClassificationResult]:
        prompt = self._build_batch_prompt(slices)
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=60.0,  # 本地模型可能较慢
        )
        return self._parse_response(response.json()["response"], len(slices))

    def estimate_cost(self, operation: str, count: int) -> float:
        return 0.0  # 本地无货币成本
```

**启动时检测 Ollama 可用性**：

```python
def check_ollama_available(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=3.0)
        return resp.status_code == 200
    except httpx.ConnectError:
        return False
```

若 Ollama 不可用，`aw-coach doctor` 中提示：

```
⚠️  local LLM: Ollama not reachable at localhost:11434
    Install: https://ollama.com/download
    Or switch to rule_only mode (default, no LLM needed)
```

---

### Week 7：即时摘要 + 用户纠正 + 按需截图（配置级）

#### Day 31-32：即时摘要

**任务**：

在 scheduler 中每 2 小时触发，生成轻量摘要并推送通知：

```python
def send_instant_summary(self):
    now = datetime.now()
    start = now - timedelta(hours=2)
    slices = self.collector.fetch_range(start, now)
    if not slices:
        return

    rules = [self.rule_engine.classify(s.primary_app, s.primary_title, s.web_url) for s in slices]
    analysis = self.analyzer.analyze(slices, rules)

    summary = (
        f"过去 2 小时: 有效工作 {analysis.effective_hours:.1f}h, "
        f"专注度 {analysis.focus_score}/100\n"
        f"主要活动: {max(analysis.activity_breakdown, key=analysis.activity_breakdown.get)}"
    )
    send_notification("AI Coach 摘要", summary)
```

#### Day 33-34：用户纠正系统

**任务**：

实现 `correction.py` + CLI `correct` 命令：

```python
class CorrectionManager:
    def __init__(self, storage: Storage, rule_engine: RuleEngine):
        self.storage = storage
        self.rule_engine = rule_engine

    def correct_last(self, correct_type: str):
        """纠正最近一次分析结果（关联 ai-coach bucket 中的 event）"""
        # 从本地 SQLite 读取最近一次低置信度分析结果
        last = self.storage.get_last_uncertain_analysis()
        if not last:
            click.echo("最近没有低置信度分类需要纠正。")
            return
        self.storage.add_correction(
            analysis_event_id=last.event_id,
            app=last.app,
            title=last.title,
            url=last.url,
            original_type=last.activity_type,
            corrected_type=correct_type,
        )
        click.echo(f"✅ 已纠正 {last.start}-{last.end}: {last.activity_type} → {correct_type}")

    def correct_range(self, start: datetime, end: datetime, correct_type: str):
        """纠正指定时间段内的所有分析结果"""
        affected = self.storage.get_analysis_in_range(start, end)
        for item in affected:
            self.storage.add_correction(
                analysis_event_id=item.event_id,
                app=item.app, title=item.title, url=item.url,
                original_type=item.activity_type,
                corrected_type=correct_type,
            )
        click.echo(f"✅ 已纠正 {len(affected)} 个切片")

    def review_uncertain(self) -> list[dict]:
        """列出今日低置信度切片供用户确认"""
        # confidence < 0.70 或 activity_type == "unknown"
        return self.storage.get_today_uncertain_analyses(threshold=0.70)

    def suggest_rules(self) -> list[dict]:
        """基于高频纠正生成规则建议"""
        corrections = self.storage.get_corrections_last_30_days()
        # 按 (app_lower, title_keyword, corrected_type) 聚类
        from collections import Counter
        counter = Counter(
            (c.app.lower(), c.corrected_type) for c in corrections
        )
        suggestions = []
        for (app, ctype), count in counter.items():
            if count >= 3 and not self.rule_engine.has_confident_rule(app):
                suggestions.append({
                    "app": app,
                    "type": ctype,
                    "count": count,
                    "suggested_confidence": min(0.70 + count * 0.05, 0.95),
                })
        return suggestions
```

**CLI `correct --review` 交互**：

```bash
$ aw-coach correct --review

今日低置信度切片（共 5 个）:

[1] 10:30-10:45  app=chrome  title="Product Hunt - chrome"
    当前分类: research (confidence: 0.60)
    → 正确类型? [p]rogramming [r]esearch [e]ntertainment [s]kip: e

[2] 14:00-14:15  app=unknown-app  title="MainWindow"
    当前分类: unknown (confidence: 0.00)
    → 正确类型? [p]rogramming [m]eeting [a]dmin [s]kip: s

已保存 1 条纠正。运行 `aw-coach rule suggest` 查看规则建议。
```

#### Day 35-36：按需截图（可选依赖）

**任务**：

实现 `screenshot.py`（仅在 `pip install aw-watcher-ai-coach[screenshot]` 时可用）：

```python
class ScreenshotCapture:
    def __init__(self, config):
        self.config = config
        self.capability = PlatformChecker().check_screenshot_capability()
        self.last_capture_time = None
        self.cooldown = timedelta(minutes=10)

    def maybe_capture(self, slice: ActivitySlice) -> bytes | None:
        if not self.config.screenshot_enabled:
            return None
        if not self.capability.available:
            return None
        if slice.primary_app.lower() in self.config.blocklist_apps:
            return None
        if self.last_capture_time and (datetime.now() - self.last_capture_time) < self.cooldown:
            return None

        # 配置级授权：用户显式设置 screenshot.enabled = true 即为同意
        # 首次启用时 doctor 会提示风险，不会静默截图
        img_bytes = self._capture()
        self.last_capture_time = datetime.now()
        return img_bytes
```

**隐私设计**：
- 默认关闭（`screenshot.enabled = false`）。
- 开启方式：`aw-coach config set screenshot.enabled true` 或直接编辑 TOML。
- `aw-coach doctor` 检测截图权限时，若已启用则明确提示风险。
- blocklist 在截图前检查，敏感应用直接跳过。
- retention_hours 默认 0，分析后立即删除。

---

### Week 8：集成 + 阶段二验收

#### Day 37-38：HybridBackend 集成

将所有 AI 组件串联：

```python
# scheduler 中的 hourly batch 流程
def hourly_batch_analyze(self):
    # 1. 拉取过去 1 小时数据
    slices = self.collector.fetch_range(hour_ago, now)

    # 2. 规则引擎分类
    results = []
    uncertain = []
    for s in slices:
        r = self.rule_engine.classify(s.primary_app, s.primary_title, s.web_url)
        results.append(r)
        if r.confidence < 0.85:
            uncertain.append(s)

    # 3. 批量 LLM（如果启用且有预算）
    if uncertain and self.config.ai.backend != "rule_only":
        self.batch_queue.enqueue(uncertain)
        pending = self.batch_queue.get_pending(limit=8)
        if pending and self.cost.can_use_llm(self.llm.estimate_cost("batch_classify", len(pending))):
            llm_results = self.llm.batch_classify(pending)
            self.cost.track_call(...)
            # 回填结果
            ...

    # 4. 写入 bucket
    self._write_to_bucket(slices, results)
```

#### Day 39-40：集成测试 + 成本验证

**测试场景**：
1. `rule_only` 模式全天运行：确认零 API 调用。
2. `hybrid` 模式：模拟规则覆盖率 80%，验证每日 API 调用 ≤12 次。
3. 超预算降级：设 budget=$0.01，验证首次调用后即降级。
4. Ollama 不可用时优雅 fallback。

**阶段二完成标准**：

- [ ] `hybrid` 模式下月成本可控在 $5 以内（批量处理 + gpt-4o-mini）
- [ ] 批量 LLM 分类结果正确回填到 ai-coach bucket
- [ ] JSON 解析失败时优雅 fallback（不重试、不 crash）
- [ ] `aw-coach cost` 显示当月用量和预算剩余
- [ ] `aw-coach correct --review` 交互式纠正可用
- [ ] `aw-coach correct --last <type>` 快速纠正可用
- [ ] `aw-coach correct --interactive` 全量逐条确认可用
- [ ] 即时摘要每 2 小时通知
- [ ] 截图模块可选安装，配置级授权，启动时检测平台权限
- [ ] 日报末尾自动追加低置信度纠正引导
- [ ] `aw-coach report --full --dry-run` 可用
- [ ] `aw-coach rule test` 可用
- [ ] `-v/--verbose` 和 `-q/--quiet` 全局选项可用

---

## 3. 阶段三：Web UI + 社区生态

**目标**：独立 Web 页面 + 社区规则库同步。  
**工期**：3-4 周

---

### Week 9-10：独立 Web 页面

#### 方案：混合方案——静态 HTML 为主 + 临时服务器为辅

**核心决策**：
- **默认**：`aw-coach open` 生成并打开静态 HTML（零资源占用）。
- **按需**：`aw-coach serve` 启动临时 Flask 服务器（支持交互式纠正 API）。

**理由**：
- 静态 HTML：日常查看报告足够，零内存占用，无端口冲突。
- 临时服务器：仅在用户需要 Web 端交互纠正时启动，用完即关。

```python
from jinja2 import Environment, PackageLoader

class WebGenerator:
    def __init__(self):
        self.env = Environment(loader=PackageLoader("aw_coach", "web/templates"))

    def generate_dashboard(self, date: date, analysis: AnalysisResult) -> Path:
        template = self.env.get_template("dashboard.html")
        html = template.render(date=date, analysis=analysis)
        path = REPORTS_DIR / "web" / "index.html"
        path.write_text(html)
        return path

    def generate_report_page(self, date: date, analysis: AnalysisResult) -> Path:
        template = self.env.get_template("report.html")
        html = template.render(date=date, analysis=analysis)
        path = REPORTS_DIR / "web" / "reports" / f"{date}.html"
        path.write_text(html)
        return path
```

**HTML 功能**：
- 今日仪表盘（时间分布饼图用 Chart.js CDN）
- 精力曲线（折线图）
- 历史报告列表
- 分类纠正表单（提交到本地 SQLite，通过 `aw-coach` 命令处理）

**纠正表单的离线方案**：
- HTML 中嵌入 JavaScript，将纠正操作写入 `localStorage`。
- `aw-coach sync-corrections` 命令从浏览器 localStorage 导入到 SQLite。
- 或者：Web 页面中显示 CLI 命令供用户复制执行。

#### 可选增强：临时 Web 服务器

如果用户需要实时 Web 体验：

```bash
# 启动临时服务器，Ctrl+C 退出
aw-coach serve --port 5601
```

```python
@main.command()
@click.option("--port", default=5601)
def serve(port):
    """启动临时 Web 面板（Ctrl+C 退出）"""
    from flask import Flask, send_from_directory
    app = Flask(__name__)
    # 提供静态 HTML + API endpoints for correction
    ...
    click.echo(f"AI Coach Web: http://localhost:{port}")
    click.echo("Press Ctrl+C to stop")
    app.run(port=port)
```

---

### Week 11：Ollama 本地后端 + 社区规则库 + 自动规则生成

#### 规则库同步机制

```python
class RuleSyncManager:
    UPSTREAM_URL = "https://raw.githubusercontent.com/{repo}/main/rules/"

    def sync(self):
        """从上游下载最新规则库"""
        # 1. 下载 manifest.yml（规则文件列表 + hash）
        # 2. 对比本地 hash，增量下载变更文件
        # 3. 仅下载 YAML 文件，不执行任何代码
        ...

    def validate_rule_file(self, content: str) -> bool:
        """安全校验：仅允许声明式 YAML"""
        data = yaml.safe_load(content)
        # 检查所有字段都是预定义的字符串/列表类型
        # 不允许自定义函数、正则表达式等
        ...
```

**安全约束**：
- 规则文件仅支持 YAML 声明式格式。
- 匹配仅支持：精确字符串、contains（大小写不敏感）、域名前缀。
- **不支持正则表达式**（防止 ReDoS 和信息泄露）。
- 上游更新需要 signed tag 或固定 commit hash。

#### 自动规则建议

```python
def auto_suggest_rules(storage: Storage, rule_engine: RuleEngine) -> list[RuleSuggestion]:
    """基于纠正记录自动生成规则建议"""
    corrections = storage.get_corrections_last_30_days()

    # 聚合：(app, corrected_type) → count
    counter = Counter((c.app.lower(), c.corrected_type) for c in corrections)

    suggestions = []
    for (app, ctype), count in counter.items():
        if count >= 3 and not rule_engine.has_confident_rule(app):
            suggestions.append(RuleSuggestion(
                app=app,
                suggested_type=ctype,
                correction_count=count,
                confidence=min(0.70 + count * 0.05, 0.95),
            ))

    return suggestions
```

**用户确认流程**：

```bash
$ aw-coach rule suggest

基于你的纠正记录，建议添加以下规则:

[1] app="cursor" → programming (纠正 5 次, 建议置信度 0.95)
[2] app="obsidian" → writing (纠正 3 次, 建议置信度 0.85)

接受? [a]ll / [1,2] 选择 / [n]one: a

已添加 2 条规则到 ~/.local/share/activitywatch/aw-watcher-ai-coach/rules/user.yml
```

---

### Week 12：目标追踪 + Ollama 后端 + 最终打磨

#### 目标系统

```python
@dataclass
class Goal:
    name: str
    metric: str  # "daily_programming_hours" / "weekly_deep_work" / ...
    target: float
    current: float
    period: str  # "daily" / "weekly"

class GoalTracker:
    def __init__(self, config: Config, storage: Storage):
        self.goals = self._load_goals(config)

    def check_progress(self, analysis: AnalysisResult) -> list[GoalStatus]:
        statuses = []
        for goal in self.goals:
            current = self._get_metric(analysis, goal.metric)
            statuses.append(GoalStatus(
                goal=goal,
                current=current,
                progress=current / goal.target,
                on_track=current >= goal.target * self._expected_progress(),
            ))
        return statuses
```

#### 最终打磨

1. 降级通知完善：成本超限时推送系统通知 + `aw-coach status` 显示降级状态。
2. 首次引导优化：`aw-coach` 无参数运行时显示简明帮助 + 当前状态摘要。
3. 关机前 flush：监听 SIGTERM/SIGINT，触发最后一次数据处理。
4. 性能优化：大量历史数据时的查询性能（aw-server 端 AQL 查询 vs 客户端过滤）。

---

## 4. 关键技术决策汇总

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 本地状态存储 | SQLite | 并发安全、查询高效、单文件，与 AW 一致 |
| Web UI | 混合：静态 HTML 为主 + 临时 Flask 为辅 | 零资源占用日常查看；临时服务器支持交互纠正 |
| CLI 框架 | click | 轻量、成熟、AW 生态已使用 |
| 截图授权 | 配置级，默认关闭 | 跨平台通知回调不可靠；默认关闭确保隐私 |
| 批量大小 | 最多 8 切片/次（可配置） | LLM JSON 稳定性；网络好可调 12 |
| 解析失败策略 | 标记 unknown，不重试 | 避免双倍成本 |
| 规则匹配 | 字符串 exact / contains / starts_with / ends_with（无正则） | 安全 + 可读 + 性能 |
| LLM 默认模型 | gpt-4o-mini | 成本比 gpt-4o 低 10x |
| Web 服务器 | 临时启动（非常驻） | 节省内存，按需使用 |
| 规则库同步 | 固定 commit hash | 防止供应链攻击 |
| CLI/后台数据流 | CLI 读取 bucket + SQLite；后台定时分析写入 bucket | 避免重复计算，CLI 不依赖后台存活 |
| 定价表 | 配置文件可覆盖 + 硬编码 fallback | OpenAI 价格可能变动 |

---

## 5. 风险与缓解

| 风险 | 缓解策略 |
|------|----------|
| 规则覆盖率 < 80% | MVP 内置 Top 100 规则 + 纠正闭环自动生成规则 + 社区规则库同步 |
| aw-client API 变更 | 锁定 aw-client 版本，CI 中加 upstream 兼容性测试 |
| 批量 LLM 输出不稳定 | 8 切片上限 + schema 校验 + 缺失填充 + 优雅 fallback |
| 用户不看通知 | CLI 为主，通知为辅；`status` 命令聚合所有信息；日报末尾附纠正引导 |
| Wayland 截图失败 | 启动检测 + 明确提示 + 规则模式不受影响 + doctor 诊断 |
| Ollama 内存不足 | `doctor` 检测可用内存，不足时建议 rule_only |
| CLI/后台结果不一致 | CLI 优先读取 bucket 已有结果；首次体验/诊断时才直接拉原始数据 |
| 截图隐私争议 | 默认关闭 + blocklist + retention_hours=0 + doctor 明确提示 |
| 首次运行空白体验 | 自动拉取过去 7 天历史数据并立即分析生成报告 |

---

## 6. 验收里程碑

| 里程碑 | 时间 | 验收标准 |
|--------|------|----------|
| M1: 骨架 | Week 1 结束 | `aw-coach --version` 可执行 |
| M2: MVP | Week 4 结束 | `aw-coach status/report/doctor` 完整可用 |
| M3: AI 增强 | Week 8 结束 | hybrid 模式月成本 < $5，纠正系统可用 |
| M4: Web + 生态 | Week 12 结束 | 静态 HTML 报告、社区规则同步、目标追踪 |

---

## 8. 修订记录

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v1.0 | 2026-05-30 | 初始实现计划 |
| v1.1 | 2026-05-30 | **综合 review 修订**：修正规则引擎匹配逻辑、DataCollector 时间轴对齐、CLI 命令扩展（rule test / --dry-run / -v/-q / config）、纠正系统关联分析结果、截图配置级授权、静态 HTML + 临时服务器混合方案、Ollama 移至阶段三、测试策略补充、定价表配置化、日报末尾纠正引导 |

## 7. 立即可执行的第一步

```bash
mkdir -p aw-watcher-ai-coach/src/aw_coach
cd aw-watcher-ai-coach
git init

# 创建 pyproject.toml（见 Day 1-2）
# 创建 src/aw_coach/__init__.py
# 创建 src/aw_coach/cli.py（空壳 click group）

pip install -e ".[dev]"
aw-coach --version  # 验证安装成功
```
