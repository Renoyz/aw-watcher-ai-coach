# aw-coach 主动式 AI Agent — 当前系统诊断与优化路线图

> 基于 `doc/active-agent-roadmap.md` 的改进计划，对当前代码库进行深度诊断，明确已有能力、缺失模块和具体优化路径。

---

## 一、当前系统能力全景图

### 1.1 已完成的模块（绿色）

```
+----------------------------------+
| 1. Observer 观察层                | ✅ 完成
| DataCollector + heartbeat 合并   |
| AFK 过滤 + browser URL join      |
+----------------------------------+
+----------------------------------+
| 2. Normalizer 数据清洗层          | ✅ 完成
| fetch_range() 输出 ActivitySlice |
| 时区统一处理                      |
+----------------------------------+
+----------------------------------+
| 3. Rules Engine 规则分类          | ✅ 完成
| 42 条内置规则 + user.yml          |
| HybridBackend (rule + LLM)       |
+----------------------------------+
+----------------------------------+
| 4. Analyzer 离线分析              | 🟡 部分完成
| focus_score / productivity_score |
| death_loops 检测                  |
| 但：无实时状态，无滚动窗口        |
+----------------------------------+
+----------------------------------+
| 5. Reporter 报告生成              | ✅ 完成
| Markdown 日报 / 周报             |
| HTML 仪表盘 (Chart.js)           |
| AI 建议 (DeepSeek)               |
+----------------------------------+
+----------------------------------+
| 6. Scheduler 定时调度             | 🟡 部分完成
| 每小时分析 + 即时摘要 + 日报     |
| 但：仅定时任务，非事件驱动        |
+----------------------------------+
+----------------------------------+
| 7. Notify 通知系统                | ✅ 刚升级
| dbus action + 点击打开仪表盘     |
+----------------------------------+
+----------------------------------+
| 8. Storage 持久化                 | ✅ 完成
| cost_log / batch_queue           |
| corrections / scheduler_state    |
+----------------------------------+
+----------------------------------+
| 9. CLI 交互生态                   | ✅ 完成
| 11 个命令 + 交互式校准           |
+----------------------------------+
+----------------------------------+
| 10. Feedback 反馈闭环             | 🟡 部分完成
| correction 表 + rule-suggest     |
| 但：无 user_profile，无学习衰减  |
+----------------------------------+
```

### 1.2 核心缺失模块（红色）

```
+----------------------------------+
| State Model 实时状态层            | ❌ 缺失
| UserWorkState + 滚动窗口         |
+----------------------------------+
+----------------------------------+
| Detector 事件检测层               | 🟡 仅 death_loop
| unknown / ai_coding / focus_block|
+----------------------------------+
+----------------------------------+
| Policy Engine 决策层              | ❌ 缺失
| interrupt budget + cooldown      |
+----------------------------------+
+----------------------------------+
| Action Executor 主动行动层        | 🟡 仅 notify
| Agent Inbox + 半自治模式         |
+----------------------------------+
+----------------------------------+
| Feedback Memory 学习层            | ❌ 缺失
| user_profile + dismiss 记忆      |
+----------------------------------+
```

---

## 二、逐层诊断与优化建议

### 2.1 Observer + Normalizer：已稳固，需微调

**当前状态：良好**

`collector.py` 已经实现了：
- `aw-watcher-window` + `aw-watcher-afk` 双 bucket 查询
- heartbeat 合并（`merge_heartbeats`，5 分钟阈值）
- AFK 过滤（`drop_afk=True`）
- `aw-watcher-web` URL 合并到 `web_url` 字段
- 时区统一（查询前 UTC，返回前转本地）

**建议优化：**

| 优化点 | 当前问题 | 建议方案 | 工作量 |
|--------|---------|---------|--------|
| `aw-watcher-web` 集成深度 | 仅提取 URL，未提取 domain/path | 在 `ActivitySlice` 中增加 `domain` 字段 | 小 |
| 短切换去抖动 | 小于 30 秒的瞬间切换被计入 | `collector.py` 中增加 `min_duration_sec=30` 过滤 | 小 |
| 跨 bucket 时间对齐 | window 和 afk bucket 可能存在微小时间差 | 增加 `tolerance_sec=5` 对齐逻辑 | 中 |
| NormalizedEvent 统一结构 | 当前使用 `ActivitySlice`，字段不够丰富 | 新增 `NormalizedEvent` dataclass，包含 domain、is_afk、source_bucket | 小 |

**优先级：P2** — 当前数据质量已足够支撑下一阶段，这些属于体验优化。

---

### 2.2 State Model：最大短板，必须优先补齐

**当前状态：严重不足**

当前系统只有**离线分析**（`AnalysisResult`），没有**实时状态**。`scheduler.py` 的 `while` 循环每小时批量分析一次，相当于：

```python
# 当前模式：离线批量
每小时读取过去1小时数据 -> 分析 -> 写入 bucket

# 缺少：实时状态
每分钟更新当前在做什么
当前已连续工作多久
最近5分钟切换了几次
当前是否处于专注块
```

**缺失的核心状态：**

```python
# 当前代码中没有这些
switches_last_5min: int      # ❌
switches_last_30min: int     # ❌
focus_score_rolling: float   # ❌
current_activity: str        # ❌ (只有离线统计后的 activity_type)
risk_level: str              # ❌
likely_mode: str             # ❌
active_block_start: datetime # ❌
active_block_category: str   # ❌
last_agent_notification_at   # ❌
```

**建议实现方案：**

新增 `src/aw_coach/state.py`：

```python
@dataclass
class UserWorkState:
    updated_at: datetime

    current_app: str
    current_title: str
    current_url: Optional[str]
    current_domain: Optional[str]

    current_activity: str
    current_confidence: float

    active_block_start: datetime
    active_block_category: str
    active_block_duration_sec: int

    switches_last_5min: int
    switches_last_30min: int
    switches_last_hour: int

    focus_score_rolling: float   # 0-100
    productivity_score_rolling: float  # 0-100

    likely_mode: str   # coding / debugging / reading / meeting / chatting / browsing / idle
    risk_level: str    # normal / fragmented / stuck / distracted / unknown

    last_user_feedback_at: Optional[datetime]
    last_agent_notification_at: Optional[datetime]
    notifications_today: int

    def to_dict(self) -> dict: ...
    @classmethod
    def from_events(cls, events: List[NormalizedEvent], rules: RuleEngine) -> "UserWorkState": ...
```

**更新频率：** 每分钟（与 scheduler 主循环同步）

**持久化：** 写入 SQLite `scheduler_state` 表（已有 v2 迁移），服务重启后可恢复。

**优先级：P0** — 没有状态层，所有 Detector 和 Policy Engine 都无从谈起。

**工作量：中（2-3 天）**

---

### 2.3 Detector 层：已有基础，需扩展

**当前状态：仅 death_loop 一个 detector**

`analyzer.py` 中 `_detect_death_loops()` 实现了 A↔B 交替检测：

```python
def _detect_death_loops(self, slices) -> List[Dict]:
    # 当前实现：检测 A<->B 交替 >=3 次
    # 输出：{app_a, app_b, count, duration, start_time}
```

**建议扩展的 Detector：**

#### detector 1: `unknown_detector`

```python
class UnknownDetector:
    def detect(self, state: UserWorkState, history: List[NormalizedEvent]) -> Optional[AgentSignal]:
        # 触发条件1：今日 unknown 累计 > 30min
        # 触发条件2：同一 app 连续 3 次被标记为 unknown
        # 触发条件3：当前处于 unknown 状态且已持续 > 10min
```

**与现有代码的关系：** 可直接复用 `RuleEngine.classify()` 的 confidence 字段，confidence < 0.5 或 activity_type == "unknown" 即触发。

#### detector 2: `high_switch_detector`

```python
class HighSwitchDetector:
    def detect(self, state: UserWorkState) -> Optional[AgentSignal]:
        # 触发条件：switches_last_5min >= 5 或 switches_last_30min >= 15
```

#### detector 3: `ai_coding_loop_detector`

```python
class AICodingLoopDetector:
    """区分 AI 辅助开发正常闭环 vs 无效循环"""

    AI_APPS = {"chatgpt", "claude", "codex", "cursor", "github-copilot"}
    OUTPUT_APPS = {"code", "gnome-terminal", "vim", "emacs"}
    RESEARCH_APPS = {"firefox", "chrome", "safari"}

    def detect(self, history: List[NormalizedEvent]) -> Optional[AgentSignal]:
        # 正常模式：AI -> OUTPUT -> AI -> OUTPUT (有产出)
        # 异常模式：AI -> RESEARCH -> AI -> RESEARCH (无产出，纯查询)
        # 判断标准：30min 内是否回到 OUTPUT 应用
```

**与现有代码的关系：** 当前 `death_loop_detector` 会把 ChatGPT↔Chrome 标记为 death_loop，但需要区分这是"AI 辅助开发"还是"无效搜索循环"。新增此 detector 可避免误报。

#### detector 4: `focus_block_detector`

```python
class FocusBlockDetector:
    def detect(self, state: UserWorkState) -> Optional[AgentSignal]:
        # 高质量专注块特征：
        # - 同一 activity_type 持续 >= 25min
        # - switches_last_5min == 0
        # - confidence >= 0.85
        # - 非 AFK
        #
        # 动作：标记为保护状态，不打扰
```

**这是 "主动保持沉默" 的关键。**

#### detector 5: `browser_search_loop_detector`

```python
class BrowserSearchLoopDetector:
    def detect(self, history: List[NormalizedEvent]) -> Optional[AgentSignal]:
        # 特征：在搜索引擎/技术文档之间反复跳转，没有回到 IDE
        # 典型：百度 -> CSDN -> 知乎 -> StackOverflow -> 百度
        # 判断：domain 变化频繁，但无 output 应用介入
```

**统一 Signal 结构：**

```python
@dataclass
class AgentSignal:
    detector: str           # "unknown_detector", "death_loop", etc.
    type: str               # "unknown_spike", "death_loop", "ai_query_loop", "focus_block", "high_switch"
    severity: float         # 0.0 - 1.0
    confidence: float       # 0.0 - 1.0
    start_time: datetime
    end_time: datetime
    evidence: dict          # 原始证据，如 {"apps": ["ChatGPT", "Chrome"], "switch_count": 12}
    suggested_actions: List[str]  # ["notify", "inbox", "log_only"]
```

**优先级：P0** — detector 是 Agent "主动发现" 的眼睛。

**工作量：中（3-4 天，5 个 detector）**

---

### 2.4 Policy Engine：从"定时通知"到"智能决策"

**当前状态：完全缺失**

当前 `scheduler.py` 的通知逻辑是：

```python
# 当前：简单定时
if (now - last_summary).total_seconds() >= interval * 3600:
    self._send_instant_summary(now)   # 直接发通知！
    last_summary = now
```

**问题：**
- 用户正在深度工作时也会发通知
- 一天内可能发多次，没有上限
- 低价值通知和死亡循环通知同等对待
- 用户连续拒绝某类建议后，系统不会学会减少

**建议实现：**

新增 `src/aw_coach/policy.py`：

```python
@dataclass
class InterruptBudget:
    max_per_day: int = 4
    min_interval_minutes: int = 45
    quiet_hours: List[Tuple[str, str]] = field(default_factory=lambda: [("12:00", "13:30"), ("22:30", "09:00")])
    suppress_after_dismiss: int = 2

@dataclass
class AgentDecision:
    action: str           # "notify_now", "inbox", "delay", "log_only", "suppress"
    priority: int         # 1-5
    requires_confirmation: bool
    reason: str
    expires_at: Optional[datetime]

class PolicyEngine:
    def __init__(self, budget: InterruptBudget, storage: Storage):
        self.budget = budget
        self.storage = storage

    def decide(self, signal: AgentSignal, state: UserWorkState) -> AgentDecision:
        # 决策逻辑：
        # 1. 检查 quiet hours -> suppress
        # 2. 检查今日通知次数 -> 超过上限则 inbox
        # 3. 检查上次通知间隔 -> 不足45min则 delay
        # 4. 检查用户是否处于 focus_block -> log_only
        # 5. 检查用户历史 dismiss 记录 -> 超过阈值则 suppress
        # 6. 根据 signal.severity 决定 action
```

**关键决策规则：**

```python
RULES = [
    # 深度工作保护
    (state.risk_level == "focus" and state.active_block_duration_sec > 1200,
     Decision("log_only", "用户处于深度工作块，不打扰")),

    # 每日上限
    (state.notifications_today >= budget.max_per_day,
     Decision("inbox", "今日通知已达上限，建议入收件箱")),

    # 冷却期
    (state.last_agent_notification_at and
     (now - state.last_agent_notification_at).seconds < budget.min_interval_minutes * 60,
     Decision("delay", "距上次通知不足45分钟")),

    # 安静时段
    (is_quiet_hour(now, budget.quiet_hours),
     Decision("suppress", "当前为安静时段")),

    # 用户历史拒绝
    (self._get_dismiss_count(signal.detector) >= budget.suppress_after_dismiss,
     Decision("inbox", f"用户已拒绝 {signal.detector} 建议 {budget.suppress_after_dismiss} 次")),

    # 高严重度 -> 立即通知
    (signal.severity >= 0.8,
     Decision("notify_now", "严重度 >= 0.8，需要立即干预")),

    # 中等严重度 -> 入收件箱
    (signal.severity >= 0.5,
     Decision("inbox", "中等严重度，建议入收件箱待处理")),

    # 低严重度 -> 仅记录
    (True,
     Decision("log_only", "低严重度，仅记录")),
]
```

**与现有代码的关系：**
- 替换 `scheduler.py` 中简单的 `if (now - last_summary) >= interval` 逻辑
- `_send_instant_summary()` 不再直接 `send_notification()`，而是先调用 `PolicyEngine.decide()`

**优先级：P0** — 没有 Policy Engine，Agent 会变成一个"烦人的定时闹钟"。

**工作量：中（2-3 天）**

---

### 2.5 Action Executor + Agent Inbox：从"直接通知"到"半自治模式"

**当前状态：只有 notify**

当前 `scheduler.py` 中，一旦触发通知条件，直接调用 `send_notification()`。用户只有"看到"和"忽略"两个选择，没有"接受/拒绝/稍后处理"的交互。

**建议实现：**

#### Agent Inbox 文件系统

```
~/.local/share/activitywatch/aw-watcher-ai-coach/inbox/
  20260530_143022_death_loop.json
  20260530_151511_unknown_calibrate.json
  20260530_163045_rule_suggest.json
```

每个 inbox item：

```json
{
  "id": "20260530_143022_death_loop",
  "created_at": "2026-05-30T14:30:22",
  "detector": "death_loop",
  "signal_type": "chatgpt_chrome_loop",
  "severity": 0.75,
  "evidence": {
    "apps": ["ChatGPT", "Chrome"],
    "switch_count": 14,
    "duration_min": 28
  },
  "suggested_action": "建议回到 VSCode 继续编码，或记录当前查询要点",
  "status": "pending",
  "user_response": null
}
```

#### CLI 命令扩展

```bash
aw-coach inbox              # 列出所有待处理建议
aw-coach accept <id>        # 接受建议（执行对应动作）
aw-coach dismiss <id>       # 拒绝建议（记录到 user_profile.dismissed）
aw-coach edit <id>          # 编辑建议内容
```

**与现有代码的关系：**
- `aw-coach correct` 和 `aw-coach rule-suggest` 已经实现了类似的"交互式确认"模式
- 可以复用 `storage.py` 的 SQLite 存储，新增 `inbox` 表

**优先级：P1** — inbox 是安全半自治的基础，但当前直接通知的方式也能工作。

**工作量：中（2-3 天）**

---

### 2.6 Feedback Memory：从"记录纠正"到"学习用户偏好"

**当前状态：仅 corrections 表**

当前系统记录了用户的纠正（`corrections` 表），但没有：
- 用户拒绝过哪些建议
- 用户偏好的工作时段
- 用户对不同 detector 的接受度
- 用户是否偏好少通知

**建议实现：**

新增 `user_profile.yml`：

```yaml
# ~/.aw-coach/user_profile.yml
profile:
  created_at: "2026-05-30"
  updated_at: "2026-05-30"

  # 通知偏好
  prefers_notifications: true
  prefers_inbox_over_notify: false

  # 高效时段（从数据中自动学习）
  best_focus_hours:
    - start: "09:30"
      end: "11:30"
      confidence: 0.85
    - start: "20:30"
      end: "22:30"
      confidence: 0.72

  # 各 detector 的接受率
  detector_stats:
    death_loop:
      accepted: 5
      dismissed: 1
      last_dismissed_at: null
    unknown_calibrate:
      accepted: 12
      dismissed: 0
    focus_guard:
      accepted: 1
      dismissed: 6
      last_dismissed_at: "2026-05-29T15:22:00"

  # 敏感应用（从不截图、不上传 LLM）
  sensitive_apps:
    - WeChat
    - Feishu
    - 1Password

  # AI 编码应用（不扣 focus 分）
  ai_coding_apps:
    - ChatGPT
    - Claude
    - Cursor
```

**自动学习逻辑：**

```python
# 从 ActivityWatch 历史数据自动学习 best_focus_hours
focus_hours = defaultdict(list)
for event in historical_events:
    if event.focus_score > 70 and event.switch_count < 5:
        focus_hours[event.start.hour].append(event.focus_score)

# 找出连续的高分时段
best_blocks = find_continuous_high_score_blocks(focus_hours)
```

**影响 Policy Engine 决策：**

```python
# 如果用户经常拒绝 focus_guard，则 suppress 该 detector
if profile.detector_stats["focus_guard"].dismissed >= 3:
    return Decision("suppress", "用户已多次拒绝 focus_guard 建议")

# 如果在用户的高效时段，更不轻易打扰
if is_best_focus_hour(now, profile.best_focus_hours):
    return Decision("log_only", "用户当前处于历史高效时段")
```

**优先级：P1** — 反馈学习让 Agent 逐渐个性化，但初始阶段可以用默认值。

**工作量：中（2-3 天）**

---

### 2.7 LLM 使用策略：当前已接近理想状态

**当前状态：良好**

当前 `HybridBackend` 已经实现了：
- rule 置信度 >= 0.85 时直通，不调用 LLM
- 低置信度样本进入 `batch_queue`，批量处理
- `CostController` 月度预算控制
- 敏感应用（`entertainment`, `social`）默认 weight 为负，不触发 LLM

**建议微调：**

| 优化点 | 当前状态 | 建议 | 工作量 |
|--------|---------|------|--------|
| 敏感应用 LLM 黑名单 | 无显式黑名单 | `user_profile.sensitive_apps` 中的应用不上传 LLM | 小 |
| LLM 输入脱敏 | 无脱敏 | 上传前替换 title 中的路径、用户名、IP | 中 |
| 日报生成节流 | 每天调用一次 LLM | 如果今日数据与昨日相似，复用昨日建议模板 | 小 |

**优先级：P2** — 当前 LLM 策略已经比较合理。

---

### 2.8 截图识别：不建议当前优先做

**当前状态：仅配置项，无实现**

`config.py` 中有 `ScreenshotConfig`，但 `scheduler.py` 和 `analyzer.py` 中完全没有使用。

**建议：**
- 保留配置项，但明确标注为 `enabled: false`
- 在文档中说明这是 P3 阶段功能
- 当前先不实现

**原因：**
1. 截图涉及隐私风险，需要更完善的脱敏和权限控制
2. 当前 `app + title + url` 已经能覆盖 80% 的场景
3. OCR/VLM 引入后，代码复杂度和依赖会显著增加
4. 应该先验证 "State + Detector + Policy" 闭环的价值，再考虑增强感知

**优先级：P3**

---

### 2.9 Research Loop：从 L4 到 L5 的关键跃迁

**当前状态：完全缺失**

当前系统没有任何主动搜索/研究能力。`HybridBackend` 中的 LLM 只用于：
- 低置信度活动分类
- 日报/周报建议生成
- 规则草案生成

这些都是在**已有数据上做总结**，不是在**识别问题后主动查找解决方案**。

**Research Loop 的价值：**

| 场景 | 当前系统 | 加入 Research Loop 后 |
|------|---------|----------------------|
| 技术卡点 | "你在 ROS2 资料上停留很久" | "你可能卡在 executor 安装路径问题，建议检查 install(PROGRAMS) / console_scripts / source setup.bash" |
| AI 查询循环 | "你可能陷入了搜索循环" | "建议你执行'验证优先'原则：每 20 分钟必须回到代码或笔记" |
| unknown 应用 | "请运行 aw-coach calibrate 分类" | "这个应用是 XXX 工具，通常用于 YYY，建议标记为 programming" |
| 学习路径停滞 | "本周阅读时间长，代码时间少" | "本周建议实验：写一个 MultiThreadedExecutor demo 验证 callback group 行为" |

**建议实现：**

新增 `src/aw_coach/research/` 包：

```
src/aw_coach/research/
  __init__.py
  hypothesis.py     # ProblemHypothesis
  planner.py        # ResearchPlanner
  executor.py       # ResearchExecutor
  evaluator.py      # SourceEvaluator
  synthesizer.py    # SolutionSynthesizer
  memory.py         # ResearchMemory
```

**与现有代码的关系：**
- Detector 输出 `AgentSignal` → Research Loop 接收 → 生成 `ProblemHypothesis`
- Policy Engine 判断 `need_research=True` 的信号 → 触发 ResearchPlanner
- 搜索结果进入 `Agent Inbox` → 用户 `accept/dismiss` → 沉淀到 `ResearchMemory`

**Search Policy 边界（必须遵守）：**

```yaml
research_policy:
  enabled: true
  mode: triggered_only      # 绝不自动定时搜索
  max_searches_per_day: 5   # 每日上限
  min_interval_minutes: 60  # 两次搜索间隔

  # 敏感信息绝不上传
  never_send:
    - raw_screenshot
    - chat_content
    - api_key
    - full_source_code
    - internal_url
    - file_path

  # 必须脱敏
  sanitize: true

  # 可信源优先
  prefer_sources:
    - official_docs
    - github_issues
    - release_notes
    - standards_docs
```

**脱敏示例：**

```text
原始（本地敏感）：
  /pkg/app/ats_dispatch_server/lib/ats_dispatch_server 找不到 ats_recorder.py

改写（脱敏搜索 query）：
  ROS2 launch executable not found in libexec directory Python script installed to wrong location
```

**优先级：P2** — Research Loop 是 L4→L5 的跃迁，但必须排在 State + Detector + Policy 之后。只有先知道"用户遇到什么问题"，主动搜索才有意义。

**工作量：大（1-2 周）** — 涉及搜索执行、来源评估、结果合成、记忆沉淀，模块较多。

---

## 三、推荐实施路线图

### Phase 1: State Model + 核心 Detector（2 周）

**目标：让系统"知道用户现在在做什么"**

```
Week 1:
  [Day 1-2] 实现 UserWorkState + state.py
  [Day 3-4] 集成到 scheduler.py，每分钟更新状态
  [Day 5]   持久化到 SQLite，重启恢复

Week 2:
  [Day 1-2] 实现 unknown_detector + high_switch_detector
  [Day 3-4] 实现 ai_coding_loop_detector + focus_block_detector
  [Day 5]   统一 AgentSignal 结构，写测试
```

**验收标准：**
- `aw-coach state` 命令能实时打印当前 UserWorkState
- 系统能识别"当前处于 coding / reading / chatting / idle"
- 系统能识别"当前是否处于专注块"

---

### Phase 2: Policy Engine（1 周）

**目标：让系统"知道什么时候该说话，什么时候该闭嘴"**

```
[Day 1-2] 实现 PolicyEngine + InterruptBudget
[Day 3]   集成到 scheduler.py，替换直接通知逻辑
[Day 4]   实现 quiet hours + cooldown + focus protect
[Day 5]   测试不同场景下的决策正确性
```

**验收标准：**
- 用户处于 focus_block > 20min 时，不发任何通知
- 每日通知不超过 4 次
- 两次通知间隔 >= 45min
- 低严重度事件只记录，不强提醒

---

### Phase 3: Agent Inbox + Feedback Memory（1 周）

**目标：让用户能"接受/拒绝/学习"Agent 的建议**

```
[Day 1-2] 实现 inbox 表 + inbox CLI 命令
[Day 3]   实现 accept / dismiss / edit 命令
[Day 4]   实现 user_profile + 自动学习 best_focus_hours
[Day 5]   集成到 PolicyEngine（dismiss 记忆影响后续决策）
```

**验收标准：**
- `aw-coach inbox` 能列出待处理建议
- `aw-coach dismiss` 后，同类建议频率降低
- 系统能自动学习用户的高效时段

---

### Phase 4: 打磨体验（1 周）

```
[Day 1-2] 优化通知内容（上下文更丰富）
[Day 3]   实现"每日 One Change"（日报只给一条建议）
[Day 4]   增加 aw-watcher-web domain 提取
[Day 5]   完善测试 + 文档
```

---

### Phase 5: Research Loop（1-2 周）

**目标：让系统不仅能发现问题，还能主动研究解决方案**

```
[Day 1-2] 实现 ProblemHypothesis + ResearchPlanner + SearchPolicy
[Day 3-4] 实现 ResearchExecutor + SourceEvaluator + SolutionSynthesizer
[Day 5-7] 实现 ResearchMemory + 脱敏逻辑 + 与 Inbox 集成
[Day 8-10] 测试真实场景（ROS2 卡点、AI 查询循环、unknown 应用解释）
```

**验收标准：**
- 检测到技术卡点时，能生成合理的脱敏搜索 query
- 搜索结果优先使用官方文档 / GitHub Issues
- 输出可执行建议，不是泛泛总结
- 用户接受建议后能沉淀到 ResearchMemory
- 同类问题再次出现时不重复搜索

---

### 总体时间线

```
Phase 1 (2周): State + Detector      -> 系统能"看懂"状态
Phase 2 (1周): Policy Engine         -> 系统能"判断"是否打扰
Phase 3 (1周): Inbox + Feedback      -> 系统能"学习"用户偏好
Phase 4 (1周): 打磨体验             -> 系统能"给出"精准建议
Phase 5 (1-2周): Research Loop       -> 系统能"研究"解决方案

总计：6-7 周 -> 从定时报表工具升级为 Local-first Personal Workflow & Research Agent
```

---

## 四、当前代码的具体修改点

### 需要新增的文件

```
src/aw_coach/
  state.py          # UserWorkState + 状态更新逻辑
  detector.py       # 所有 detector 实现
  policy.py         # PolicyEngine + InterruptBudget + AgentDecision
  inbox.py          # Inbox 管理（CRUD + CLI 集成）
  profile.py        # UserProfile + 自动学习

src/aw_coach/research/
  __init__.py
  hypothesis.py     # ProblemHypothesis
  planner.py        # ResearchPlanner
  executor.py       # ResearchExecutor
  evaluator.py      # SourceEvaluator
  synthesizer.py    # SolutionSynthesizer
  memory.py         # ResearchMemory
```

### 需要修改的文件

```
src/aw_coach/scheduler.py
  - 主循环：增加每分钟状态更新
  - _send_instant_summary(): 改为通过 PolicyEngine 决策
  - _generate_daily_report(): 增加"每日 One Change"

src/aw_coach/storage.py
  - _migrate(): 增加 v3（inbox 表 + user_profile 表）

src/aw_coach/cli.py
  - 新增命令：inbox, accept, dismiss, edit, state

src/aw_coach/collector.py
  - ActivitySlice: 增加 domain 字段
  - fetch_range(): 增加 min_duration_sec 过滤
```

---

## 五、关键设计决策建议

### 决策 1: State 更新频率

**建议：每分钟**

原因：
- ActivityWatch heartbeat 间隔通常为 1-2 分钟
- 5 分钟太慢，死亡循环可能在 5 分钟内已经发生并结束
- 1 秒太快，对系统资源消耗无意义

### 决策 2: 通知 vs Inbox 的默认比例

**建议：80% inbox，20% notify**

原因：
- 初期 Agent 的判断不一定准确，默认入 inbox 更安全
- 只有 severity >= 0.8 且通过所有 policy 检查才直接通知
- 随着 user_profile 学习，notify 比例可以逐渐提高

### 决策 3: 是否引入 asyncio

**建议：不引入**

原因：
- 当前 scheduler 使用 `time.sleep(60)` 的同步循环，简单可靠
- 引入 asyncio 会增加代码复杂度，但对功能没有实质提升
- detector 和 policy 的计算量很小，同步执行即可
- 如果未来需要并发处理多个 watcher，再考虑引入

### 决策 4: user_profile 存储格式

**建议：YAML 文件 + SQLite 混合**

```
user_profile.yml       # 用户可手动编辑的配置
SQLite corrections     # 系统自动记录的纠正历史
SQLite inbox           # 动态生成的建议条目
```

原因：
- YAML 适合用户手动编辑（best_focus_hours, sensitive_apps）
- SQLite 适合高频读写（inbox, corrections, detector_stats）

---

## 六、风险与应对

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| Agent 过于烦人 | 高 | 用户关闭服务 | Policy Engine + Interrupt Budget 严格限制 |
| 状态更新消耗资源 | 中 | 系统卡顿 | 每分钟只做增量更新，不做全量查询 |
| Detector 误报多 | 中 | 用户不信任 | 默认入 inbox 而非直接通知，逐步学习 |
| 代码复杂度激增 | 中 | 维护困难 | 模块化设计，每个 detector/policy 独立测试 |
| 隐私担忧 | 低 | 用户卸载 | 本地优先，不上传，敏感应用黑名单 |

---

## 七、结论

当前 aw-coach 已经完成了**数据收集、规则分类、LLM 辅助、报告生成、通知推送**的基础设施，相当于一个"功能丰富的生产力分析仪"。

要升级为"主动式 AI Agent"，**最关键的四个缺口**是：

1. **State Model** — 系统不知道"用户现在处于什么状态"
2. **Detector 层** — 系统只能发现 death_loop，不能发现 unknown/ai_coding/focus_block
3. **Policy Engine** — 系统不会判断"现在该不该打扰"
4. **Feedback Memory** — 系统不会记住"用户上次拒绝了这个建议"

**建议优先做 State + Detector + Policy，这三者形成最小可行闭环后，系统就已经具备"主动 Agent"的核心能力。** Inbox 和 Feedback Memory 是在此基础上的体验增强，Research Loop 是 L4→L5 的跃迁。

按照 6 周路线图实施，aw-coach 将从：

```
"每天 21:00 告诉你昨天做了什么"
```

升级为：

```
"在你卡住 20 分钟时轻轻提醒，
 并且我已经帮你查了解决方案，
 在你专注时不打扰，
 在你分类不清时主动询问，
 在你拒绝建议后学会闭嘴，
 每天只给一条最值得改的建议。"
```

这才是真正的 Local-first Personal Workflow & Research Agent。

### 更新后的 Agent 能力分级

| 等级 | 能力 | 当前状态 | 预计完成时间 |
|------|------|---------|------------|
| L1 | 记录行为 | ✅ 完成 | 已上线 |
| L2 | 分类总结 | ✅ 完成 | 已上线 |
| L3 | 主动发现问题 | 🟡 death_loop 已做，待扩展 | Phase 1 (2周) |
| L4 | 主动建议动作 | ❌ 待实现 | Phase 2-3 (2周) |
| L5 | **主动研究方案** | ❌ 待实现 | Phase 4 (1-2周) |
| L6 | 半自治执行改进 | ❌ 待实现 | Phase 5 (1周) |
