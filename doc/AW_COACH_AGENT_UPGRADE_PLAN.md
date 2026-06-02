# aw-coach Agent 化升级计划

> 版本：v1.0
> 日期：2026-06-01
> 状态：待实施

---

## 一、当前位置诊断

### 1.1 已完成（绿色）

| 模块 | 状态 | 说明 |
|------|------|------|
| Observer 观察层 | ✅ | DataCollector + AFK过滤 + URL合并 + heartbeat合并 |
| Normalizer 清洗层 | ✅ | ActivitySlice + 时区处理 + 去抖动 |
| Rules Engine 规则分类 | ✅ | 42条内置规则 + user.yml + HybridBackend(LLM兜底) |
| Analyzer 离线分析 | ✅ | focus_score / productivity_score / death_loops / 深度工作检测 |
| Reporter 报告生成 | ✅ | Markdown日报/周报 + HTML仪表盘 + AI建议(DeepSeek) |
| Scheduler 定时调度 | ✅ | 每小时分析 + 即时摘要 + 日报通知 + 断点续传 |
| Notify 通知系统 | ✅ | dbus action + 点击打开仪表盘 + 跨平台分支 |
| Storage 持久化 | ✅ | SQLite: cost_log / batch_queue / corrections / scheduler_state |
| CLI 交互生态 | ✅ | 11个命令 + 交互式calibrate + rule-suggest |
| Cost 成本控制 | ✅ | 月度预算 + 阈值告警 + 自动降级 |

### 1.2 核心缺失（红色）

| 模块 | 状态 | 影响 |
|------|------|------|
| **State Model 实时状态层** | ❌ | 系统不知道"用户现在处于什么状态" |
| **Detector 事件检测层** | 🟡 | 只有death_loop，无法识别unknown/ai_coding_loop/focus_block |
| **Policy Engine 决策层** | ❌ | 系统不会判断"现在该不该打扰用户" |
| **Action Executor 行动层** | 🟡 | 只有notify，无Agent Inbox/半自治模式 |
| **Feedback Memory 学习层** | ❌ | 系统不会记住"用户上次拒绝了这个建议" |
| **Research Loop 研究层** | ❌ | 系统只能总结数据，不能主动研究解决方案 |

### 1.3 关键结论

当前 aw-coach 是 **ActivityWatch 生态中产品化最完整的 AI 分析工具**，但本质上仍是**"定时报表生成器"**。

要升级为 **Local-first Personal Workflow Agent**，必须补齐四层：

```
State（看懂状态） → Detector（发现问题） → Policy（判断干预） → Action（执行建议）
```

---

## 二、总体愿景

```text
从："每天21:00告诉你昨天做了什么"

到："在你卡住20分钟时轻轻提醒，
     并且我已经查了解决方案，
     在你专注时不打扰，
     在你分类不清时主动询问，
     在你拒绝建议后学会闭嘴，
     每天只给一条最值得改的建议。"
```

---

## 三、分阶段实施计划

### Phase 1: 感知升级 — State Model + Detector 层（2 周）

**目标**：让系统"看懂"用户现在的状态。

**新增文件**：

```
src/aw_coach/
  state.py          # UserWorkState + 状态更新逻辑
  detector.py       # 所有 detector 实现
```

**具体任务**：

| 天数 | 任务 | 产出 |
|------|------|------|
| Day 1-2 | 实现 `UserWorkState` dataclass | `state.py` |
| Day 3-4 | 集成到 `scheduler.py`，每分钟更新状态 | 状态持久化到 SQLite |
| Day 5 | 实现 `unknown_detector` + `high_switch_detector` | `detector.py` (2/5) |
| Day 6-7 | 实现 `ai_coding_loop_detector` + `focus_block_detector` | `detector.py` (4/5) |
| Day 8 | 实现 `browser_search_loop_detector` | `detector.py` (5/5) |
| Day 9 | 统一 `AgentSignal` 结构，CLI 新增 `aw-coach state` 命令 | 可实时查看状态 |
| Day 10 | 测试：验证各 detector 在真实数据上的触发准确率 | 测试报告 |

**关键设计**：

```python
@dataclass
class UserWorkState:
    updated_at: datetime
    current_app: str
    current_title: str
    current_url: Optional[str]
    current_activity: str
    current_confidence: float
    active_block_start: datetime
    active_block_category: str
    active_block_duration_sec: int
    switches_last_5min: int
    switches_last_30min: int
    switches_last_hour: int
    focus_score_rolling: float
    productivity_score_rolling: float
    likely_mode: str   # coding / debugging / reading / meeting / chatting / browsing / idle
    risk_level: str    # normal / fragmented / stuck / distracted / unknown
    last_user_feedback_at: Optional[datetime]
    last_agent_notification_at: Optional[datetime]
    notifications_today: int
```

**验收标准**：
- `aw-coach state` 能实时打印当前状态
- 系统能判断"当前是 coding / reading / chatting / idle"
- 系统能识别"当前是否处于专注块"
- 系统能识别"当前是否碎片化"

---

### Phase 2: 决策升级 — Policy Engine（1 周）

**目标**：让系统"知道什么时候该说话，什么时候该闭嘴"。

**新增文件**：

```
src/aw_coach/
  policy.py         # PolicyEngine + InterruptBudget + AgentDecision
```

**具体任务**：

| 天数 | 任务 | 产出 |
|------|------|------|
| Day 1-2 | 实现 `PolicyEngine` + `InterruptBudget` + `AgentDecision` | `policy.py` |
| Day 3 | 集成到 `scheduler.py`，替换直接通知逻辑 | 所有通知走 Policy |
| Day 4 | 实现 quiet_hours + cooldown + focus protect + 每日上限 | 配置项扩展 |
| Day 5 | 测试不同场景下的决策正确性 | 测试用例 |

**关键设计**：

```python
@dataclass
class InterruptBudget:
    max_per_day: int = 4
    min_interval_minutes: int = 45
    quiet_hours: List[Tuple[str, str]] = field(default_factory=lambda: [("12:00", "13:30"), ("22:30", "09:00")])
    suppress_after_dismiss: int = 2

class PolicyEngine:
    def decide(self, signal: AgentSignal, state: UserWorkState) -> AgentDecision:
        # 1. quiet hours -> suppress
        # 2. 今日通知次数 -> inbox
        # 3. 上次通知间隔 -> delay
        # 4. focus_block > 20min -> log_only
        # 5. 用户历史 dismiss -> suppress
        # 6. severity >= 0.8 -> notify_now
        # 7. severity >= 0.5 -> inbox
        # 8. 其他 -> log_only
```

**验收标准**：
- 用户处于 focus_block > 20min 时，不发任何通知
- 每日通知不超过 4 次
- 两次通知间隔 >= 45min
- 低严重度事件只入 inbox，不强提醒

---

### Phase 3: 交互升级 — Agent Inbox + User Profile（1-2 周）

**目标**：让 Agent 的建议可确认、可拒绝、可学习。

**新增文件**：

```
src/aw_coach/
  inbox.py          # Inbox 管理（CRUD + CLI 集成）
  profile.py        # UserProfile + 自动学习
```

**具体任务**：

| 天数 | 任务 | 产出 |
|------|------|------|
| Day 1-2 | 实现 inbox 表 + `inbox.py` 管理模块 | SQLite v4 迁移 |
| Day 3 | CLI 新增 `inbox` / `accept` / `dismiss` / `edit` 命令 | CLI 扩展 |
| Day 4 | 实现 `UserProfile` + 自动学习 best_focus_hours | `profile.py` |
| Day 5 | 集成到 PolicyEngine（dismiss 记忆影响后续决策） | 闭环完成 |
| Day 6-7 | 打磨：inbox 通知内容上下文更丰富 | 体验优化 |

**关键设计**：

```yaml
# ~/.local/share/activitywatch/aw-watcher-ai-coach/user_profile.yml
profile:
  best_focus_hours:
    - start: "09:30"
      end: "11:30"
      confidence: 0.85
  detector_stats:
    death_loop:
      accepted: 5
      dismissed: 1
    focus_guard:
      accepted: 1
      dismissed: 6
  sensitive_apps:
    - WeChat
    - 1Password
```

**验收标准**：
- `aw-coach inbox` 能列出待处理建议
- `aw-coach dismiss` 后，同类建议频率降低
- 系统能自动学习用户的高效时段

---

### Phase 4: 数据深度 — 项目追踪 + Site 分析 + OCR（2 周）

**目标**：让分析从"应用级"突破到"项目级"。

**具体任务**：

| 天数 | 任务 | 产出 |
|------|------|------|
| Day 1-2 | `collector.py` 提取 domain 字段；URL 解析增强 | ActivitySlice 扩展 |
| Day 3-4 | Git 项目追踪：读取进程工作目录的 git repo + 分支 | 项目维度统计 |
| Day 5-6 | 浏览器 site-level 时间分布分析 | 报告增强 |
| Day 7-8 | 截图 OCR 触发式采集：`screen_sensor.py` + PaddleOCR GPU | `screen_sensor.py` |
| Day 9-10 | 集成到 Detector：OCR 文本辅助 unknown / low confidence 判断 | Detector 增强 |

**关键设计**：

```python
@dataclass
class ActivitySlice:
    # 现有字段...
    domain: Optional[str] = None      # 从 URL 提取
    git_repo: Optional[str] = None    # 从进程 CWD 提取
    git_branch: Optional[str] = None
    screen_context: Optional[ScreenContext] = None  # OCR 结果
```

**截图策略**：

```yaml
screen_understanding:
  enabled: false          # 默认关闭
  mode: triggered_only    # 绝不定时扫描
  triggers:
    - unknown_block_duration_gt: 10min
    - low_confidence_duration_gt: 15min
    - death_loop_detected: true
  never_capture_apps:
    - WeChat
    - 1Password
  store_raw_image: false
  store_ocr_text: true
```

**验收标准**：
- 报告从"VS Code 3h"升级到"x_system 项目 3h (main分支)"
- 浏览器时间能区分 github.com / stackoverflow.com / youtube.com
- unknown 场景触发 OCR 后，分类准确率提升

---

### Phase 5: 跨平台 — 服务安装与路径适配（1 周）

**目标**：让 Linux/macOS/Windows 用户都能开箱即用。

**新增文件**：

```
src/aw_coach/
  platform.py           # 跨平台抽象层
  service_installer.py  # systemd / launchd / Task Scheduler 安装器
```

**具体任务**：

| 天数 | 任务 | 产出 |
|------|------|------|
| Day 1-2 | 使用 `platformdirs` 替换硬编码 XDG 路径 | `config.py` 改造 |
| Day 3 | 实现 `PlatformPaths` + `ServiceManager` | `platform.py` |
| Day 4 | Linux systemd + macOS launchd 安装器 | `service_installer.py` |
| Day 5 | Windows Task Scheduler 安装器 + 终端编码修复 | `service_installer.py` |

**CLI 新增命令**：

```bash
aw-coach install-service    # 自动识别平台并安装后台服务
aw-coach uninstall-service
aw-coach start-service
aw-coach stop-service
```

**验收标准**：
- `pip install` 后在 Linux/macOS/Windows 均可运行
- `aw-coach doctor` 在各平台正确诊断
- 后台服务在各平台正确安装和自启

---

### Phase 6: Research Loop — 主动研究（2-3 周）

**目标**：从 L3（主动发现问题）跃迁到 L5（主动研究方案）。

**新增文件**：

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

**触发场景**：

| 场景 | 当前系统 | Research Loop 后 |
|------|---------|-----------------|
| 技术卡点 | "你在 ROS2 资料上停留很久" | "你可能卡在 executor 安装路径问题，建议检查 install(PROGRAMS) / console_scripts" |
| AI 查询循环 | "你可能陷入了搜索循环" | "建议你执行'验证优先'原则：每 20 分钟必须回到代码或笔记" |
| unknown 应用 | "请运行 aw-coach calibrate 分类" | "这个应用是 XXX 工具，通常用于 YYY，建议标记为 programming" |

**Search Policy 边界**：

```yaml
research_policy:
  enabled: true
  mode: triggered_only
  max_searches_per_day: 5
  min_interval_minutes: 60
  never_send:
    - raw_screenshot
    - chat_content
    - api_key
    - full_source_code
    - internal_url
    - file_path
  sanitize: true
  prefer_sources:
    - official_docs
    - github_issues
    - release_notes
```

**验收标准**：
- 检测到技术卡点时，能生成合理的脱敏搜索 query
- 搜索结果优先使用官方文档 / GitHub Issues
- 输出可执行建议，不是泛泛总结
- 用户接受建议后沉淀到 ResearchMemory
- 同类问题再次出现时不重复搜索

---

## 四、总体时间线

```
Phase 1 (2周): State + Detector         -> 系统能"看懂"状态
Phase 2 (1周):  Policy Engine            -> 系统能"判断"是否打扰
Phase 3 (1-2周): Inbox + Feedback        -> 系统能"学习"用户偏好
Phase 4 (2周):  项目追踪 + Site + OCR    -> 系统能"精准"分析
Phase 5 (1周):  跨平台适配              -> 系统能"到处"运行
Phase 6 (2-3周): Research Loop          -> 系统能"研究"方案

总计：9-11 周 -> 从定时报表工具升级为 Local-first Personal Workflow & Research Agent
```

---

## 五、关键设计决策

### 决策 1: State 更新频率
**建议：每分钟**
- ActivityWatch heartbeat 间隔通常为 1-2 分钟
- 5 分钟太慢，死亡循环可能在 5 分钟内已经发生并结束
- 1 秒太快，对系统资源消耗无意义

### 决策 2: 通知 vs Inbox 的默认比例
**建议：80% inbox，20% notify**
- 初期 Agent 的判断不一定准确，默认入 inbox 更安全
- 只有 severity >= 0.8 且通过所有 policy 检查才直接通知
- 随着 user_profile 学习，notify 比例可以逐渐提高

### 决策 3: 是否引入 asyncio
**建议：不引入**
- 当前 scheduler 使用 `time.sleep(60)` 的同步循环，简单可靠
- 引入 asyncio 会增加代码复杂度，但对功能没有实质提升
- detector 和 policy 的计算量很小，同步执行即可

### 决策 4: user_profile 存储格式
**建议：YAML 文件 + SQLite 混合**
- YAML 适合用户手动编辑（best_focus_hours, sensitive_apps）
- SQLite 适合高频读写（inbox, corrections, detector_stats）

### 决策 5: 截图识别优先级
**建议：P3，最后做**
- 当前 `app + title + url` 已能覆盖 80% 场景
- 截图涉及隐私风险，需要更完善的脱敏和权限控制
- 应先验证 "State + Detector + Policy" 闭环的价值，再考虑增强感知

---

## 六、风险与应对

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| Agent 过于烦人 | 高 | 用户关闭服务 | Policy Engine + Interrupt Budget 严格限制；默认 80% 入 inbox |
| 状态更新消耗资源 | 中 | 系统卡顿 | 每分钟只做增量更新，不做全量查询 |
| Detector 误报多 | 中 | 用户不信任 | 默认入 inbox 而非直接通知；逐步学习 |
| 代码复杂度激增 | 中 | 维护困难 | 模块化设计，每个 detector/policy 独立测试 |
| 隐私担忧 | 低 | 用户卸载 | 本地优先，不上传；敏感应用黑名单；截图默认关闭 |
| Research Loop 搜索结果质量差 | 中 | 用户觉得没用 | 严格限制搜索次数；优先官方文档；脱敏机制 |

---

## 七、与现有文档的关系

| 文档 | 本计划吸收的内容 |
|------|----------------|
| `AI_COACH_DESIGN.md` | 六层架构、配置系统、报告生成设计 |
| `AI_COACH_IMPL_PLAN.md` | MVP 已实现，本计划是后续阶段 |
| `AI_COACH_CLI_DESIGN.md` | CLI 命令扩展设计（state, inbox, accept, dismiss, edit）|
| `active-agent-roadmap.md` | Agent 化愿景、Fast/Slow Brain、六阶段路线 |
| `active-agent-analysis.md` | 当前代码诊断、State/Detector/Policy 缺口分析 |
| `AI_COACH_COMPETITIVE_ANALYSIS.md` | 竞品差距中已补齐 Calibration/Weight/Death Loop，剩余差距纳入 Phase 4 |
| `cross-platform-migration.md` | Phase 5 跨平台实施直接引用 |
| `ocr-vlm-recommendation.md` | Phase 4 OCR 实施直接引用 PaddleOCR GPU 方案 |
| `WORK_ANALYSIS_REPORT.md` | 数据分析方法论复用 |

---

## 八、立即开始的下一步

如果今天就开始，优先顺序是：

1. **创建 `src/aw_coach/state.py`** — 实现 `UserWorkState` dataclass（2小时）
2. **修改 `src/aw_coach/scheduler.py`** — 主循环每分钟更新并持久化状态（2小时）
3. **CLI 新增 `aw-coach state` 命令** — 实时查看当前状态（1小时）

这三个任务构成**最小可行闭环**，完成后即可验证"系统能否看懂用户状态"这一核心假设。
