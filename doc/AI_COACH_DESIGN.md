# ActivityWatch AI 教练功能设计文档

> 版本：v2.0（修订版）  
> 日期：2026-05-30  
> 状态：设计阶段

---

## 1. 项目背景与目标

### 1.1 背景
ActivityWatch 是一款优秀的开源时间追踪工具，能够自动记录用户在各个应用、窗口、浏览器标签页的停留时间。然而，原始数据仅提供了"我在哪里花了时间"，缺乏对"这些时间花得是否有价值"的深度洞察。

### 1.2 目标
设计并实现一个 **AI 教练（AI Coach）** 功能模块，在 ActivityWatch 现有数据基础上：

1. **智能识别工作内容**：结合窗口标题、应用名称、浏览器 URL，通过规则引擎 + 可选 AI 识别用户当前从事的具体工作类型。
2. **工作模式分析**：分析用户一天中的工作节奏，包括专注时段、中断频率、任务切换模式等。
3. **即时反馈与定时复盘**：提供过去 2 小时快速回顾 + 每天定时生成建议和复盘报告。
4. **智能建议**：基于历史数据和当日表现，给出个性化的 productivity 改进建议。
5. **零配置开箱即用**：不依赖 LLM 也能提供有价值的基础体验。

### 1.3 核心设计原则

| 原则 | 说明 |
|------|------|
| **零配置优先** | 默认 `rule_only` 模式，安装后立即可用，无需 API Key、无需本地 LLM |
| **成本可控** | 用户明确知道每月开销上限，超预算自动降级 |
| **隐私默认本地** | 敏感数据不出本机，截图默认关闭、按需触发 |
| **渐进增强** | 规则引擎覆盖 80% 场景，LLM 只处理边缘 case |
| **CLI 优先** | 先让功能可用（命令行），再追求 UI 美观 |
| **批量处理降成本** | 非实时逐条调 LLM，而是积累后批量分析 |

---

## 2. 整体架构设计

### 2.1 模块组成

```
┌─────────────────────────────────────────────────────────────────┐
│                        aw-server (localhost:5600)               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ aw-watcher-  │  │ aw-watcher-  │  │  aw-watcher-ai-coach │  │
│  │    window    │  │     afk      │  │   (本设计的新模块)    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                     │              │
│         ▼                 ▼                     ▼              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ bucket:      │  │ bucket:      │  │ bucket:              │  │
│  │ currentwindow│  │ afkstatus    │  │ ai-coach-insights    │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ REST API (read/write)
┌─────────────────────────────┴───────────────────────────────────┐
│                     aw-watcher-ai-coach                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Data        │  │ Screenshot  │  │     AI Analysis Engine  │  │
│  │ Collector   │  │ Capture     │  │  ┌───────────────────┐  │  │
│  │             │  │  (按需触发)  │  │  │ Rule Engine       │  │  │
│  │ - 拉取window│  │             │  │  │ (覆盖 80% 场景)   │  │  │
│  │ - 拉取afk   │  │ - 规则命中   │  │  └───────────────────┘  │  │
│  │ - 聚合事件  │  │   时请求授权 │  │  ┌───────────────────┐  │  │
│  └─────────────┘  │ - 批量OCR   │  │  │ Batch LLM Classify│  │  │
│                   │   分析      │  │  │ (1小时批量)        │  │  │
│  ┌─────────────┐  └─────────────┘  │  └───────────────────┘  │  │
│  │ Report      │  ┌─────────────┐  │  ┌───────────────────┐  │  │
│  │ Generator   │  │ Scheduler   │  │  │ Suggestion Engine │  │  │
│  │             │  │             │  │  │ - 个性化建议      │  │  │
│  │ - CLI查看   │  │ - 批量分析  │  │  │ - 目标追踪        │  │  │
│  │ - 实时摘要  │  │ - 定时报告  │  │  └───────────────────┘  │  │
│  │ - 通知直达  │  │ - 提醒通知  │  │  ┌───────────────────┐  │  │
│  └─────────────┘  └─────────────┘  │  │ Cost Controller   │  │  │
│                                     │  │ (预算+降级)       │  │  │
│  ┌─────────────┐  ┌─────────────┐  │  └───────────────────┘  │  │
│  │ CLI         │  │ Web UI      │  └─────────────────────────┘  │  │
│  │ (aw-coach)  │  │ (独立页面)  │                                │  │
│  │             │  │             │                                │  │
│  │ report      │  │ 仪表盘      │                                │  │
│  │ status      │  │ 历史报告    │                                │  │
│  │ correct     │  │ 实时状态    │                                │  │
│  └─────────────┘  └─────────────┘                                │  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 与 ActivityWatch 生态的集成

| 集成点 | 方式 | 说明 |
|--------|------|------|
| 进程生命周期 | `aw-watcher-ai-coach` 可执行文件 | aw-qt/aw-tauri 自动发现启动 |
| 数据读取 | `aw-client` REST API | 读取 window/afk/web bucket |
| 结果写入 | `aw-client` insert_event | 写入 `ai-coach_*` bucket |
| 配置管理 | TOML 配置文件 | 极简默认，高级用户可选扩展 |
| 日志记录 | `aw_core.log` | 统一日志格式与轮转策略 |
| CLI 工具 | `aw-coach` 命令 | 查看报告、状态、纠正分类 |

---

## 3. 核心功能模块设计

### 3.1 数据收集模块（Data Collector）

#### 3.1.1 职责
- 定期从 aw-server 拉取各 watcher 的原始事件数据。
- 对多源数据进行时间对齐、合并、清洗。
- 生成按时间段聚合的活动切片（Activity Slice）。

#### 3.1.2 批量拉取策略

不再高频逐条拉取，而是按小时批量聚合：

```python
class DataCollector:
    def fetch_hourly_slices(self, hour_start: datetime) -> List[ActivitySlice]:
        """拉取某个小时的所有相关 bucket 数据，并聚合为切片"""
        hour_end = hour_start + timedelta(hours=1)
        
        windows = self.client.get_events(
            bucket_id=f"aw-watcher-window_{self.hostname}",
            start=hour_start, end=hour_end
        )
        afk = self.client.get_events(
            bucket_id=f"aw-watcher-afk_{self.hostname}",
            start=hour_start, end=hour_end
        )
        
        return ActivitySlice.merge_hourly(windows, afk)
```

#### 3.1.3 活动切片（Activity Slice）

```json
{
  "start": "2026-05-30T09:00:00Z",
  "end": "2026-05-30T10:00:00Z",
  "duration": 3600,
  "is_afk": false,
  "primary_app": "vscode",
  "primary_window_title": "AI_COACH_DESIGN.md - activitywatch",
  "web_url": null,
  "screenshot_id": null,
  "rule_result": {
    "activity_type": "programming",
    "confidence": 0.88,
    "method": "rule"
  },
  "llm_result": null,
  "focus_score": null
}
```

### 3.2 截图采集模块（Screenshot Capture）——按需触发

#### 3.2.1 核心变更：从"定时全量"改为"按需授权"

**默认状态：关闭自动截图。**

只有当规则引擎无法确定活动类型时，才**请求用户一次性授权截图**，而非后台静默截取。

```python
class ScreenshotCapture:
    def maybe_capture(self, slice: ActivitySlice) -> Optional[Screenshot]:
        """
        仅在以下情况触发截图：
        1. 规则引擎 confidence < threshold（如 0.6）
        2. 用户未配置 disable_screenshot_fallback
        3. 当前应用不在 blocklist 中
        4. 距离上次截图 fallback 已超过冷却时间
        """
        if not self.should_capture(slice):
            return None
        
        # 弹出一次性授权通知
        if not self.request_user_consent(slice):
            return None
        
        return self.capture()
    
    def should_capture(self, slice: ActivitySlice) -> bool:
        if not self.config.screenshot_enabled:
            return False
        if slice.rule_result and slice.rule_result.confidence > 0.6:
            return False
        if slice.primary_app.lower() in self.config.blocklist_apps:
            return False
        return True
```

#### 3.2.2 配置项（极简版）

```toml
[screenshot]
# 默认关闭。仅在规则引擎无法判断时，请求用户授权后截图
enabled = false
# 截图后是否立即删除（0=立即删除，只保留分析结果）
retention_hours = 0
# 永不截图的应用（大小写不敏感）
blocklist_apps = ["1password", "keepass", "bank", "password"]
```

#### 3.2.3 平台兼容性与权限检测

**启动时检测，给出明确提示：**

```python
class PlatformChecker:
    def check_screenshot_capability(self) -> ScreenshotCapability:
        system = platform.system()
        
        if system == "Linux":
            if os.environ.get("WAYLAND_DISPLAY"):
                # Wayland 需要 xdg-desktop-portal
                if not self.has_portal_support():
                    return ScreenshotCapability(
                        available=False,
                        reason="Wayland detected. Install xdg-desktop-portal and grant Screen Capture permission.",
                        fallback="规则引擎纯文本分析仍可正常工作"
                    )
                return ScreenshotCapability(available=True, method="xdg-desktop-portal")
            else:
                return ScreenshotCapability(available=True, method="X11")
        
        elif system == "Darwin":
            if not self.has_screen_recording_permission():
                return ScreenshotCapability(
                    available=False,
                    reason="macOS Screen Recording permission required. Open System Settings > Privacy & Security > Screen Recording.",
                    fallback="规则引擎纯文本分析仍可正常工作"
                )
            return ScreenshotCapability(available=True, method="CGDisplay")
        
        elif system == "Windows":
            return ScreenshotCapability(available=True, method="Win32 GDI")
```

启动日志示例：

```
[WARNING] Wayland detected. Screenshot fallback requires xdg-desktop-portal.
[INFO]  Rule-only mode is fully functional. Run `aw-coach doctor` for details.
```

#### 3.2.4 隐私保护——事前拦截

放弃"先截图再模糊"的事后方案，改为**事前拦截**：

| 拦截层级 | 机制 | 说明 |
|----------|------|------|
| **应用级** | blocklist_apps | 配置中指定永不截图的应用 |
| **规则级** | 规则引擎预分类 | 已知敏感应用（如 KeePass）在规则中标记为 `sensitive`，直接跳过截图 |
| **用户级** | 一次性授权弹窗 | 每次截图前通过系统通知请求用户确认（可设置"本次允许 / 总是允许 / 拒绝"） |
| **时间级** | 非工作时段可选禁用 | 可配置仅在 work_hours 内启用截图 fallback |

### 3.3 AI 分析引擎（AI Analysis Engine）

#### 3.3.1 核心变更：规则引擎为主，LLM 为辅

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Analysis Engine                        │
│                                                             │
│  ┌─────────────────┐                                        │
│  │  Rule Engine    │ ◀── 80%+ 场景直接命中，零成本零延迟     │
│  │  (本地规则库)    │                                        │
│  └────────┬────────┘                                        │
│           │ confidence > 0.85                               │
│           ▼                                                 │
│      ┌─────────┐                                            │
│      │  直接输出 │                                            │
│      └─────────┘                                            │
│           │ confidence < 0.85                               │
│           ▼                                                 │
│  ┌─────────────────┐     ┌─────────────────────────────┐   │
│  │  Batch Queue    │────▶│  Hourly Batch LLM Classify  │   │
│  │  (积累1小时)     │     │  (每天最多 8-12 次调用)      │   │
│  └─────────────────┘     └─────────────────────────────┘   │
│                                        │                    │
│                                        ▼                    │
│                              ┌─────────────────┐           │
│                              │  Hybrid Decision │           │
│                              │  (规则 + LLM融合)│           │
│                              └─────────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

#### 3.3.2 规则引擎设计（Rule Engine）

**规则库结构（社区可贡献）：**

```yaml
# rules/apps.yml —— 应用级规则
rules:
  - name: vscode
    match_apps: ["code", "code - insiders", "vscodium", "cursor"]
    default_type: programming
    confidence: 0.90
    
  - name: browser
    match_apps: ["chrome", "chromium", "firefox", "safari", "edge"]
    default_type: research  # 浏览器默认研究，由 URL/标题细化
    confidence: 0.60  # 需要进一步判断
    sub_rules:
      - match_titles: ["youtube", "bilibili", "netflix"]
        type: entertainment
        confidence: 0.95
      - match_titles: ["jira", "confluence", "trello", "notion"]
        type: admin
        confidence: 0.85
      - match_urls: ["github.com", "gitlab.com", "stackoverflow.com"]
        type: programming
        confidence: 0.80
        
  - name: communication
    match_apps: ["zoom", "teams", "slack", "wechat", "qq", "telegram", "discord"]
    default_type: meeting
    confidence: 0.75
    sub_rules:
      - match_window_titles: ["zoom meeting", "teams meeting"]
        type: meeting
        confidence: 0.95
      - match_window_titles: ["slack -", "telegram -"]
        type: social
        confidence: 0.70
        
  - name: sensitive_apps
    match_apps: ["1password", "keepassxc", "lastpass"]
    type: sensitive
    confidence: 1.0
    skip_screenshot: true
    skip_analysis: true
```

**规则匹配流程：**

```python
class RuleEngine:
    def classify(self, app: str, title: str, url: Optional[str]) -> RuleResult:
        # 1. 应用名精确匹配
        rule = self.find_app_rule(app)
        if rule and rule.confidence >= 0.90:
            return RuleResult(
                activity_type=rule.default_type,
                confidence=rule.confidence,
                method="rule_app_exact"
            )
        
        # 2. 子规则匹配（URL/标题关键词）
        if rule and rule.sub_rules:
            for sub in rule.sub_rules:
                if sub.matches(title, url):
                    return RuleResult(
                        activity_type=sub.type,
                        confidence=sub.confidence,
                        method="rule_sub"
                    )
        
        # 3. 标题关键词模糊匹配
        keyword_result = self.match_keywords(title)
        if keyword_result.confidence >= 0.70:
            return keyword_result
        
        # 4. 无法确定，进入 batch queue
        return RuleResult(
            activity_type="unknown",
            confidence=0.0,
            method="rule_uncertain"
        )
```

**规则库维护策略：**

- 内置覆盖 Top 100 常见应用的中文 + 英文规则。
- 规则库以 YAML 文件形式存放在 `~/.local/share/activitywatch/aw-watcher-ai-coach/rules/`。
- 支持热重载（修改后无需重启）。
- 用户可通过 CLI 贡献规则：`aw-coach rule suggest --app "Cursor" --type programming`
- 定期从 GitHub 上游同步社区规则库。

#### 3.3.3 批量 LLM 分类（Hourly Batch Classify）

**核心思路**：积累 1 小时的未确定切片，一次性批量分类，大幅降低调用次数。

**成本对比**：

| 策略 | 每日调用次数 | 日成本 (GPT-4o) |
|------|-------------|----------------|
| 逐条实时分析 | 96 次 | ~$1.5-3 |
| **批量分析（本方案）** | **8-12 次** | **~$0.15-0.30** |

**批量 Prompt 设计：**

```
你是一个工作效率助手。以下是用户过去 1 小时内的活动切片，部分规则引擎无法确定类型。
请批量分析每个切片的活动类型。

可选类型：programming, writing, meeting, research, design, entertainment, admin, social, unknown

切片列表：
1. [09:00-09:15] app="chrome", title="Hacker News - chrome", url="news.ycombinator.com"
2. [09:15-09:30] app="unknown-binary", title="MainWindow", url=null
3. [09:30-09:45] app="cursor", title="main.rs - myproject", url=null
...

请返回 JSON 数组：
[
  {"slice_id": 1, "activity_type": "research", "confidence": 0.80, "reasoning": "Hacker News 属于技术资讯浏览"},
  {"slice_id": 2, "activity_type": "unknown", "confidence": 0.30, "reasoning": "无法从信息判断"},
  ...
]
```

#### 3.3.4 成本控制器（Cost Controller）

**新增模块，核心设计：**

```python
class CostController:
    """跟踪 AI API 调用成本，超预算自动降级"""
    
    def __init__(self, monthly_budget_usd: float, backend: AIBackend):
        self.monthly_budget = monthly_budget_usd
        self.backend = backend
        self.usage_log = UsageLog.load()
    
    def can_use_llm(self, estimated_cost: float = 0.02) -> bool:
        current_month_cost = self.usage_log.this_month_total()
        if current_month_cost + estimated_cost > self.monthly_budget:
            logger.warning(
                f"Monthly budget ${self.monthly_budget} almost reached "
                f"(current: ${current_month_cost:.2f}). Falling back to rule-only."
            )
            return False
        return True
    
    def track_call(self, model: str, input_tokens: int, output_tokens: int):
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        self.usage_log.record(datetime.now(), cost)
        logger.info(f"LLM call: ${cost:.4f} | Monthly: ${self.usage_log.this_month_total():.2f}/{self.monthly_budget}")
```

**配置项：**

```toml
[cost]
# 每月 AI API 开销上限（美元）。达到后自动降级到 rule_only
monthly_budget_usd = 5.0
# 是否允许临时超预算 10%（用于完成已开始的批量分析）
allow_overrun = false
# 成本告警阈值（达到预算的 50%/80%/100% 时通知）
alert_thresholds = [0.5, 0.8, 1.0]
```

#### 3.3.5 内容分类器输出

```json
{
  "activity_type": "programming",
  "activity_type_confidence": 0.92,
  "classification_method": "rule", // 或 "llm_batch", "hybrid"
  "project": "activitywatch-ai-coach",
  "task": "design-document",
  "is_work_related": true,
  "is_deep_work": true
}
```

### 3.4 模式分析器（Pattern Analyzer）

基于聚合后的切片数据，分析以下维度：

| 维度 | 指标 | 计算方法 |
|------|------|----------|
| **专注度** | 深度工作时长占比 | 连续 ≥25min 的同类型工作切片累计 |
| **中断频率** | 每小时任务切换次数 | 统计 activity_type 变化次数 |
| **有效工作时长** | 非 AFK 且非娱乐时长 | 排除 afk/entertainment/social |
| **时间分布** | 各类型工作的时间占比 | 按 activity_type 聚合 duration |
| **精力曲线** | 每小时工作效率得分 | 结合专注度、切换频率、应用类型 |

**专注度得分算法：**

```python
def calculate_focus_score(slices: List[ActivitySlice]) -> float:
    score = 100.0
    switches = count_activity_switches(slices)
    score -= min(switches * 3, 30)  # 切换惩罚（上限 30 分）
    
    deep_work_minutes = calculate_deep_work(slices, threshold_min=25)
    score += min(deep_work_minutes * 0.3, 20)  # 深度工作奖励（上限 20 分）
    
    distraction_ratio = calculate_distraction_ratio(slices)
    score -= distraction_ratio * 40
    
    return clamp(score, 0, 100)
```

### 3.5 建议引擎（Suggestion Engine）

**建议类型：**

1. **时间管理建议**
   - "你上午 10-11 点的专注度最高，建议将重要编程任务安排在此时间段。"
   - "你今天下午有 12 次任务切换，尝试使用番茄工作法减少中断。"

2. **工作模式建议**
   - "检测到你在 VS Code 和浏览器间频繁切换，可能是查文档，考虑使用离线文档减少上下文切换。"

3. **健康提醒**
   - "你已持续工作 90 分钟未休息，建议起身活动 5 分钟。"

4. **目标追踪**
   - "本周编程时间目标 20 小时，目前已完成 14 小时（70%）。"

### 3.6 报告生成模块（Report Generator）——多通道消费

#### 3.6.1 CLI 优先（aw-coach 命令）

**立即可用的命令行工具：**

```bash
# 查看今日报告（默认行为）
aw-coach report

# 查看今日报告（完整版）
aw-coach report --full

# 查看昨日报告
aw-coach report yesterday

# 查看本周概况
aw-coach report --week

# 实时状态（当前在做什么、今日累计）
aw-coach status

# 纠正最近的分类（低成本的纠错入口）
aw-coach correct --last programming
aw-coach correct --time "14:00-15:00" --type meeting

# 查看成本使用情况
aw-coach cost

# 诊断运行状态
aw-coach doctor

# 查看/贡献规则
aw-coach rule list
aw-coach rule suggest --app "Cursor" --type programming
```

**CLI 输出示例（aw-coach status）：**

```
┌────────────────────────────────────────┐
│  🧠 AI Coach - 实时状态                │
├────────────────────────────────────────┤
│  当前活动: programming (vscode)        │
│  专注得分: 82/100                      │
│                                        │
│  今日累计 (截至 15:30)                 │
│  ──────────────────────────────────    │
│  programming  ████████████  3h 20m     │
│  meeting      ██████░░░░░░  1h 30m     │
│  research     ████░░░░░░░░  1h 10m     │
│  admin        ██░░░░░░░░░░    32m     │
│                                        │
│  有效工作: 6h 32m  |  专注度: 72/100  │
│  任务切换: 23 次   |  深度工作: 2h 15m │
│                                        │
│  💡 提示: 下午 3 点后专注度下降        │
│     建议安排低脑力任务                 │
│                                        │
│  本月 AI 成本: $1.23 / $5.00           │
└────────────────────────────────────────┘
```

#### 3.6.2 即时摘要（Instant Summary）——解决反馈延迟

每隔 2 小时自动生成一段轻量摘要，通过系统通知推送：

```markdown
🧠 AI Coach 午间摘要 (09:00-12:00)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
有效工作: 3h 15m
深度工作: 1h 20m
专注得分: 78/100 ✅

时间分布:
  programming  60%  ████████████
  meeting      25%  ██████
  research     15%  ████

💡 下午建议: 你上午专注度很高，下午 2-4 点
   已预订会议，建议午休后先处理邮件/行政事务。
```

**配置项：**

```toml
[report]
# 日报生成时间
daily_report_time = "21:00"
# 即时摘要间隔（小时）
instant_summary_interval_hours = 2
# 通知方式: "notification" / "cli_only" / "both"
notification_method = "both"
```

#### 3.6.3 日报（Daily Report）

每天晚上 21:00（可配置）生成，同时通过以下渠道推送：

1. **系统通知**：摘要版（今日总工作时长、专注度、Top 建议）
2. **CLI 查看**：`aw-coach report`
3. **本地 Markdown**：`~/.local/share/activitywatch/reports/daily/2026-05-30.md`
4. **独立 Web 页面**（见 3.8 节）

#### 3.6.4 报告事件写入 bucket

```json
{
  "timestamp": "2026-05-30T21:00:00Z",
  "duration": 0,
  "data": {
    "report_type": "daily",
    "report_date": "2026-05-30",
    "report_path": "~/.local/share/activitywatch/reports/daily/2026-05-30.md",
    "notification_sent": true,
    "summary": {
      "total_work_hours": 6.53,
      "deep_work_hours": 2.25,
      "focus_score_avg": 72,
      "top_activity": "programming",
      "suggestion_count": 3
    }
  }
}
```

### 3.7 调度模块（Scheduler）

```python
class Scheduler:
    def __init__(self):
        self.jobs = [
            # 每小时批量数据采集与分析
            Job("hourly_batch", cron="0 * * * *", func=self.hourly_batch_analyze),
            
            # 即时摘要（每 2 小时）
            Job("instant_summary", cron="0 */2 * * *", func=self.send_instant_summary),
            
            # 实时提示检查（低频，避免打扰）
            Job("nudge_check", interval=300, func=self.check_nudges),
            
            # 日报生成
            Job("daily_report", cron=config.daily_report_time, func=self.generate_daily_report),
            
            # 周报生成
            Job("weekly_report", cron="0 9 * * 1", func=self.generate_weekly_report),
            
            # 数据清理
            Job("cleanup", cron="0 3 * * *", func=self.cleanup_old_data),
        ]
```

### 3.8 UI 集成方案——独立 Web 页面优先

#### 3.8.1 为什么先不做 aw-webui 集成

aw-webui 是 Vue 2 项目（正在迁移 Vue 3），贡献新面板需要跟随上游发布节奏。作为独立模块，更现实的方案是：

**做一个独立的轻量 Web 服务器**，通过 aw-server 反向代理或独立端口暴露。

#### 3.8.2 独立 Web 页面设计

```python
# 内置轻量 HTTP 服务器（Flask/FastAPI）
# 监听 localhost:5601（或 aw-server 反向代理）

class CoachWebServer:
    """提供 AI 教练的独立 Web 界面"""
    
    @route("/")
    def dashboard():
        """今日仪表盘"""
        return render_dashboard(get_today_data())
    
    @route("/report/<date>")
    def report(date):
        """查看指定日期报告"""
        return render_report(date)
    
    @route("/api/correct", methods=["POST"])
    def correct_classification():
        """用户纠正分类（低成本的 Web 纠错入口）"""
        data = request.json
        save_user_correction(data)
        return {"status": "ok"}
```

**访问方式：**
- `aw-coach open` 命令打开浏览器
- aw-webui 中通过 iframe 或链接嵌入（如果上游愿意）
- 系统托盘菜单「打开 AI 教练面板」

**Web 页面功能：**
- 今日仪表盘（精力曲线、时间分布饼图、实时状态）
- 历史报告列表（点击日期查看完整日报）
- 分类纠正（点击任意时间块，下拉选择正确类型）
- 目标设定与追踪
- 规则库浏览与贡献

#### 3.8.3 纠错交互设计（低成本）

| 入口 | 操作 | 成本 |
|------|------|------|
| CLI | `aw-coach correct --last programming` | 最低，无需离开终端 |
| 系统通知 | 日报通知中附带「纠正今日分类」按钮 | 低，一键直达 |
| Web 页面 | 点击时间块 → 下拉选择类型 → 保存 | 中等，但最直观 |
| 托盘菜单 | 右键「纠正最近活动」 | 低 |

纠正结果保存到本地 `corrections.jsonl`，定期用于：
- 增强规则库（高频纠正自动生成规则建议）
- few-shot 样本（LLM 批量分析时附带最近 10 条纠正记录）

---

## 4. 数据模型设计

### 4.1 Bucket 定义

```json
{
  "id": "ai-coach_{hostname}",
  "name": "AI Coach Analysis",
  "type": "ai.coach.activity",
  "client": "aw-watcher-ai-coach",
  "hostname": "{hostname}"
}
```

> **兼容性说明**：aw-server 对未知 bucket type 不会过滤或报错，type 字段仅用于 aw-webui 的可视化器识别。即使 aw-webui 无法渲染，数据仍可正常存储和查询。

### 4.2 Event 类型

#### 4.2.1 实时分析结果（`ai.coach.activity`）

```json
{
  "timestamp": "2026-05-30T09:00:00Z",
  "duration": 3600,
  "data": {
    "activity_type": "programming",
    "confidence": 0.92,
    "classification_method": "rule",
    "project": "activitywatch-ai-coach",
    "task": "design-document",
    "is_work_related": true,
    "is_deep_work": true,
    "focus_score": 78,
    "source_apps": ["vscode"],
    "source_window_titles": ["AI_COACH_DESIGN.md - activitywatch"]
  }
}
```

#### 4.2.2 建议事件（`ai.coach.suggestion`）

```json
{
  "timestamp": "2026-05-30T15:30:00Z",
  "duration": 0,
  "data": {
    "type": "focus_tip",
    "priority": 2,
    "message": "检测到频繁切换任务，试试先完成当前事项",
    "action_item": "使用番茄工作法，设置 25 分钟专注计时",
    "context": {
      "trigger": "rapid_context_switch",
      "switch_count_last_5min": 3
    }
  }
}
```

#### 4.2.3 报告事件（`ai.coach.report`）

```json
{
  "timestamp": "2026-05-30T21:00:00Z",
  "duration": 0,
  "data": {
    "report_type": "daily",
    "report_date": "2026-05-30",
    "report_path": "~/.local/share/activitywatch/reports/daily/2026-05-30.md",
    "notification_sent": true,
    "summary": {
      "total_work_hours": 6.53,
      "deep_work_hours": 2.25,
      "focus_score_avg": 72,
      "top_activity": "programming",
      "suggestion_count": 3
    }
  }
}
```

---

## 5. AI 后端适配层

### 5.1 统一接口

```python
from abc import ABC, abstractmethod

class AIBackend(ABC):
    @abstractmethod
    def batch_classify(self, slices: List[ActivitySlice]) -> List[ClassificationResult]:
        """批量分类（核心接口，替代单条 classify）"""
        pass
    
    @abstractmethod
    def generate_suggestions(self, summary: Dict) -> List[Dict]:
        pass
    
    @abstractmethod
    def generate_report(self, report_type: str, data: Dict) -> str:
        pass
    
    @abstractmethod
    def estimate_cost(self, operation: str, count: int) -> float:
        """估算调用成本，供 CostController 使用"""
        pass
```

### 5.2 后端实现

| 后端 | 适用场景 | 成本 | 隐私 |
|------|----------|------|------|
| `RuleBackend` | 默认模式，80%+ 场景 | $0 | 完全本地 |
| `LocalLLMBackend` | 需要 AI 分析但不愿付费 | $0（电费） | 完全本地 |
| `OpenAIBackend` | 追求最高准确率 | 按量计费 | 文本/截图上传 |
| `HybridBackend` | 推荐模式：规则为主，LLM 为辅 | 可控（批量） | 最小化上传 |

### 5.3 HybridBackend 详细逻辑

```python
class HybridBackend(AIBackend):
    def __init__(self, rule: RuleBackend, llm: AIBackend, 
                 cost_controller: CostController, threshold: float = 0.85):
        self.rule = rule
        self.llm = llm
        self.cost = cost_controller
        self.threshold = threshold
    
    def batch_classify(self, slices: List[ActivitySlice]) -> List[ClassificationResult]:
        results = []
        uncertain_slices = []
        
        # 1. 规则引擎先筛一遍
        for slice in slices:
            rule_result = self.rule.classify(slice.app, slice.title, slice.url)
            if rule_result.confidence >= self.threshold:
                results.append(rule_result)
            else:
                uncertain_slices.append(slice)
                results.append(None)  # 占位
        
        # 2. 未确定的切片批量调 LLM（如果预算允许）
        if uncertain_slices and self.cost.can_use_llm(
            estimated_cost=self.llm.estimate_cost("batch_classify", len(uncertain_slices))
        ):
            llm_results = self.llm.batch_classify(uncertain_slices)
            self.cost.track_call(...)  # 记录成本
            
            # 回填结果
            llm_idx = 0
            for i, r in enumerate(results):
                if r is None:
                    results[i] = llm_results[llm_idx]
                    llm_idx += 1
        else:
            # 预算不足，保持 unknown
            for i, r in enumerate(results):
                if r is None:
                    results[i] = ClassificationResult(
                        activity_type="unknown",
                        confidence=0.0,
                        method="rule_uncertain_budget_limited"
                    )
        
        return results
```

### 5.4 后端配置

```toml
[ai]
# 后端选择: "rule_only"（默认）, "local", "openai", "hybrid"
backend = "rule_only"

[ai.openai]
api_key = "${OPENAI_API_KEY}"
model = "gpt-4o-mini"  # 默认使用更便宜的模型
base_url = "https://api.openai.com/v1"

[ai.local]
provider = "ollama"
base_url = "http://localhost:11434"
model = "llama3"

[ai.hybrid]
# 规则引擎置信度阈值，超过则跳过 LLM
rule_confidence_threshold = 0.85
# 批量分析间隔（分钟）
batch_interval_minutes = 60
```

### 5.5 成本参考（修订后）

场景假设：每天活跃 8 小时，规则引擎覆盖率 85%。

| 模式 | 每日 API 调用 | 估算日成本 | 月成本 |
|------|-------------|-----------|--------|
| `rule_only` | 0 | $0 | $0 |
| `local` (Ollama) | ~2 次（仅报告生成） | ~$0 | ~$0 |
| `hybrid` (批量) | ~2-3 次批量分类 + 1 次日报 | ~$0.05-0.15 | ~$2-5 |
| `openai` 全量 | 8 次批量 + 1 次日报 | ~$0.5-1 | ~$15-30 |

> 通过批量处理和规则引擎优先，hybrid 模式月成本控制在 $5 以内，远低于原设计的 $50+。

---

## 6. 配置系统——极简默认 + 高级扩展

### 6.1 零配置默认

**安装后无需任何配置即可使用。** 默认启用 `rule_only` 模式，提供：

- 基于内置规则库的活动分类
- 专注度分析
- 每日报告（本地 Markdown + 系统通知）
- `aw-coach` CLI 工具

### 6.2 极简配置（90% 用户只需这些）

```toml
# ~/.config/activitywatch/aw-watcher-ai-coach.toml
# 以下为全部必要配置，未列出的均使用默认值

[ai]
# 默认 rule_only，零成本。改为 hybrid 启用 AI 增强
backend = "rule_only"

[ai.openai]
# 仅在 backend = "openai" 或 "hybrid" 时需要
api_key = "sk-..."

[cost]
# 月度预算（美元），达到后自动降级
monthly_budget_usd = 5.0

[report]
# 日报通知时间
daily_report_time = "21:00"
```

### 6.3 高级配置（完整版，按需启用）

```toml
[general]
enabled = true
retention_days = 90

[schedule]
daily_report_time = "21:00"
weekly_report_time = "0 9 * * 1"
report_timezone = "Asia/Shanghai"
instant_summary_interval_hours = 2

[screenshot]
enabled = false
retention_hours = 0
blocklist_apps = ["1password", "keepass", "bank", "password"]

[analysis]
deep_work_threshold_minutes = 25
distraction_apps = ["youtube", "bilibili", "twitter", "reddit", "tiktok"]
social_apps = ["wechat", "qq", "slack", "telegram", "discord"]
work_hours_start = "09:00"
work_hours_end = "18:00"
work_days = [1, 2, 3, 4, 5]

[nudge]
enabled = true
focus_reminder = true
break_reminder = true
distraction_warning = true
goal_tracking = true
cooldown_minutes = 30

[goals]
daily_programming_hours = 4
daily_deep_work_hours = 2
daily_entertainment_limit_hours = 2
weekly_programming_hours = 20

[ai]
backend = "rule_only"

[ai.openai]
api_key = "${OPENAI_API_KEY}"
model = "gpt-4o-mini"

[ai.local]
provider = "ollama"
base_url = "http://localhost:11434"
model = "llama3"

[ai.hybrid]
rule_confidence_threshold = 0.85
batch_interval_minutes = 60

[cost]
monthly_budget_usd = 5.0
allow_overrun = false
alert_thresholds = [0.5, 0.8, 1.0]
```

---

## 7. 隐私与安全设计

### 7.1 数据分级

| 级别 | 数据类型 | 处理方式 |
|------|----------|----------|
| **L1 公开** | 应用名称、时间分布统计 | 可本地存储，可上传 |
| **L2 内部** | 窗口标题、URL 域名 | 本地存储，上传前脱敏 |
| **L3 敏感** | 完整 URL、截图 | 仅本地存储，默认不上传 |
| **L4 机密** | 密码框、银行页面 | **事前拦截，绝不采集** |

### 7.2 隐私保护措施

| 措施 | 机制 |
|------|------|
| **默认 rule_only** | 不调用任何外部 API，数据完全本地 |
| **截图默认关闭** | 仅在规则无法判断时请求用户授权 |
| **事前拦截** | 敏感应用直接跳过截图，而非事后模糊 |
| **即时删除** | 截图 retention_hours 默认 0，分析后立即删除 |
| **一键清除** | `aw-coach purge` 清除所有本地截图和分析数据 |
| **预算控制** | 成本上限防止意外大量调用 API |

### 7.3 用户控制

- **完全本地模式**：默认启用，不调用任何外部 API。
- **云端增强模式**：用户明确授权后，将脱敏后的文本/截图上传至云端 AI 分析。
- **一键清除**：`aw-coach purge` 命令清除所有本地数据。

---

## 8. CLI 工具设计（aw-coach）

### 8.1 命令清单

```
aw-coach
  report [today|yesterday|YYYY-MM-DD] [--full]   查看日报
  status                                          实时状态
  correct --last <type>                           纠正最近分类
  correct --time <range> --type <type>            纠正指定时段
  cost                                            查看 AI 成本使用
  doctor                                          诊断运行状态
  rule list                                       列出规则库
  rule suggest --app <name> --type <type>         建议新规则
  open                                            打开 Web 面板
  purge                                           清除所有本地数据
  --version                                       版本信息
  --help                                          帮助
```

### 8.2 使用示例

```bash
# 安装后立即查看今日概况
aw-coach status

# 查看完整日报
aw-coach report --full

# 发现分类错误，立即纠正
aw-coach correct --last meeting

# 检查 AI 成本（hybrid 模式下）
aw-coach cost
# 输出: Monthly AI usage: $1.23 / $5.00 (24.6%)

# 诊断问题
aw-coach doctor
# 输出:
# ✅ aw-server reachable at localhost:5600
# ✅ rule engine loaded (142 rules)
# ⚠️  Wayland detected, screenshot requires xdg-desktop-portal
# ✅ cost controller active (budget: $5.00/month)
```

---

## 9. 实现路线图

### 阶段一：MVP（最小可行产品）

**目标**：零配置开箱即用，rule_only 模式提供有价值的基础体验。

**任务**：
1. [ ] 搭建 `aw-watcher-ai-coach` 项目骨架 + `aw-coach` CLI。
2. [ ] 实现 DataCollector（批量拉取 window/afk 数据）。
3. [ ] 实现 Rule Engine（内置 Top 100 应用规则库）。
4. [ ] 实现 Pattern Analyzer（专注度、时间分布统计）。
5. [ ] 实现 Report Generator（Markdown + 系统通知）。
6. [ ] 实现 `aw-coach report / status` CLI 命令。
7. [ ] 接入 aw-qt 自动启动。
8. [ ] 实现平台权限检测（Wayland / macOS Screen Recording）。

**交付物**：安装后 `rule_only` 模式立即可用，用户无需任何配置。
**预计工期**：3-4 周

### 阶段二：智能增强

**目标**：引入按需截图、批量 LLM、成本可控。

**任务**：
1. [ ] 实现按需 Screenshot Capture（规则未命中时请求授权）。
2. [ ] 实现 Batch Queue + Hourly Batch LLM Classify。
3. [ ] 集成 Ollama 本地 LLM。
4. [ ] 集成 OpenAI API（gpt-4o-mini 优先）。
5. [ ] 实现 CostController（预算 + 自动降级）。
6. [ ] 实现 HybridBackend。
7. [ ] 实现即时摘要（每 2 小时推送）。
8. [ ] 实现 `aw-coach correct / cost / doctor` 命令。

**预计工期**：3-4 周

### 阶段三：Web UI 与生态完善

**目标**：独立 Web 页面 + 社区规则库 + 目标追踪。

**任务**：
1. [ ] 开发独立轻量 Web 服务器（Flask/FastAPI）。
2. [ ] 开发 Web 仪表盘（今日概况、精力曲线、历史报告）。
3. [ ] Web 端分类纠正（点击时间块修改类型）。
4. [ ] 社区规则库自动同步机制。
5. [ ] 目标设定与追踪系统。
6. [ ] 撰写用户文档和部署指南。

**预计工期**：3-4 周

---

## 10. 技术栈建议

| 组件 | 推荐技术 | 说明 |
|------|----------|------|
| 语言 | Python 3.9+ | 与 ActivityWatch 生态一致 |
| 截图 | `mss` + `Pillow` | 跨平台，支持 X11/Wayland-portal |
| OCR | `easyocr`（按需安装）| 截图分析时可选依赖 |
| 调度 | `APScheduler` | 支持 cron 和 interval |
| HTTP 客户端 | `aw-client`（官方）| 复用生态基础设施 |
| HTTP 服务器 | `Flask` 或 `FastAPI` | 独立 Web 页面 |
| 配置解析 | `toml` + `pydantic` | 类型安全 + 默认值 |
| 本地 LLM | `ollama` HTTP API | 用户自行安装 Ollama |
| 通知 | `plyer` | 跨平台系统通知 |
| 数据验证 | `pydantic` | Event 模型校验 |
| CLI 框架 | `click` 或 `typer` | 现代化 CLI 开发 |

---

## 11. 风险评估与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| 规则引擎覆盖率不足 | 高 | 内置 Top 100 规则 + 社区贡献 + 高频纠正自动生成规则建议 |
| 截图隐私争议 | 高 | **默认关闭** + 按需授权 + 事前拦截 + 即时删除 |
| AI API 成本超预期 | 中 | 批量处理 + 成本控制器 + 月度预算 + 自动降级 |
| 本地 LLM 硬件门槛 | 中 | 默认 rule_only 无需 LLM；Ollama 为可选增强 |
| Wayland 截图兼容性 | 中 | 启动时检测并提示；规则引擎纯文本分析不受影响 |
| 分类准确率不足 | 中 | 允许用户低成本纠正；积累 few-shot；持续优化规则库 |
| 性能开销 | 低 | 批量拉取/分析；异步队列；合理调度间隔 |
| aw-webui 集成困难 | 低 | 优先独立 Web 页面，不依赖上游发布节奏 |

---

## 12. 附录

### 12.1 参考资源

- [ActivityWatch Documentation](https://docs.activitywatch.net/)
- [ActivityWatch REST API](https://docs.activitywatch.net/en/latest/api.html)
- [aw-client Python 库](https://github.com/ActivityWatch/aw-client)
- [aw-watcher-screenshot 参考实现](https://github.com/ActivityWatch/aw-watcher-screenshot)

### 12.2 术语表

| 术语 | 说明 |
|------|------|
| Bucket | ActivityWatch 中的数据存储单元 |
| Event | 时间线上的活动记录（timestamp + duration + data） |
| Heartbeat | Watcher 定期发送的心跳，server 自动合并 |
| Watcher | 数据采集端，如 aw-watcher-window |
| AQL | ActivityWatch Query Language |
| AFK | Away From Keyboard |
| Deep Work | 无中断的专注工作时段 |
| Nudge | 实时轻量提示 |
| Batch Classify | 积累多切片后一次性 LLM 分类 |

---

## 13. 修订记录

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v1.0 | 2026-05-30 | 初始版本 |
| v2.0 | 2026-05-30 | **大幅修订**：零配置默认、按需截图、批量 LLM、成本控制器、CLI 优先、独立 Web 页面、事前拦截隐私、社区规则库、即时摘要 |

---

> 本文档为 ActivityWatch AI 教练功能的 v2.0 设计，已针对易用性、可实现性和运行成本三个维度进行系统性优化。核心思路：**规则引擎覆盖 80% 场景，LLM 仅处理边缘 case，成本可控在 $5/月以内，零配置开箱即用**。
