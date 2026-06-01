# AI 教练 CLI 协同设计文档

> 版本：v1.0  
> 日期：2026-05-30  
> 定位：阐述 CLI 作为 AI 教练系统的「人机交互枢纽」，如何与各个功能模块协同工作。

---

## 1. CLI 的定位：系统交互枢纽

CLI（`aw-coach`）不是简单的命令解析器，而是 AI 教练系统的**人机交互枢纽**。它承担以下职责：

| 职责 | 说明 |
|------|------|
| **用户意图入口** | 接收用户的查询、配置、纠正等意图 |
| **模块协调器** | 按需唤醒 DataCollector、RuleEngine、AIBackend、ReportGenerator 等模块 |
| **状态查询器** | 实时读取 aw-server 数据 + 本地分析结果，聚合后呈现 |
| **反馈收集器** | 收集用户的分类纠正，写入本地样本库 |
| **诊断工具** | 检查系统运行状态、平台兼容性、权限问题 |
| **Web 启动器** | `aw-coach open` 启动浏览器打开独立 Web 面板 |

```
                    ┌─────────────┐
                    │    User     │
                    └──────┬──────┘
                           │ 输入命令
                           ▼
              ┌────────────────────────┐
              │    aw-coach CLI        │
              │  (人机交互枢纽)         │
              └───────────┬────────────┘
                          │ 协调/查询
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
    ┌────────────┐ ┌────────────┐ ┌────────────┐
    │ Data       │ │ RuleEngine │ │ AIBackend  │
    │ Collector  │ │ (本地规则)  │ │ (LLM/API)  │
    └────────────┘ └────────────┘ └────────────┘
           │              │              │
           └──────────────┼──────────────┘
                          ▼
                 ┌────────────────┐
                 │ CostController │
                 │ (预算+降级)     │
                 └────────┬───────┘
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
    ┌────────────┐ ┌────────────┐ ┌────────────┐
    │ Report     │ │ Screenshot │ │ Correction │
    │ Generator  │ │ Capture    │ │ Store      │
    └────────────┘ └────────────┘ └────────────┘
```

---

## 2. CLI 命令与模块的映射关系

### 2.1 命令 → 模块映射总表

| CLI 命令 | 触发模块 | 操作类型 | 是否写入数据 |
|----------|----------|----------|--------------|
| `aw-coach status` | DataCollector + PatternAnalyzer | 只读查询 | ❌ |
| `aw-coach report` | ReportGenerator | 只读查询 | ❌ |
| `aw-coach report --full` | ReportGenerator + AIBackend | 可能触发 LLM | ❌（生成报告） |
| `aw-coach correct` | RuleEngine + CorrectionStore | 用户反馈写入 | ✅ 写入 corrections |
| `aw-coach cost` | CostController | 只读查询 | ❌ |
| `aw-coach doctor` | PlatformChecker + 各模块自检 | 诊断查询 | ❌ |
| `aw-coach rule list` | RuleEngine | 只读查询 | ❌ |
| `aw-coach rule suggest` | RuleEngine + CorrectionStore | 提交规则建议 | ✅ 写入待审核规则 |
| `aw-coach open` | CoachWebServer | 启动服务 | ❌ |
| `aw-coach purge` | 全模块 | 清除本地数据 | ✅ 删除文件 |

### 2.2 各命令的详细交互流程

---

#### 2.2.1 `aw-coach status` —— 实时状态查询

**触发模块**：DataCollector → PatternAnalyzer → RuleEngine（增量）

**交互流程**：

```
User: aw-coach status
  │
  ▼
aw-coach CLI
  │
  ├─► DataCollector.fetch_today_so_far()
  │     │
  │     ├─► aw-client.get_events(bucket="aw-watcher-window", start=today_00:00, end=now)
  │     ├─► aw-client.get_events(bucket="aw-watcher-afk", start=today_00:00, end=now)
  │     │
  │     ▼
  │   ActivitySlice.merge_today()
  │     │
  │     ▼
  ├─► PatternAnalyzer.calculate_focus_score(slices)
  │     │
  │     ├─► count_activity_switches()
  │     ├─► calculate_deep_work()
  │     ├─► calculate_distraction_ratio()
  │     │
  │     ▼
  ├─► RuleEngine.classify_current()
  │     │
  │     ├─► match_app_rule(current_app)
  │     ├─► match_sub_rules(title, url)
  │     │
  │     ▼
  ▼
CLI 渲染输出
  │
  ├─► 当前活动类型 + 应用名
  ├─► 今日累计各类型时长
  ├─► 专注度得分
  ├─► 深度工作时长
  ├─► AI 成本使用情况
  │
  ▼
终端输出
```

**性能特点**：
- 全部本地计算，无 LLM/API 调用
- 查询耗时 < 200ms（从 aw-server 拉取今日数据 + 本地聚合）
- 适合高频使用（用户可随时敲命令查看）

---

#### 2.2.2 `aw-coach report [date]` —— 查看日报

**触发模块**：DataCollector → PatternAnalyzer → ReportGenerator → （可选）AIBackend

**交互流程**：

```
User: aw-coach report today --full
  │
  ▼
aw-coach CLI
  │
  ├─► DataCollector.fetch_daily_data(today)
  │     │
  │     ├─► aw-client.get_events(window_bucket, today)
  │     ├─► aw-client.get_events(afk_bucket, today)
  │     ├─► aw-client.get_events(ai-coach_bucket, today)  // 已分析结果
  │     │
  │     ▼
  ├─► PatternAnalyzer.generate_summary(slices)
  │     │
  │     ├─► 各类型时间分布
  │     ├─► 精力曲线（每小时专注度）
  │     ├─► 任务切换统计
  │     │
  │     ▼
  ├─► CostController.can_use_llm(estimate)
  │     │
  │     ├─► 检查本月累计成本 < monthly_budget
  │     │
  │     ▼
  ├─► [若 --full 且预算允许] AIBackend.generate_report()
  │     │
  │     ├─► 组装 prompt（今日数据 + 历史对比）
  │     ├─► 调用 LLM API
  │     ├─► CostController.track_call()
  │     │
  │     ▼
  ├─► ReportGenerator.render_cli_report()
  │     │
  │     ├─► 若 --full: 使用 LLM 生成的建议
  │     ├─► 否则: 使用模板化建议（基于规则的 if-else）
  │     │
  │     ▼
  ▼
终端输出 Markdown 格式报告
```

**关键分支**：

```
if --full:
    if CostController.can_use_llm():
        report = AIBackend.generate_report(data)  // AI 生成，个性化建议
    else:
        report = ReportGenerator.rule_based_report(data)  // 降级为规则模板
        CLI 提示: "[INFO] AI budget reached. Using rule-based report. Run `aw-coach cost` for details."
else:
    report = ReportGenerator.rule_based_report(data)  // 快速轻量报告
```

---

#### 2.2.3 `aw-coach correct` —— 分类纠正（核心反馈闭环）

**触发模块**：CorrectionStore → （可选）RuleEngine 增量更新

**交互流程**：

```
User: aw-coach correct --last meeting
  │
  ▼
aw-coach CLI
  │
  ├─► 解析参数：--last → 最近一个未确定/错误的切片
  │     │
  │     ▼
  ├─► CorrectionStore.append_correction(
  │       timestamp=last_slice.start,
  │       original_type=last_slice.activity_type,
  │       corrected_type="meeting",
  │       user_confidence=1.0  // 用户明确纠正 = 高置信度
  │     )
  │     │
  │     ▼
  ├─► 写入 ~/.local/share/activitywatch/aw-watcher-ai-coach/corrections.jsonl
  │     │
  │     ▼
  ├─► [异步] RuleEngine.check_correction_pattern()
  │     │
  │     ├─► 统计：同一 app/title 被纠正次数 ≥ 3 次？
  │     ├─► 是 → 自动生成规则建议，写入 pending_rules.yml
  │     │
  │     ▼
  ├─► CLI 输出确认
  │     "✅ Corrected 14:32-14:47 from 'unknown' to 'meeting'"
  │     "📊 You have corrected 12 classifications this week."
  │     "💡 Consider running `aw-coach rule suggest` to improve the rule engine."
  │
  ▼
完成
```

**批量纠正**：

```bash
# 纠正指定时段
aw-coach correct --time "14:00-15:00" --type meeting

# 纠正多个切片（交互式）
aw-coach correct --interactive
# 输出：
# [1] 09:00-09:15 vscode → programming (rule, 0.92) [看起来正确? Y/n]
# [2] 09:15-09:30 chrome "Hacker News" → research (rule, 0.60) [看起来正确? Y/n/TYPE]
# User 输入: n → "请输入正确类型: " → programming
# User 输入: TYPE → 直接输入新类型
```

**纠正数据的消费路径**：

```
corrections.jsonl
    │
    ├─► 用于 AIBackend 的 few-shot prompt
    │     └─► 每次 LLM 调用时附带最近 10 条纠正记录
    │
    ├─► 用于 RuleEngine 规则自动生成
    │     └─► 高频纠正模式 → pending_rules.yml
    │
    └─► 用于社区规则库贡献
          └─► aw-coach rule suggest --from-corrections
```

---

#### 2.2.4 `aw-coach cost` —— 成本监控

**触发模块**：CostController（纯本地查询）

**交互流程**：

```
User: aw-coach cost
  │
  ▼
CostController.get_usage_summary()
  │
  ├─► 读取 ~/.local/share/activitywatch/aw-watcher-ai-coach/usage_log.jsonl
  │
  ├─► 聚合本月数据
  │     ├─► 本月累计调用次数
  │     ├─► 本月累计成本（USD）
  │     ├─► 按模型分类（gpt-4o-mini / gpt-4o / local）
  │     ├─► 按操作分类（batch_classify / generate_report / generate_suggestions）
  │
  ▼
终端输出

┌────────────────────────────────────────┐
│  💰 AI Coach - 成本使用情况            │
├────────────────────────────────────────┤
│  本月预算: $5.00                       │
│  已使用:   $1.23 (24.6%)               │
│  剩余:     $3.77                       │
│                                        │
│  调用详情:                             │
│  ──────────────────────────────────    │
│  batch_classify  12 次   $0.84         │
│  generate_report  5 次   $0.32         │
│  suggestions      8 次   $0.07         │
│                                        │
│  按模型:                               │
│  gpt-4o-mini    $1.23                  │
│                                        │
│  日均消耗: ~$0.05                      │
│  预计月末: ~$1.50 ✅ 远低于预算        │
└────────────────────────────────────────┘
```

---

#### 2.2.5 `aw-coach doctor` —— 系统诊断

**触发模块**：PlatformChecker + 各模块自检

**交互流程**：

```
User: aw-coach doctor
  │
  ▼
Doctor.run_diagnostics()
  │
  ├─► 检查 aw-server 可达性
  │     ├─► GET http://localhost:5600/api/0/info
  │     ├─► 若失败 → 🔴 "aw-server not reachable. Is ActivityWatch running?"
  │     │
  │     ▼
  ├─► 检查 bucket 存在性
  │     ├─► 检查 ai-coach bucket 是否已创建
  │     ├─► 若不存在 → 🟡 "ai-coach bucket not found. Will auto-create on first run."
  │     │
  │     ▼
  ├─► 检查 RuleEngine
  │     ├─► 加载内置规则库
  │     ├─► 统计规则数量（如 "142 rules loaded"）
  │     ├─► 检查是否有更新可用
  │     │
  │     ▼
  ├─► 检查 AIBackend
  │     ├─► 当前 backend 类型（rule_only / hybrid / openai / local）
  │     ├─► 若 openai → 检查 API Key 是否有效（轻量测试调用）
  │     ├─► 若 local → 检查 Ollama 是否运行
  │     │
  │     ▼
  ├─► 检查 CostController
  │     ├─► 读取本月成本
  │     ├─► 提示预算状态
  │     │
  │     ▼
  ├─► 检查 Screenshot 平台兼容性
  │     ├─► Linux: 检测 X11 / Wayland
  │     │       Wayland → 检查 xdg-desktop-portal
  │     ├─► macOS: 检查 Screen Recording 权限
  │     ├─► Windows: 检查 Win32 API 可用性
  │     │
  │     ▼
  ├─► 检查 CorrectionStore
  │     ├─► 统计累计纠正数量
  │     ├─► 提示是否足够生成规则建议
  │     │
  │     ▼
  ▼
终端输出诊断报告
```

**输出示例**：

```
🩺 AI Coach - System Diagnostics
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ aw-server        reachable at localhost:5600
✅ buckets          window, afk, web buckets found
✅ ai-coach bucket  exists (12 events today)
✅ rule engine      142 rules loaded
   └─ last updated: 2026-05-28
✅ ai backend       hybrid (gpt-4o-mini)
✅ api key          valid (test call succeeded)
✅ cost controller  $1.23 / $5.00 (24.6%)
⚠️  screenshot       Wayland detected
   └─ xdg-desktop-portal found, but no active session
   └─ Screenshot fallback will request portal on demand
✅ corrections      23 corrections stored
   └─ 5 pending rule suggestions available
   └─ Run `aw-coach rule suggest` to review

Overall Status: 🟢 Healthy
```

---

#### 2.2.6 `aw-coach rule suggest` —— 社区规则贡献

**触发模块**：RuleEngine + CorrectionStore

**交互流程**：

```
User: aw-coach rule suggest --from-corrections
  │
  ▼
RuleEngine.generate_rule_suggestions()
  │
  ├─► 读取 corrections.jsonl
  ├─► 聚类高频纠正模式
  │     ├─► 例: 5 次将 app="cursor", title="*.rs" 从 unknown → programming
  │     ├─► 置信度 = 纠正次数 / 总出现次数
  │
  ├─► 生成规则草案
  │     │
  │     ▼
  ├─► 交互式确认
  │     "Suggested rule for 'cursor':"
  │     "  match_apps: [\"cursor\"]"
  │     "  default_type: programming"
  │     "  confidence: 0.90"
  │     "  source: 5 user corrections"
  │     "Accept? (Y/n/e[dit])"
  │     │
  │     ▼
  ├─► 用户确认后写入
  │     ~/.local/share/activitywatch/aw-watcher-ai-coach/rules/custom-rules.yml
  │
  ├─► RuleEngine.hot_reload()
  │
  ▼
CLI 输出: "✅ Rule added. Rule engine reloaded."
```

**手动提交规则**：

```bash
aw-coach rule suggest --app "Cursor" --type programming \
  --confidence 0.90 --keywords "*.rs,*.py,*.js"
```

---

#### 2.2.7 `aw-coach open` —— 启动 Web 面板

**触发模块**：CoachWebServer

**交互流程**：

```
User: aw-coach open
  │
  ▼
CLI 检查 CoachWebServer 是否已运行
  │
  ├─► 若未运行 → 启动内置 HTTP 服务器（localhost:5601）
  │     ├─► 读取今日分析数据
  │     ├─► 渲染 dashboard.html
  │     │
  │     ▼
  ├─► 调用系统默认浏览器打开 http://localhost:5601
  │     │
  │     ├─► Linux: xdg-open
  │     ├─► macOS: open
  │     ├─► Windows: start
  │     │
  │     ▼
  ▼
CLI 输出: "🌐 Opening AI Coach dashboard at http://localhost:5601"
```

---

#### 2.2.8 `aw-coach purge` —— 数据清除

**交互流程**：

```
User: aw-coach purge
  │
  ▼
CLI 确认: "This will delete all local screenshots, corrections, and reports. Continue? (y/N)"
  │
  ├─► 用户确认
  │     │
  │     ▼
  ├─► 删除 ~/.local/share/activitywatch/aw-watcher-ai-coach/screenshots/
  ├─► 删除 ~/.local/share/activitywatch/aw-watcher-ai-coach/corrections.jsonl
  ├─► 删除 ~/.local/share/activitywatch/aw-watcher-ai-coach/reports/
  ├─► 保留 ~/.config/activitywatch/aw-watcher-ai-coach.toml
  │
  ▼
CLI 输出: "✅ Local data purged. Configuration preserved."
```

---

## 3. CLI 与各模块的深度协同模式

### 3.1 CLI 与 AIBackend：按需唤醒、预算守门

CLI 不直接调用 AIBackend，而是通过 CostController 守门：

```
User: aw-coach report --full
  │
  ▼
aw-coach CLI
  │
  ├─► CostController.can_use_llm(estimated_cost=0.05)
  │     │
  │     ├─► 若允许 → 调用 AIBackend.generate_report()
  │     │            CostController.track_call()
  │     │
  │     ├─► 若不允许 → 返回降级提示
  │     │              "AI budget reached. Using rule-based report."
  │     │              "Run `aw-coach cost` for details."
  │     │
  │     ▼
  ▼
```

### 3.2 CLI 与 Screenshot：按需触发、用户授权

```
后台分析流程（定时触发，非 CLI 直接调用）：

Hourly Batch Analyzer
  │
  ├─► 对未确定切片检查是否需要截图
  │     │
  │     ├─► 若需要 → 系统通知请求授权
  │     │            "AI Coach needs a screenshot to classify 'Unknown App'. Allow?"
  │     │            [Allow Once] [Always Allow] [Deny]
  │     │
  │     ├─► 若用户通过 CLI 配置了 auto_allow_screenshot = true
  │     │            则跳过通知直接截图
  │     │
  │     ▼
  ▼

CLI 相关配置命令（未来扩展）：
aw-coach config screenshot.auto_allow true   # 高级用户可选
aw-coach config screenshot.blocklist add "BankApp"
```

### 3.3 CLI 与 ReportGenerator：多通道消费

CLI 是报告的首要消费通道，同时触发其他消费通道：

```
ReportGenerator.generate_daily_report()
  │
  ├─► 1. CLI 渲染（aw-coach report）
  │     └─► 终端输出 Markdown
  │
  ├─► 2. 系统通知（通过 plyer）
  │     └─► 摘要版（今日工作时长 + Top 建议）
  │
  ├─► 3. 本地文件写入
  │     └─► ~/.local/share/activitywatch/reports/daily/YYYY-MM-DD.md
  │
  ├─► 4. aw-server Event 写入
  │     └─► bucket: ai-coach_{hostname}
  │
  └─► 5. Web 页面更新
        └─► CoachWebServer 重新渲染 dashboard
```

### 3.4 CLI 与 RuleEngine：实时反馈闭环

CLI 是规则引擎最主要的用户反馈入口，形成「分析 → 纠错 → 规则增强 → 分析更准确」的正循环：

```
        ┌─────────────────┐
        │  RuleEngine     │
        │  (142 rules)    │
        └────────┬────────┘
                 │ classify
                 ▼
        ┌─────────────────┐
        │  User observes  │
        │  classification │
        │  in CLI/Web     │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │  aw-coach       │
        │  correct        │
        └────────┬────────┘
                 │ write correction
                 ▼
        ┌─────────────────┐
        │  CorrectionStore│
        │  (corrections   │
        │   .jsonl)       │
        └────────┬────────┘
                 │
                 ├─► 用于 AIBackend few-shot
                 │
                 └─► 积累到阈值
                     │
                     ▼
              ┌──────────────┐
              │ aw-coach     │
              │ rule suggest │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │ RuleEngine   │
              │ hot reload   │
              └──────────────┘
```

---

## 4. CLI 状态机设计

CLI 本身是无状态的，但用户与 CLI 的交互存在隐式状态机：

```
[初始状态]
   │
   │ aw-coach doctor
   ▼
[诊断通过] ──► [日常使用循环]
   │              │
   │              ├─► aw-coach status ──► 查看实时状态 ──► [日常使用循环]
   │              │
   │              ├─► aw-coach report ──► 查看报告 ──► [日常使用循环]
   │              │
   │              ├─► aw-coach correct ──► 纠正分类 ──► [日常使用循环]
   │              │                    │
   │              │                    └─► [规则增强触发条件]
   │              │                          │
   │              │                          ▼
   │              │                    [规则引擎更新]
   │              │                          │
   │              │                          ▼
   │              │                    [日常使用循环]
   │              │
   │              ├─► aw-coach cost ──► 监控成本 ──► [日常使用循环]
   │              │
   │              ├─► aw-coach open ──► Web 面板 ──► [日常使用循环]
   │              │
   │              └─► aw-coach purge ──► 清除数据 ──► [初始状态]
   │
   │ aw-coach doctor 诊断失败
   ▼
[问题修复]
   │
   │ 用户按提示修复后
   ▼
[初始状态]
```

---

## 5. CLI 的日志与调试

### 5.1 日志级别控制

```bash
# 默认级别：INFO
aw-coach status

# 调试模式：DEBUG（显示模块调用细节）
LOG_LEVEL=debug aw-coach status
# 输出:
# [DEBUG] DataCollector.fetch_today_so_far() → 247 events from window bucket
# [DEBUG] PatternAnalyzer.calculate_focus_score() → switches=12, deep_work=135min, score=72
# [DEBUG] RuleEngine.classify_current() → app=vscode, rule_match=programming, confidence=0.90

# 静默模式：WARNING 以上
LOG_LEVEL=warning aw-coach status
```

### 5.2 调试命令

```bash
# 模拟特定日期数据（测试用）
aw-coach report 2026-05-25 --mock

# 测试 LLM prompt（不实际调用 API，只输出 prompt）
aw-coach report --full --dry-run

# 测试规则匹配
aw-coach rule test --app "vscode" --title "main.rs - myproject"
# 输出:
# app: vscode
# matched_rule: vscode-programming
# activity_type: programming
# confidence: 0.90
# reasoning: exact app match
```

---

## 6. CLI 与后台服务的协同

`aw-watcher-ai-coach` 是一个**常驻后台服务**（被 aw-qt 自动管理），`aw-coach` 是一个**按需调用的 CLI 工具**。它们通过以下机制协同：

### 6.1 数据共享机制

| 数据 | 存储位置 | 后台服务 | CLI |
|------|----------|----------|-----|
| 分析结果 Events | aw-server bucket | ✅ 写入 | ✅ 读取 |
| 规则库 | `~/.local/share/activitywatch/aw-watcher-ai-coach/rules/` | ✅ 加载 | ✅ 读取/写入 |
| 纠正记录 | `corrections.jsonl` | ✅ 读取（few-shot） | ✅ 写入 |
| 成本日志 | `usage_log.jsonl` | ✅ 写入 | ✅ 读取 |
| 本地报告 | `reports/daily/*.md` | ✅ 写入 | ✅ 读取 |
| 截图 | `screenshots/` | ✅ 写入/删除 | ❌ 不直接访问 |

### 6.2 进程间通信

CLI 与后台服务**不直接通信**，而是通过以下方式协同：

```
aw-watcher-ai-coach (后台常驻)
  │
  ├─► 定时从 aw-server 读取原始数据
  ├─► 分析后写回 aw-server (ai-coach bucket)
  ├─► 生成报告写入本地文件
  └─► 推送系统通知

aw-coach CLI (按需调用)
  │
  ├─► 从 aw-server 读取分析结果（通过 REST API）
  ├─► 读取本地文件（报告、成本日志、纠正记录）
  ├─► 修改规则库（热重载，后台自动感知）
  └─► 触发系统通知（独立调用）
```

**设计意图**：CLI 与后台服务解耦，CLI 可随时运行，不依赖后台是否在线。后台服务离线时，CLI 仍可读取历史分析结果和本地文件；后台服务在线时，CLI 可看到最新实时数据。

---

## 7. 总结：CLI 的核心价值

| 价值点 | 说明 |
|--------|------|
| **零延迟反馈** | 用户发现分类错误 → `aw-coach correct` → 立即纠正，无需打开浏览器 |
| **成本透明** | `aw-coach cost` 随时查看 AI 开销，用户对成本有完全掌控感 |
| **问题自愈** | `aw-coach doctor` 一键诊断，用户可按提示自助修复常见问题 |
| **规则共建** | `aw-coach rule suggest` 让用户参与规则库建设，从消费者变为贡献者 |
| **渐进增强** | 先用 CLI 验证功能价值，再投入 Web UI 开发，降低前期投入风险 |
| **跨平台一致** | CLI 在 Linux/macOS/Windows/WSL 上体验一致，无前端兼容性问题 |

CLI 是 AI 教练系统的**第一站**（用户首次体验）和**最高频入口**（日常使用），其设计质量直接决定用户留存率。
