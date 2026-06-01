# aw-coach 主动式 AI Agent 改进计划

## 1. 背景与目标

aw-coach 当前可以理解为基于 ActivityWatch 的个人生产力分析工具。它已经具备以下基础能力：

* ActivityWatch 数据读取
* 应用 / 标题 / 时间窗口分析
* 规则分类
* LLM 辅助分类
* 成本控制
* 日报 / 周报
* systemd daemon 常驻
* 用户纠正与规则演化

但如果目标是探索"主动式 AI Agent"，项目不应停留在"定时生成报告"或"调用 LLM 总结数据"的阶段，而应升级为一个具备持续观察、状态建模、事件检测、策略决策、主动行动和反馈学习能力的本地工作流 Agent。

最终目标：

```text
aw-coach = Local-first Personal Workflow Agent
```

它的核心任务不是简单统计时间，而是：

```text
持续理解我的工作状态
识别低效 / 阻塞 / 分心 / 高质量专注状态
判断是否需要主动干预
用最小打扰推动我回到有效行动
从我的反馈中学习并逐渐个性化
```

---

## 2. 我们对主动 AI Agent 的核心共识

### 2.1 主动 Agent 不是"定时调用 LLM"

低级主动化：

```text
定时读取数据
定时调用 LLM
定时生成报告
定时通知用户
```

这更像定时任务，不是真正的主动 Agent。

真正的主动 Agent 应该是：

```text
持续观察环境
维护当前状态
识别状态变化
判断是否需要行动
选择合适动作
执行低风险动作
请求用户确认中高风险动作
根据反馈调整策略
```

---

### 2.2 aw-coach 天然适合做主动 Agent

因为它已经拥有主动 Agent 所需的几个关键条件：

```text
ActivityWatch 数据流       -> 环境观察
规则 / LLM 分类            -> 状态理解
Death Loop / focus 分析    -> 异常检测
通知 / 报告 / correction   -> 行动入口
user.yml / corrections     -> 反馈记忆
systemd daemon             -> 长期运行载体
```

因此，aw-coach 不应该只做成：

```text
AI productivity analyzer
```

而应升级为：

```text
Local-first Personal Workflow Agent
```

---

### 2.3 主动 Agent 的关键不是更强 LLM

项目的核心不应是：

```text
把更多数据喂给 LLM
让 LLM 决定一切
让 LLM 每隔几分钟分析一次
```

而应该是：

```text
State Model
Detector
Policy Engine
Action Executor
Feedback Loop
Privacy Guard
```

LLM 只是其中一个工具，适合做自然语言解释、复杂分类、规则建议、日报总结，不适合直接控制系统行为。

---

## 3. 总体架构升级

建议将 aw-coach 拆成六层：

```text
+------------------------------------+
| 1. Observer 观察层                  |
| ActivityWatch / watcher-web / AFK   |
+------------------------------------+
                 |
                 v
+------------------------------------+
| 2. State 状态层                     |
| 当前工作状态 / 专注块 / 切换模式     |
+------------------------------------+
                 |
                 v
+------------------------------------+
| 3. Detector 事件检测层              |
| death loop / unknown / distraction  |
+------------------------------------+
                 |
                 v
+------------------------------------+
| 4. Policy 决策层                    |
| 是否干预 / 何时干预 / 如何干预       |
+------------------------------------+
                 |
                 v
+------------------------------------+
| 5. Action 行动层                    |
| notify / ask / suggest / write rule |
+------------------------------------+
                 |
                 v
+------------------------------------+
| 6. Feedback 学习层                  |
| correction / accept / dismiss       |
+------------------------------------+
```

这套架构比"读取数据 -> 调用 LLM -> 发报告"更接近真实 Agent。

---

## 4. 核心模块设计

### 4.1 Observer：观察层

观察层负责从 ActivityWatch 和相关 watcher 中读取原始行为数据。

优先数据源：

```text
aw-watcher-window
aw-watcher-afk
aw-watcher-web
后续可选：screenshot OCR / local VLM
```

第一阶段必须先做好：

```text
app
title
url
duration
timestamp
AFK
browser domain
browser path
```

不建议第一阶段就把截图作为核心输入。

---

### 4.2 Normalizer：数据清洗层

原始 ActivityWatch 数据会比较碎，必须先做清洗。

需要支持：

```text
heartbeat 合并
短切换去抖动
AFK 过滤
浏览器 URL 合并
重复事件合并
过短事件忽略
跨 bucket 时间对齐
```

推荐输出统一结构：

```python
@dataclass
class NormalizedEvent:
    start: datetime
    end: datetime
    duration_sec: int

    app: str
    title: str
    url: str | None
    domain: str | None

    source_bucket: str
    is_afk: bool
```

这一层非常关键。没有可靠数据清洗，后面的 Agent 决策会建立在噪声数据上。

---

### 4.3 State Model：状态层

主动 Agent 必须知道"我现在处于什么状态"。

建议维护一个 `UserWorkState`：

```python
@dataclass
class UserWorkState:
    current_app: str
    current_title: str
    current_url: str | None

    current_activity: str
    current_confidence: float

    active_block_start: datetime
    active_block_category: str

    switches_last_5min: int
    switches_last_30min: int

    focus_score_rolling: float
    productivity_score_rolling: float

    likely_mode: str
    # coding / debugging / reading / meeting / chatting / browsing / idle

    risk_level: str
    # normal / fragmented / stuck / distracted / unknown

    last_user_feedback_at: datetime | None
    last_agent_notification_at: datetime | None
```

状态层是主动 Agent 的核心。

没有状态层，系统只能做离线分析；有了状态层，系统才能判断：

```text
用户正在深度工作，不应打扰
用户已卡在 ChatGPT/Chrome 循环 30 分钟，可以轻提醒
用户 unknown 时间过高，需要 calibration
用户今天已经拒绝多次提醒，应进入静默
```

---

### 4.4 Detector：事件检测层

Detector 负责从状态中识别值得关注的信号。

建议统一输出：

```python
@dataclass
class AgentSignal:
    type: str
    severity: float
    confidence: float
    start_time: datetime
    end_time: datetime
    evidence: dict
    suggested_actions: list[str]
```

第一批 detector 建议实现：

```text
unknown_detector
high_switch_detector
death_loop_detector
ai_coding_loop_detector
focus_block_detector
browser_search_loop_detector
llm_budget_detector
```

#### 4.4.1 unknown_detector

识别 unknown 时间过高。

触发条件示例：

```text
今日 unknown 时间 > 30min
或某个 app/title/domain 连续 3 次被判定为 unknown
```

建议动作：

```text
主动询问用户如何分类
生成 user.yml 规则草案
```

---

#### 4.4.2 death_loop_detector

识别注意力循环。

典型模式：

```text
ChatGPT <-> Chrome <-> ChatGPT <-> Chrome
VSCode <-> 微信 <-> VSCode <-> 微信
IDE <-> Browser <-> ChatGPT <-> Browser
```

触发条件示例：

```text
A/B/C 高频切换
持续时间 >= 20min
切换次数 >= 10
没有回到稳定输出应用
```

建议动作：

```text
提醒用户定义下一步具体输出
建议进入 25min coding block
建议写一段笔记或回到代码验证
```

---

#### 4.4.3 ai_coding_loop_detector

识别 AI 辅助开发闭环。

正常模式：

```text
VSCode -> Codex -> Terminal -> VSCode
VSCode -> ChatGPT -> Docs -> VSCode
Terminal -> ChatGPT -> Terminal
```

这种不应被简单判定为分心。

异常模式：

```text
ChatGPT -> Chrome -> ChatGPT -> Chrome
长时间没有回到 VSCode / Terminal / 文档产出
```

这种应判定为 research loop 或 AI query loop。

目标：

```text
区分 AI 在推进工作，还是 AI 替代了行动。
```

---

#### 4.4.4 focus_block_detector

识别高质量专注块。

特征：

```text
连续工作时间较长
应用集合稳定
切换次数低
AFK 少
分类置信度高
```

动作：

```text
记录该专注块
日报中总结
不要打扰用户
进入 protect mode
```

主动 Agent 不只是主动说话，也包括主动保持沉默。

---

### 4.5 Policy Engine：决策层

Detector 只负责发现信号，不能直接通知用户。

中间必须有 Policy Engine：

```python
decision = policy.decide(signal, state, user_profile)
```

输出：

```python
@dataclass
class AgentDecision:
    action_type: str
    priority: int
    requires_confirmation: bool
    reason: str
    expires_at: datetime | None
```

可能的 action_type：

```text
notify_now
ask_for_feedback
create_rule_suggestion
generate_review_item
delay_until_idle
log_only
suppress
```

Policy Engine 要解决的问题：

```text
这个信号是否足够重要？
现在是否适合打扰用户？
今天是否已经提醒太多？
用户是否处于深度工作？
用户是否经常拒绝这类建议？
该动作是否需要确认？
```

---

### 4.6 Interrupt Budget：打扰预算

主动 Agent 最大风险是烦人。

必须配置打扰预算：

```yaml
interrupt_policy:
  max_notifications_per_day: 4
  min_interval_minutes: 45
  no_interrupt_if_focus_block_longer_than: 20

  quiet_hours:
    - "12:00-13:30"
    - "22:30-09:00"

  suppress_after_dismiss_count: 2
```

基本规则：

```text
用户深度工作时不打扰
短时间内不重复提醒
低置信度问题只记录不提醒
用户连续拒绝后降低提醒频率
没有可行动建议时不提醒
```

Agent 的体验好坏，很大程度上取决于是否懂得克制。

---

### 4.7 Action Executor：行动层

Action Executor 负责真正执行动作。

建议先支持低风险动作：

```text
生成日报
生成周报
生成 rule suggestion
写入 agent inbox
发送 notify-send
标记低置信度样本
触发 reclassify 草案
```

暂时不要做高风险动作：

```text
自动关闭应用
自动拦截网站
自动修改系统设置
自动发送外部消息
自动上传截图
```

---

### 4.8 Agent Inbox：建议收件箱

不建议让 Agent 直接修改所有配置。

推荐引入 `agent_inbox`：

```text
~/.aw-coach/inbox/
  2026-05-30_unknown_calibration.json
  2026-05-30_death_loop.json
  2026-05-30_rule_suggestion.json
```

用户可以用 CLI 处理：

```bash
aw-coach inbox
aw-coach accept 3
aw-coach dismiss 4
aw-coach edit 5
```

这样可以形成安全的半自治模式：

```text
Agent 主动发现问题
Agent 生成建议
用户确认
系统执行
反馈进入记忆
```

---

### 4.9 Feedback Memory：反馈学习层

Agent 必须记住用户反馈。

建议维护：

```yaml
user_profile:
  prefers_notifications: false

  best_focus_hours:
    - "09:30-11:30"
    - "20:30-22:30"

  accepted_suggestions:
    death_loop_intervention: 5
    calibration_prompt: 12

  dismissed_suggestions:
    focus_guard: 6

  sensitive_apps:
    - WeChat
    - 1Password
    - Bitwarden

  ai_coding_apps:
    - ChatGPT
    - Claude
    - Codex
    - Cursor
```

反馈应该影响后续行为：

```text
经常接受 calibration -> 可以适当多问
经常拒绝 focus guard -> 减少提醒
晚上专注度高 -> 晚上不轻易打扰
ChatGPT 常被用户标为工作 -> 不轻易扣分
```

---

## 5. LLM 使用策略

LLM 不应该接管整个 Agent。

推荐分成两个脑子：

```text
Fast Brain：规则脑
Slow Brain：LLM 脑
```

### 5.1 Fast Brain

每分钟运行，便宜、稳定、可解释：

```text
状态更新
事件检测
通知节流
权限判断
低风险动作
规则分类
```

### 5.2 Slow Brain

只在必要时运行：

```text
复杂活动解释
低置信度分类
日报 / 周报生成
规则草案生成
行为模式总结
自然语言建议
```

### 5.3 LLM 调用原则

```text
规则能判定的，不调用 LLM
用户纠正过的，不调用 LLM
敏感应用，不调用 LLM
只有 unknown / low confidence 才调用 LLM
超过预算后自动降级到 rule_only
```

---

## 6. 截图识别策略

截图识别可以提高上下文理解能力，但不应作为第一优先级。

正确定位：

```text
截图识别 = 高阶上下文传感器
不是 Agent 的核心入口
```

### 6.1 为什么截图有价值

ActivityWatch 常规数据只能看到：

```text
app
title
url
duration
AFK
```

截图 / OCR / VLM 能补足屏幕语义：

```text
VSCode 中是在写代码、看日志还是读文档
Terminal 中是在 build、test、debug 还是报错
Chrome 中是在技术文档、搜索结果还是娱乐网站
ChatGPT 中是在推进任务还是反复绕圈
远程桌面 / RViz / 机器人控制台中到底在看什么
```

### 6.2 为什么不应默认开启

截图高度敏感，可能包含：

```text
聊天内容
公司代码
API key
内部文档
个人信息
账号密码
文件路径
浏览记录
```

因此必须遵守：

```text
默认关闭
本地优先
触发式截图
默认不保存原图
敏感应用永不截图
外部 LLM 调用前必须脱敏
```

### 6.3 推荐截图策略

```yaml
screen_understanding:
  enabled: false
  mode: triggered_only

  triggers:
    - unknown_block_duration_gt: 10min
    - low_confidence_duration_gt: 15min
    - death_loop_detected: true
    - browser_search_loop_gt: 20min
    - ai_chat_without_code_return_gt: 25min

  never_capture_apps:
    - WeChat
    - 企业微信
    - Feishu
    - DingTalk
    - 1Password
    - Bitwarden

  capture_cooldown_minutes: 10
  store_raw_image: false
  store_ocr_text: true
```

### 6.4 实施顺序

```text
第一步：先做 OCR，不做 VLM
第二步：只在 unknown / low confidence 时触发
第三步：只保存结构化 ScreenContext，不保存原图
第四步：后期再考虑 local VLM
```

ScreenContext 结构：

```python
@dataclass
class ScreenContext:
    captured_at: datetime
    app: str
    title: str
    source: str  # ocr / local_vlm / external_vlm

    visible_text: str | None
    summary: str | None
    keywords: list[str]

    inferred_task: str | None
    confidence: float

    raw_image_path: str | None = None
    redacted: bool = True
```

---

## 7. 权限与安全边界

主动 Agent 必须区分不同风险级别。

### 7.1 可自动执行

```text
合并事件
计算评分
生成日报
生成周报
缓存 LLM 结果
标记低置信度样本
生成规则草案
写入 agent inbox
```

### 7.2 需要用户确认

```text
写入 user.yml
调用外部 LLM
启用截图识别
重分类历史数据
修改通知策略
创建专注计划
```

### 7.3 暂不建议做

```text
关闭应用
拦截网站
读取聊天内容
上传截图
修改系统设置
自动发消息
自动操作浏览器
```

短期目标应该是 L3 级主动 Agent：

```text
主动识别
主动询问
主动建议
一键执行
低风险自治
```

不建议直接进入 L5 强自治。

---

## 8. 分阶段改进路线

### 阶段一：夯实数据基础

目标：

```text
每天稳定产出可信数据
```

任务：

```text
接入 aw-watcher-window
接入 aw-watcher-afk
接入 aw-watcher-web
实现 heartbeat 合并
实现 AFK 过滤
实现 browser URL 合并
实现基础分类规则
实现每日 Markdown / HTML 报告
```

验收标准：

```text
数据不中断
浏览器时间可到 domain/path 级别
unknown 时间占比下降
日报能准确反映当天主要工作
```

---

### 阶段二：建立 Agent 状态模型

目标：

```text
从离线统计升级为实时状态理解
```

任务：

```text
实现 UserWorkState
实现滚动窗口统计
实现 switches_last_5min / 30min
实现 current_activity + confidence
实现 active_block
实现 likely_mode
实现 risk_level
```

验收标准：

```text
系统能判断当前是 coding / reading / chatting / idle
系统能识别当前是否处于专注块
系统能识别当前是否碎片化
```

---

### 阶段三：实现 Detector 层

目标：

```text
让系统主动发现值得关注的事件
```

任务：

```text
unknown_detector
high_switch_detector
death_loop_detector
ai_coding_loop_detector
focus_block_detector
browser_search_loop_detector
```

验收标准：

```text
能识别 unknown 高发场景
能识别 ChatGPT/Chrome 循环
能识别 AI coding 正常闭环
能识别高质量专注块
```

---

### 阶段四：实现 Policy Engine

目标：

```text
让系统学会是否该打扰
```

任务：

```text
实现 AgentSignal
实现 AgentDecision
实现 interrupt budget
实现 quiet hours
实现 cooldown
实现 deep work protect mode
```

验收标准：

```text
不是 detector 一触发就通知
深度工作中不打扰
每天通知数量受控
低置信度事件只入 inbox，不强提醒
```

---

### 阶段五：实现 Agent Inbox 与反馈闭环

目标：

```text
让 Agent 的建议可确认、可拒绝、可学习
```

任务：

```text
实现 ~/.aw-coach/inbox
实现 aw-coach inbox
实现 aw-coach accept
实现 aw-coach dismiss
实现 aw-coach edit
实现 correction -> rule suggestion
实现 user_profile 更新
```

验收标准：

```text
用户可以处理 Agent 建议
接受建议后能生成规则
拒绝建议后能降低同类提醒频率
系统能逐渐减少重复误判
```

---

### 阶段六：引入按需 LLM

目标：

```text
让 LLM 负责解释和建议，而不是负责所有决策
```

任务：

```text
低置信度分类
规则草案生成
日报自然语言总结
Death Loop 解释
One Change 建议
成本预算控制
敏感字段脱敏
```

验收标准：

```text
LLM 调用量可控
敏感应用不上传
报告可读性明显提升
规则建议质量可用
```

---

### 阶段七：引入触发式截图 / OCR

目标：

```text
提升低置信度场景的上下文理解能力
```

任务：

```text
实现 screen_sensor
实现 triggered screenshot
实现本地 OCR
实现 sensitive app blacklist
实现 raw image 不落盘
实现 ScreenContext
实现 OCR 辅助分类
```

验收标准：

```text
unknown 场景分类更准确
Terminal / VSCode / Browser 语义识别增强
截图不默认启用
敏感应用不会被截图
默认不保存原图
```

---

## 9. 第一版 MVP 建议

第一版主动 Agent 不要做太大。

建议只做四个主动场景：

### 9.1 unknown 主动归类

触发：

```text
unknown 时间 > 30min/day
或同一 unknown app 连续出现 3 次
```

动作：

```text
主动询问用户如何分类
生成规则草案
用户确认后写入 user.yml
```

---

### 9.2 Death Loop 干预

触发：

```text
A <-> B 高频切换
持续 >= 20min
切换次数 >= 10
没有回到稳定输出应用
```

动作：

```text
提醒用户定义下一步输出
建议回到 VSCode / Terminal / 笔记
```

---

### 9.3 AI Coding 模式识别

触发：

```text
AI app + IDE + Terminal + Docs 在 30min 内形成稳定循环
```

动作：

```text
标记为 ai_assisted_coding
不扣 focus 分
日报中总结该 block
```

---

### 9.4 每日 One Change

触发：

```text
每日结束时
```

动作：

```text
只给一个明天最值得调整的动作
```

示例：

```text
明天只改一件事：
当你连续 20 分钟停留在 ChatGPT/Chrome 查询链路时，
必须回到 VSCode 或写一段笔记。
```

---

## 10. 指标体系

建议最终不要只输出一个总分，而是输出四类指标：

```text
productivity_score：时间是否花在有价值活动上
focus_score：过程是否连续、是否被频繁打断
context_switch_score：切换是否异常
confidence_score：系统对分类结果是否有信心
```

日报示例：

```text
今天 productivity_score 78，但 focus_score 52。

说明：
你大部分时间确实在工作相关活动中，
但上下文切换过多。

主要问题：
15:00-17:00，VSCode、Chrome、ChatGPT、微信之间发生了 42 次切换。

建议：
明天在 15:00-17:00 设置一个 45 分钟 coding block，
只允许 VSCode、Terminal、技术文档。
```

---

## 11. 推荐优先级

短期优先级：

```text
P0: aw-watcher-web 集成
P0: heartbeat 合并 / AFK 过滤 / browser URL join
P0: UserWorkState
P0: unknown_detector
P0: death_loop_detector
P0: ai_coding_loop_detector
P1: Policy Engine + interrupt budget
P1: Agent Inbox
P1: correction -> rule suggestion
P1: 每日 One Change
P2: triggered OCR
P3: local VLM
```

不建议现在优先做：

```text
复杂仪表盘
跨平台 Accessibility API
全量 OCR
强制 Focus Guard
应用拦截
团队功能
商业化
```

---

## 12. 最终判断

aw-coach 最有价值的升级方向不是"更强的生产力报表"，而是：

```text
本地优先的个人工作流 Agent
```

它应该具备：

```text
持续观察
状态建模
模式检测
主动建议
安全执行
反馈学习
隐私保护
```

短期真正应该做的四个核心模块是：

```text
State Model
Detector
Policy Engine
Action Executor
```

截图识别、OCR、VLM 都是增强能力，不是基础能力。

---

## 13. Research Loop：主动研究能力（新增）

前面的架构让 aw-coach 成为 **"观察 + 建议"型 Agent**（L4）。

但真正的主动 Agent 不应该只停留在：

```text
"我发现你卡住了"
```

而应该升级到：

```text
"我发现你卡住了，并且我帮你查了解决方案"
```

这就是 **Research Loop / Knowledge Agent** 层。

---

### 13.1 核心定位

Research Loop 解决的不是"搜索"，而是 **"从问题到方案"**。

普通搜索：

```text
用户发现问题 -> 用户想关键词 -> 用户搜索 -> 用户筛选 -> 用户总结 -> 用户执行
```

主动 Agent：

```text
Agent 发现问题 -> Agent 形成假设 -> Agent 判断是否需要外部知识
-> Agent 自动生成搜索任务 -> Agent 搜索可靠来源
-> Agent 总结可执行方案 -> Agent 推送给用户确认
-> Agent 根据反馈沉淀经验
```

核心不是：

```text
aw-coach 可以联网搜索
```

而是：

```text
aw-coach 可以在识别问题后主动研究解决办法
```

---

### 13.2 适合主动搜索的场景

#### 场景 1：技术卡点

Agent 观察到：

```text
Terminal 连续出现编译错误
Chrome 反复搜索同一技术关键词
ChatGPT 反复讨论同类问题
VSCode 没有代码推进
```

判断：用户可能卡在某个技术点上。

主动生成搜索任务，输出排查路径：

```text
我观察到你在 ROS2 executor 相关资料上停留较久，搜索链路较碎片化。
建议你下一步按这个顺序阅读：
1. rclcpp Executor::spin_once_impl
2. wait_for_work
3. rcl_wait
4. rmw_fastrtps_cpp::rmw_wait
5. FastDDS WaitSetImpl::wait

并建议你做一个最小实验验证 wait_set 唤醒路径。
```

#### 场景 2：工作流低效

Agent 发现 ChatGPT ↔ Chrome 循环 45 分钟，没有回到代码。

普通提醒只能说"你可能陷入了搜索循环"。

加入 Research Loop 后：

```text
我发现你经常在 AI 查询和浏览器搜索之间循环。
我查找并总结了几个适合 AI Coding 的工作流约束：

1. 每次向 AI 提问前先写 expected output
2. 每 20 分钟必须回到代码或笔记
3. 对 AI 答案执行"验证优先"而不是"继续追问"
4. 复杂问题拆成 reproduce → inspect → patch → test 四步

建议你明天试行其中第 2 条。
```

#### 场景 3：工具 / 依赖 / 版本问题

Agent 发现你频繁打开 Boost.Beast docs、GitHub issues、编译错误页面。

主动搜索后给出：

```text
你当前的问题更像 async write 并发控制，而不是 WebSocket 协议问题。
建议优先检查：
1. 是否有并发 async_write
2. 是否使用 write_queue 串行化
3. session 生命周期是否由 shared_from_this 维持
4. strand 是否覆盖所有回调路径
```

#### 场景 4：学习路径优化

Agent 基于长期记录发现：

```text
你最近一周大量阅读 rclcpp executor，但很少做实验。
```

主动搜索并生成：

```text
本周建议实验：
1. 写一个两个 subscription + 一个 timer 的 MultiThreadedExecutor demo
2. 构造 MutuallyExclusive callback group 验证串行行为
3. 修改 QoS depth 观察回调延迟
4. 用 trace 工具观察 executor wakeup
```

#### 场景 5：方案改进

Agent 发现你在某个设计文档上停留较久，主动搜索相邻方案：

```text
当前方案缺少：
1. 权限等级
2. agent inbox
3. interrupt budget
4. research loop
5. feedback memory
```

---

### 13.3 搜索边界与隐私保护

**不能全量自动搜索：**

```text
每发现一个问题就联网
每隔几分钟搜索一次
把窗口标题 / 截图 / 代码 / 聊天内容直接发出去
```

正确方式：

```text
问题触发
低频搜索
隐私脱敏
可信源优先
结果进入 inbox
用户确认后再执行
```

**搜索必须经过 Search Policy：**

```yaml
research_policy:
  enabled: true
  mode: triggered_only

  max_searches_per_day: 5
  min_interval_minutes: 60

  require_confirmation_if:
    - contains_company_project_name
    - contains_file_path
    - contains_error_log
    - contains_private_url

  never_send:
    - raw_screenshot
    - chat_content
    - api_key
    - token
    - full_source_code
    - internal_url

  prefer_sources:
    - official_docs
    - github_issues
    - release_notes
    - standards_docs
    - high_quality_blogs
```

**搜索内容必须脱敏：**

```text
原始：/pkg/app/ats_dispatch_server/lib/ats_dispatch_server 找不到 ats_recorder.py
改写：ROS2 launch executable not found in libexec directory Python script installed to wrong location
```

即：`private problem -> generalized search query`

---

### 13.4 Research Loop 架构

新增模块：

```text
ProblemHypothesis        # 从 Detector 信号生成问题假设
ResearchPlanner          # 把假设转成搜索任务
SearchPolicy             # 判断是否需要搜索、是否脱敏
ResearchExecutor         # 执行搜索（web / docs / GitHub / local notes）
SourceEvaluator          # 给搜索结果分级
SolutionSynthesizer      # 把搜索结果转成可执行建议
ResearchMemory           # 沉淀已解决的问题，避免重复搜索
```

统一数据结构：

```python
@dataclass
class ProblemHypothesis:
    type: str
    severity: float
    confidence: float
    summary: str
    evidence: dict
    private_context: dict       # 本地敏感信息，不上传
    public_query_context: dict  # 脱敏后的搜索上下文
    need_research: bool
    research_scope: str

@dataclass
class ResearchTask:
    query: str
    source_policy: str
    recency: str
    priority: int
    requires_confirmation: bool

@dataclass
class ResearchResult:
    source: str
    url: str
    title: str
    snippet: str
    credibility: float   # 0.0 - 1.0
    recency: str
    matched_problem: bool
```

---

### 13.5 触发规则

只在以下情况触发：

```text
1. 技术卡住：同一技术主题持续 > 30min，高频切换，无产出，出现错误关键词
2. 重复问题：同类错误 3 天内多次出现
3. unknown 无法归类：规则和 LLM 都低置信度
4. 工具升级 / 最佳实践：某工具大量使用但模式低效
5. 学习路径停滞：阅读时间长，实验 / 代码时间很少
```

---

### 13.6 输出形态

不要直接弹一大段搜索总结。建议进入 Agent Inbox：

```text
aw-coach inbox

[1] 可能的 ROS2 launch 安装问题
    证据：Terminal 出现 executable not found；你在 ROS2 launch 页面停留 34min
    建议：检查 install(PROGRAMS) / console_scripts / source setup.bash
    操作：[查看详情] [标记解决] [忽略] [加入知识库]

[2] 可能的 AI 查询循环
    证据：ChatGPT ↔ Chrome 循环 42min，未回到 VSCode
    建议：下一步写一个最小验证代码或笔记
    操作：[开始 25min block] [稍后提醒] [忽略]
```

---

### 13.7 Research Memory

搜索结果要沉淀，避免重复搜索：

```yaml
research_memory:
  - problem_type: ros2_launch_executable_not_found
    symptoms:
      - "executable not found"
      - "libexec directory"
      - "Python script"
    solution:
      - "check install(PROGRAMS ... DESTINATION lib/${PROJECT_NAME})"
      - "check console_scripts"
      - "source install/setup.bash"
    confidence: 0.85
    accepted_by_user: true
```

之后同类问题优先用本地记忆，不再联网。

---

### 13.8 Agent 等级定义

加入 Research Loop 后，aw-coach 的 Agent 能力分级：

| 等级 | 能力 | aw-coach 对应 |
|------|------|--------------|
| L1 | 记录行为 | ActivityWatch |
| L2 | 分类总结 | aw-coach analyzer |
| L3 | 主动发现问题 | Detector |
| L4 | 主动建议动作 | Policy + Action |
| L5 | **主动研究方案** | **Research Loop（新增）** |
| L6 | 半自治执行改进 | Inbox + Confirmed Actions |

Research Loop 是从 L4 到 L5 的关键跃迁。

---

## 14. 最终判断

aw-coach 最有价值的升级方向不是"更强的生产力报表"，而是：

```text
Local-first Personal Workflow & Research Agent
```

它应该具备：

```text
持续观察
状态建模
模式检测
主动建议
主动研究
安全执行
反馈学习
隐私保护
```

短期核心模块（按优先级）：

```text
P0: State Model
P0: Detector
P0: Policy Engine
P1: Agent Inbox + Feedback Memory
P2: Research Loop
P3: triggered OCR
P4: local VLM
```

Research Loop 必须排在 State + Detector + Policy 之后。只有先知道用户"处于什么状态、遇到什么问题"，主动搜索才有意义。

最终目标：

```text
aw-coach 不只是告诉我昨天做了什么，
而是能在合适的时候提醒我：
我现在可能卡住了，
我已经帮你查了解决方案，
我现在不该被打扰，
我现在应该回到代码，
我应该把这个 unknown 活动标记成某类，
我明天只需要改一个最关键的行为。
```

这才是真正的主动式 AI Agent。
