# ActivityWatch AI 插件竞品分析报告

> 日期：2026-05-30  
> 范围：GitHub 开源生态中基于 ActivityWatch 的 AI/分析/生产力扩展项目

---

## 1. 调研范围

| 项目 | Stars | 定位 | 相关性 |
|------|-------|------|--------|
| [activitywatch-analysis-skill](https://github.com/BayramAnnakov/activitywatch-analysis-skill) | ~100 | Claude Code Skill，生产力分析 | 直接竞品 |
| aw-watcher-enhanced | ~50 | 增强型 watcher（OCR + LLM） | 技术路线竞品 |
| [aw-llm-worker](https://github.com/Srakai/aw-llm-worker) | 5 | 30min 聚合 + 本地视觉模型标注截图 | 技术参考 |
| [aw-export-timewarrior](https://github.com/tobixen/aw-export-timewarrior) | 0 | AW 事件导出到 TimeWarrior | 架构参考 |
| [aw-research](https://github.com/ActivityWatch/aw-research) | — | 官方分析实验工具集 | 参考 |
| 官方 Categorization (aw-webui) | — | UI 正则分类 | 功能重叠 |

---

## 2. 核心竞品深度分析

### 2.1 activitywatch-analysis-skill（直接竞品，~100 stars）

**形态**：Claude Code Skill（不是独立 daemon，必须在 Claude 内使用）  
**作者**：BayramAnnakov  
**技术**：纯 Python 脚本，手动运行，无 LLM 调用（靠 Claude Code 系统 prompt 生成报告）

核心能力：

| 功能 | 实现方式 | 与我们的对比 |
|------|----------|-------------|
| Calibration 模式 | 首次运行扫描未分类 app，引导用户配置 | 我们没有 |
| 80+ App 默认规则 | JSON config，weight 评分体系 | 我们有 42 条规则，数量少一半 |
| AI Agent 检测 | 识别 Claude Code / Copilot / Aider 为 productive | 我们没有 |
| 双评分系统 | Productivity Score（做什么）+ Focus Score（怎么做） | 我们只有 focus_score |
| Death Loop 检测 | 识别 A↔B 重复切换模式 | 我们没有 |
| 深度浏览器分析 | 依赖 aw-watcher-web，site-level 分类 | 我们只有 title/url 子规则 |
| Telegram 聊天分类 | 工作群 vs 私聊分离 | 我们没有 |
| Weight 评分 | 1.0=deep work, 0.0=neutral, -0.5=distracting | 我们是 binary 分类 |
| Focus Guard | 应用拦截脚本（Cold Turkey 等集成） | 我们没有 |
| Weekly Review 仪式 | 每周日 15min 回顾 + "One Change" | 我们有周报但无仪式设计 |

**局限**：
- 不能独立运行，依附 Claude Code 生态
- 无 daemon 模式，无自动通知
- 无规则自进化机制
- 无成本控制

---

### 2.2 aw-watcher-enhanced（技术路线竞品，~50 stars）

**形态**：替换 aw-watcher-window 的增强 watcher  
**作者**：kepptic  
**技术**：Rust/Python 混合，直接替换官方 watcher

核心能力：

| 功能 | 实现方式 | 与我们的对比 |
|------|----------|-------------|
| Accessibility API | macOS AXFocusedUIElement，读取 terminal tab / editor pane | 我们只有 app + title |
| 浏览器 URL 合并 | 自动合并 aw-watcher-web 数据到 window 事件 | 我们依赖手动子规则 |
| 会议检测 | Zoom/Teams/Meet/Discord 自动识别 | 我们没有专门的会议检测逻辑 |
| 自适应 OCR | 屏幕内容变化时触发 OCR | 我们没有 |
| 本地 LLM 提取 | Ollama（gemma3:4b）提取文档名、项目信息 | 我们用 DeepSeek API |
| 150+ 分类规则 | 层级分类（Work/Development/Coding） | 我们只有 42 条扁平规则 |
| 上下文切换指标 | focus_duration + switches_last_hour | 我们有 switch_count 但无速率指标 |
| 活动水平追踪 | 鼠标/键盘滚动 5min 窗口活动百分比 | 我们没有（依赖 AFK） |
| 隐私控制 | PII 自动脱敏、密码管理器排除 | 我们只有敏感 app 规则 |
| 回溯重分类 | --reclassify 重跑历史数据 | 我们没有 |

**局限**：
- 仅 macOS（Accessibility API 依赖）
- 无报告/通知/仪表盘
- 无成本控制
- 无反馈闭环

---

### 2.3 aw-llm-worker（技术参考，5 stars）

**形态**：独立 worker 进程  
**技术**：Python + llama-cpp-python，Apple Silicon Metal 加速

核心能力：

| 功能 | 实现方式 |
|------|----------|
| 30min 窗口聚合 | 将原始事件聚合为 30min block |
| 本地视觉模型 | 截图分析 + 标注 |
| Keyword topic kernels | 文本卷积做主题分类 |
| 写回 bucket | `aw-llm-blocks_{hostname}` |

**局限**：仅 Apple Silicon、无 UI、无报告、无通知。

---

### 2.4 ActivityWatch 官方 Categorization

**位置**：Web UI → Settings → Categorization

- Regex 规则匹配 app + title（不支持 URL）
- 树形分类（父/子类别）
- 可视化饼图/时间线

**差距**：
- 规则只能在 Web UI 手动编辑，无 CLI/文件管理
- 无 AI 辅助分类
- 无通知/报告功能
- 无评分系统

---

## 3. 能力对比矩阵

```
                          analysis-skill    enhanced    aw-llm    官方UI    aw-coach(我们)
─────────────────────────────────────────────────────────────────────────────────────────────
Daemon 自动运行              ❌              ✅          ✅         ❌           ✅
systemd 集成                 ❌              ❌          ❌         ❌           ✅
通知推送                     ❌              ❌          ❌         ❌           ✅
Hybrid LLM 分类              ❌              ⚠️(本地)    ✅(本地)   ❌           ✅
成本控制                     ❌              ❌          ❌         ❌           ✅
规则自进化(corrections)      ❌              ❌          ❌         ❌           ✅
HTML 仪表盘                  ❌              ❌          ❌         ✅           ✅
CLI 命令                     ⚠️(脚本)        ✅          ❌         ❌           ✅
中文应用生态                 ❌              ❌          ❌         ❌           ✅
Heartbeat 合并               ❌              ❌          ✅         ❌           ✅
─────────────────────────────────────────────────────────────────────────────────────────────
Calibration 首次引导         ✅              ❌          ❌         ❌           ❌
AI Agent 检测                ✅              ❌          ❌         ❌           ❌
Death Loop 检测              ✅              ❌          ❌         ❌           ❌
Weight 评分体系              ✅              ✅          ❌         ❌           ❌
浏览器 site-level 分析       ✅              ✅          ❌         ❌           ❌
Focus Guard 拦截             ✅              ❌          ❌         ❌           ❌
Accessibility API            ❌              ✅          ❌         ❌           ❌
OCR 屏幕内容                 ❌              ✅          ✅         ❌           ❌
活动水平追踪                 ❌              ✅          ❌         ❌           ❌
回溯重分类                   ❌              ✅          ❌         ❌           ❌
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 4. 我们的核心优势

### 独有的差异化能力（竞品均无）

| 能力 | 价值 |
|------|------|
| **Hybrid 架构（规则 + DeepSeek）** | 规则覆盖 80% 场景，LLM 只处理 20% 不确定的，月成本 <$5 |
| **月度预算熔断** | 用户可控成本，超支自动降级到 rule_only |
| **规则自进化闭环** | correct → corrections → rule-suggest → user.yml，越用越准 |
| **系统级部署** | systemd + autostart + SIGTERM flush，开箱即用，重启不丢数据 |
| **中文应用生态** | 飞书、钉钉、微信、企业微信、WPS、腾讯会议等原生支持 |
| **Heartbeat 合并 + 切换去抖动** | 解决了 AW 原始数据碎片化问题，其他项目均未处理 |

### 产品完整度对比

| 维度 | analysis-skill | enhanced | aw-coach |
|------|---------------|----------|----------|
| 安装后可用（无需配置） | 需要 Claude Code | 需替换 watcher | **零配置即用** |
| 持续运行（用户无感） | 手动触发 | 自动 | **systemd 自启** |
| 主动推送信息 | 无 | 无 | **每 2h + 日报通知** |
| 用户反馈闭环 | 无 | 无 | **correct → rule-suggest** |
| 成本透明 | N/A | N/A | **aw-coach cost** |

---

## 5. 我们的明显差距

### 高价值缺失功能（建议优先补齐）

| 差距 | 来源 | 影响 | 实现难度 |
|------|------|------|----------|
| **Calibration 首次引导** | analysis-skill | 新用户看到大量 unknown，体验差 | 低 (3h) |
| **Weight 评分体系** | analysis-skill + enhanced | binary 分类太粗，无法量化"部分有效" | 低 (4h) |
| **Death Loop 检测** | analysis-skill | 无法识别 Slack↔IDE↔Slack 的注意力碎片化模式 | 低 (2h) |
| **AI Agent 检测** | analysis-skill | 用 Cursor/Copilot 时被误判为频繁切换 | 低 (2h) |
| **浏览器 site-level 分析** | analysis-skill + enhanced | Chrome 时间只能到 app 级别 | 中 (3h) |

### 技术深度差距（长期追赶）

| 差距 | 竞品实现 | 补齐难度 |
|------|----------|----------|
| Accessibility API | enhanced 读取 terminal tab / editor pane | 高（Linux AT-SPI 不成熟） |
| OCR 屏幕内容 | enhanced 用 OCR 提取 remote desktop 内容 | 中（依赖 easyocr） |
| 活动水平追踪 | enhanced 追踪鼠标/键盘 5min 滚动活动率 | 中（需新 watcher） |
| Focus Guard 拦截 | analysis-skill 集成 Cold Turkey | 高（跨平台实现复杂） |

---

## 6. 改进路线图建议

### 阶段四：补齐核心差距（1-2 周，~14h）

```
1. Calibration 模式 (3h)
   └─ `aw-coach doctor --calibrate`
      └─ 扫描今日未知 app → 展示给用户 → 一键写入 user.yml

2. Weight 评分体系 (4h)
   └─ 规则增加 weight 字段（1.0 deep work → -0.5 distracting）
   └─ 新增 productivity_score = weighted sum
   └─ 报告展示双评分（productivity + focus）

3. Death Loop 检测 (2h)
   └─ analyzer.py 新增 _detect_death_loops()
   └─ 识别 A↔B 重复切换 ≥3 次 → 报告 + 建议

4. AI Agent 检测 (2h)
   └─ 规则增加：窗口标题含 "✳ Claude" / "codex" / "aider" → ai_assisted
   └─ ai_assisted 模式下 IDE↔Browser 切换不扣 focus 分

5. aw-watcher-web 集成 (3h)
   └─ 优先读取 aw-watcher-web bucket 的 URL 数据
   └─ 浏览器事件合并 site-level 信息后再分类
```

### 阶段五：数据深度增强（2-4 周）

```
6. 回溯重分类 (4h)
   └─ `aw-coach reclassify --from 2026-05-01`
   └─ 用最新规则库重新分析历史数据

7. Focus Guard 初版 (8h)
   └─ 基于 notify-send 的"专注模式"提醒
   └─ 集成 xdg-desktop-portal 的 Do Not Disturb

8. Accessibility API 调研 (探索性)
   └─ Linux AT-SPI / D-Bus 获取 terminal tab / editor pane
   └─ 作为可选 watcher 增强

9. 活动水平追踪 (8h)
   └─ 独立轻量 watcher 追踪鼠标/键盘活动率
   └─ 5min 滚动窗口输出 activity_level 指标
```

---

## 7. 结论

ActivityWatch 的 AI 扩展生态目前非常早期（最高 ~100 stars），没有成熟的商业级产品。

**我们的定位**：
- 不是"技术最深"的（enhanced 在数据采集层更强）
- 不是"分析模型最精细"的（analysis-skill 的双评分 + death loop 更有洞察力）
- 但我们是**唯一一个"产品化完整"的**：零配置 → 自动运行 → 智能分析 → 主动通知 → 反馈闭环

**下一步核心策略**：保持产品化优势，快速补齐 analysis-skill 的分析模型差距（Calibration + Weight + Death Loop + AI Agent），这些都是低投入高回报的功能。技术深度差距（Accessibility API / OCR）可以延后——它们对普通用户的可感知价值远低于"更聪明的分析 + 更好的首次体验"。
