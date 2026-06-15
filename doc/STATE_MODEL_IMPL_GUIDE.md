# State Model 实现指南：如何让 aw-coach "看懂"用户状态

> 对应 Phase 1 核心任务
> 目标：实现 `UserWorkState`，让系统每分钟知道用户正在做什么、处于什么模式

---

## 一、总体思路

"看懂工作状态"不是魔法，而是**三个问题的答案**：

```text
1. 用户现在在做什么？    -> current_app + current_activity + likely_mode
2. 用户做了多久了？      -> active_block_duration_sec
3. 用户现在状态好不好？   -> risk_level (专注? 碎片化? 卡住了? 分心了?)
```

实现路径：

```
每分钟从 aw-server 拉取最近 5 分钟数据
        ↓
找出当前活动切片（最新的非 AFK 事件）
        ↓
RuleEngine 分类当前活动
        ↓
计算滚动窗口统计（5min/30min/1h 切换次数）
        ↓
计算活跃块（当前连续工作状态的起止）
        ↓
推断 likely_mode 和 risk_level
        ↓
持久化到 SQLite + 供 Detector 消费
```

---

## 二、数据结构定义

### 2.1 UserWorkState

```python
# src/aw_coach/state.py

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple


@dataclass
class UserWorkState:
    """用户实时工作状态快照。每分钟更新一次。"""

    # === 元信息 ===
    updated_at: datetime

    # === 当前活动 ===
    current_app: str
    current_title: str
    current_url: Optional[str]
    current_domain: Optional[str]  # 从 URL 提取，浏览器分析用
    current_activity: str          # RuleEngine 输出的类型
    current_confidence: float      # RuleEngine 输出的置信度
    current_method: str            # rule_app_exact / rule_sub / llm_batch 等

    # === 当前活跃块（连续同类型工作）===
    active_block_start: datetime   # 这个工作块什么时候开始
    active_block_category: str     # 这个块的活动类型
    active_block_duration_sec: int # 已持续多少秒

    # === 滚动窗口切换统计 ===
    switches_last_5min: int
    switches_last_30min: int
    switches_last_hour: int

    # === 滚动得分（0-100）===
    focus_score_rolling: float      # 最近30分钟专注度
    productivity_score_rolling: float  # 最近30分钟生产力

    # === 推断状态 ===
    likely_mode: str   # coding / debugging / reading / meeting / chatting / browsing / idle / unknown
    risk_level: str    # normal / focused / fragmented / stuck / distracted / unknown

    # === 交互历史（用于 Policy Engine）===
    last_user_feedback_at: Optional[datetime]
    last_agent_notification_at: Optional[datetime]
    notifications_today: int

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的 dict。"""
        d = asdict(self)
        # datetime 转 ISO 字符串
        for key in ["updated_at", "active_block_start", "last_user_feedback_at", "last_agent_notification_at"]:
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        return d

    @classmethod
    def empty(cls) -> "UserWorkState":
        """初始化时的空状态。"""
        now = datetime.now(timezone.utc).astimezone()
        return cls(
            updated_at=now,
            current_app="unknown",
            current_title="",
            current_url=None,
            current_domain=None,
            current_activity="unknown",
            current_confidence=0.0,
            current_method="init",
            active_block_start=now,
            active_block_category="unknown",
            active_block_duration_sec=0,
            switches_last_5min=0,
            switches_last_30min=0,
            switches_last_hour=0,
            focus_score_rolling=0.0,
            productivity_score_rolling=0.0,
            likely_mode="idle",
            risk_level="unknown",
            last_user_feedback_at=None,
            last_agent_notification_at=None,
            notifications_today=0,
        )
```

---

## 三、核心算法：StateUpdater

### 3.1 入口方法

```python
# src/aw_coach/state.py

from collections import defaultdict
from typing import List

from aw_coach.collector import ActivitySlice, DataCollector
from aw_coach.rules.engine import RuleEngine, RuleResult


class StateUpdater:
    """每分钟调用一次，从原始事件计算出 UserWorkState。"""

    # 活跃块的 AFK 容忍时间：短于此时间的 AFK 不中断活跃块
    AFK_GAP_TOLERANCE_SEC = 120

    # 切换去抖动：短于此时间的不算一次有效切换
    SWITCH_DEBOUNCE_SEC = 30

    def __init__(self, collector: DataCollector, rule_engine: RuleEngine):
        self.collector = collector
        self.rules = rule_engine
        self._last_state: Optional[UserWorkState] = None

    def update(self, now: Optional[datetime] = None) -> UserWorkState:
        """主入口：拉取最近数据，计算新状态。"""
        if now is None:
            now = datetime.now()

        # 1. 拉取最近 70 分钟的数据（1小时窗口 + 10分钟缓冲）
        start = now - timedelta(minutes=70)
        slices = self.collector.fetch_range(start, now)

        if not slices:
            # 没有数据，返回 idle 状态
            return self._build_idle_state(now)

        # 2. 分类所有切片
        classified = self._classify_slices(slices)

        # 3. 找出"当前"活动（最新的非 AFK 事件）
        current_slice, current_rule = self._find_current_activity(slices, classified, now)

        # 4. 计算滚动窗口切换次数
        switches_5m, switches_30m, switches_1h = self._count_switches_in_windows(
            slices, classified, now
        )

        # 5. 计算活跃块
        block_start, block_cat, block_dur = self._compute_active_block(
            slices, classified, now
        )

        # 6. 计算滚动得分
        focus_rolling, prod_rolling = self._compute_rolling_scores(
            slices, classified, now
        )

        # 7. 推断 likely_mode 和 risk_level
        likely_mode = self._infer_likely_mode(
            current_slice, current_rule, block_dur, switches_5m
        )
        risk_level = self._assess_risk(
            current_slice, current_rule, block_dur, switches_5m, switches_30m,
            focus_rolling, likely_mode
        )

        # 8. 组装状态
        state = UserWorkState(
            updated_at=now,
            current_app=current_slice.primary_app if current_slice else "unknown",
            current_title=current_slice.primary_title if current_slice else "",
            current_url=getattr(current_slice, "web_url", None),
            current_domain=self._extract_domain(getattr(current_slice, "web_url", None)),
            current_activity=current_rule.activity_type if current_rule else "unknown",
            current_confidence=current_rule.confidence if current_rule else 0.0,
            current_method=current_rule.method if current_rule else "none",
            active_block_start=block_start,
            active_block_category=block_cat,
            active_block_duration_sec=block_dur,
            switches_last_5min=switches_5m,
            switches_last_30min=switches_30m,
            switches_last_hour=switches_1h,
            focus_score_rolling=focus_rolling,
            productivity_score_rolling=prod_rolling,
            likely_mode=likely_mode,
            risk_level=risk_level,
            # 交互历史从上一个状态继承
            last_user_feedback_at=self._last_state.last_user_feedback_at if self._last_state else None,
            last_agent_notification_at=self._last_state.last_agent_notification_at if self._last_state else None,
            notifications_today=self._last_state.notifications_today if self._last_state else 0,
        )

        self._last_state = state
        return state

    # --- 以下为内部辅助方法 ---

    def _classify_slices(self, slices: List[ActivitySlice]) -> List[RuleResult]:
        """对所有切片进行分类。"""
        return [
            self.rules.classify(s.primary_app, s.primary_title, s.web_url)
            for s in slices
        ]

    def _find_current_activity(
        self, slices: List[ActivitySlice], rules: List[RuleResult], now: datetime
    ) -> Tuple[Optional[ActivitySlice], Optional[RuleResult]]:
        """找出当前活动：最新的非 AFK 事件。"""
        # 倒序遍历，找最近一个非 AFK 且距离现在不超过 2 分钟的事件
        for s, r in zip(reversed(slices), reversed(rules)):
            if s.is_afk:
                continue
            if (now - s.end).total_seconds() <= 120:
                return s, r
        # fallback：最新的非 AFK 事件（不管多久以前）
        for s, r in zip(reversed(slices), reversed(rules)):
            if not s.is_afk:
                return s, r
        return None, None

    def _count_switches_in_windows(
        self, slices: List[ActivitySlice], rules: List[RuleResult], now: datetime
    ) -> Tuple[int, int, int]:
        """计算 5min/30min/1h 窗口内的有效切换次数。"""

        def count_in_window(minutes: int) -> int:
            cutoff = now - timedelta(minutes=minutes)
            # 筛选窗口内的非 AFK 切片
            window_items = [
                (s, r) for s, r in zip(slices, rules)
                if not s.is_afk and s.end > cutoff
            ]
            if len(window_items) < 2:
                return 0

            # 构建连续段（合并同类型，去抖动）
            segments: List[Tuple[str, float]] = []  # (activity_type, total_duration_sec)
            for s, r in window_items:
                atype = r.activity_type
                if segments and segments[-1][0] == atype:
                    segments[-1] = (atype, segments[-1][1] + s.duration)
                else:
                    segments.append((atype, s.duration))

            # 去抖动：跳过持续时间过短的段
            filtered = []
            for atype, dur in segments:
                if dur < self.SWITCH_DEBOUNCE_SEC:
                    continue
                if filtered and filtered[-1] == atype:
                    continue
                filtered.append(atype)

            return max(0, len(filtered) - 1)

        return count_in_window(5), count_in_window(30), count_in_window(60)

    def _compute_active_block(
        self, slices: List[ActivitySlice], rules: List[RuleResult], now: datetime
    ) -> Tuple[datetime, str, int]:
        """
        计算当前活跃块：从 now 往回追溯，找到连续同类型工作的起始点。
        允许短 AFK (<=120s) 不中断块。
        """
        if not slices:
            return now, "unknown", 0

        # 找到当前活动类型（最新的非 AFK）
        current_type = None
        for s, r in zip(reversed(slices), reversed(rules)):
            if not s.is_afk:
                current_type = r.activity_type
                break

        if current_type is None:
            return now, "idle", 0

        # 往回追溯块的起点
        block_start = None
        last_valid_end = now

        for s, r in zip(reversed(slices), reversed(rules)):
            if s.is_afk:
                if s.duration <= self.AFK_GAP_TOLERANCE_SEC:
                    # 短 AFK，不中断，但更新时间边界
                    last_valid_end = s.start
                    continue
                else:
                    # 长 AFK，块在此中断
                    break

            if r.activity_type == current_type:
                block_start = s.start
                last_valid_end = s.end
            else:
                # 活动类型变了，块结束
                break

        if block_start is None:
            block_start = now

        duration = int((last_valid_end - block_start).total_seconds())
        return block_start, current_type, max(0, duration)

    def _compute_rolling_scores(
        self, slices: List[ActivitySlice], rules: List[RuleResult], now: datetime
    ) -> Tuple[float, float]:
        """计算最近 30 分钟的滚动 focus_score 和 productivity_score。"""
        cutoff = now - timedelta(minutes=30)
        window = [
            (s, r) for s, r in zip(slices, rules)
            if not s.is_afk and s.end > cutoff
        ]

        if not window:
            return 0.0, 0.0

        # Focus score 简化版：基于 deep_work 比例、切换频率、娱乐比例
        total_duration = sum(s.duration for s, _ in window)
        if total_duration == 0:
            return 0.0, 0.0

        # Deep work 比例
        from aw_coach.analyzer import DEEP_WORK_TYPES
        deep_sec = sum(s.duration for s, r in window if r.activity_type in DEEP_WORK_TYPES)
        deep_ratio = deep_sec / total_duration

        # 娱乐比例
        distraction_sec = sum(
            s.duration for s, r in window
            if r.activity_type in ("entertainment", "social")
        )
        distraction_ratio = distraction_sec / total_duration

        # 30 分钟内切换次数（基于去抖动后的段）
        segments = []
        for s, r in window:
            if segments and segments[-1] == r.activity_type:
                continue
            segments.append(r.activity_type)
        switches = max(0, len(segments) - 1)

        # Focus score: 60 基础 + deep 奖励 - 切换惩罚 - 娱乐惩罚
        score = 60.0
        score += min(deep_ratio * 30, 30)           # deep work 奖励，上限 30
        score -= min(switches * 3, 30)              # 切换惩罚，上限 30
        score -= distraction_ratio * 40             # 娱乐惩罚
        focus_score = max(0.0, min(100.0, score))

        # Productivity score: 基于 weight 的加权平均
        from aw_coach.rules.engine import DEFAULT_WEIGHTS
        weighted = sum(
            DEFAULT_WEIGHTS.get(r.activity_type, 0.0) * s.duration
            for s, r in window
        )
        raw = weighted / total_duration  # -0.5 ~ 1.0
        prod_score = max(0.0, min(100.0, (raw + 0.5) / 1.5 * 100))

        return round(focus_score, 1), round(prod_score, 1)

    def _infer_likely_mode(
        self,
        current_slice: Optional[ActivitySlice],
        current_rule: Optional[RuleResult],
        block_dur: int,
        switches_5m: int,
    ) -> str:
        """推断用户当前的模式。"""
        if current_slice is None or current_slice.is_afk:
            return "idle"

        app = current_slice.primary_app.lower()
        title = current_slice.primary_title.lower()
        activity = current_rule.activity_type if current_rule else "unknown"

        # Meeting
        if activity == "meeting":
            return "meeting"

        # Chatting
        if activity == "social":
            return "chatting"

        # Browsing
        if activity == "entertainment":
            return "browsing"

        # Reading / Research
        if activity == "research":
            return "reading"

        # Coding vs Debugging
        if activity == "programming":
            # 如果在 IDE 里长时间稳定工作 -> coding
            if block_dur >= 600 and switches_5m <= 1:
                return "coding"
            # 如果在 IDE 和浏览器/AI 之间频繁切换 -> debugging
            if switches_5m >= 3:
                return "debugging"
            return "coding"

        # Writing
        if activity == "writing":
            return "reading" if "阅读" in title or "read" in title else "coding"

        # Admin
        if activity == "admin":
            return "normal"

        return "unknown"

    def _assess_risk(
        self,
        current_slice: Optional[ActivitySlice],
        current_rule: Optional[RuleResult],
        block_dur: int,
        switches_5m: int,
        switches_30m: int,
        focus_score: float,
        likely_mode: str,
    ) -> str:
        """评估用户当前的风险等级。"""
        if current_slice is None or current_slice.is_afk:
            return "normal"

        activity = current_rule.activity_type if current_rule else "unknown"

        # Focused: 高质量专注块
        if likely_mode == "coding" and block_dur >= 1200 and switches_5m == 0:
            return "focused"

        # Fragmented: 频繁切换
        if switches_5m >= 5 or switches_30m >= 15:
            return "fragmented"

        # Distracted: 娱乐/社交占比高
        if activity in ("entertainment", "social"):
            return "distracted"

        # Stuck: 长时间 unknown 或 debugging 模式
        if likely_mode == "debugging" and block_dur >= 1800:
            return "stuck"
        if activity == "unknown" and block_dur >= 600:
            return "unknown"

        # Low focus: 滚动专注度低
        if focus_score < 30:
            return "fragmented"

        return "normal"

    def _build_idle_state(self, now: datetime) -> UserWorkState:
        """没有数据时的 idle 状态。"""
        empty = UserWorkState.empty()
        empty.updated_at = now
        return empty

    @staticmethod
    def _extract_domain(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower() if parsed.netloc else None
        except Exception:
            return None
```

---

## 四、与 Scheduler 集成

### 4.1 修改 `scheduler.py`

```python
# src/aw_coach/scheduler.py

from aw_coach.state import StateUpdater, UserWorkState  # 新增

class CoachScheduler:
    def __init__(self, config: Config, dashboard_url: Optional[str] = None):
        # ... 现有初始化 ...
        self._state_updater: Optional[StateUpdater] = None
        self._current_state: Optional[UserWorkState] = None

    @property
    def state_updater(self) -> StateUpdater:
        if self._state_updater is None:
            from aw_coach.rules.engine import RuleEngine
            self._state_updater = StateUpdater(self.collector, RuleEngine.with_all_rules())
        return self._state_updater

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        logger.info("AI Coach scheduler started")
        # ...

        while self._running:
            now = datetime.now()

            # === 新增：每分钟更新实时状态 ===
            try:
                self._current_state = self.state_updater.update(now)
                self._persist_state(self._current_state)
                logger.debug(
                    f"State updated: mode={self._current_state.likely_mode}, "
                    f"risk={self._current_state.risk_level}, "
                    f"block={self._current_state.active_block_duration_sec}s"
                )
            except Exception as e:
                logger.warning(f"State update failed: {e}")

            # === 新增：Detector 检查 ===
            try:
                self._check_detectors(now)
            except Exception as e:
                logger.warning(f"Detector check failed: {e}")

            # === 原有定时任务 ===
            next_hour = last_hourly + timedelta(hours=1)
            if now >= next_hour:
                if self._hourly_analyze(last_hourly, next_hour):
                    last_hourly = next_hour
                    self._save_last_hourly(last_hourly)

            interval = self.config.report.instant_summary_interval_hours
            if (now - last_summary).total_seconds() >= interval * 3600:
                self._send_instant_summary(now)
                last_summary = now
                self._save_last_summary(last_summary)

            # ... 日报检查 ...

            time.sleep(60)

    def _persist_state(self, state: UserWorkState) -> None:
        """将状态持久化到 SQLite，供 CLI 查询和重启恢复。"""
        try:
            self.storage.set_scheduler_state("user_work_state", json.dumps(state.to_dict()))
        except Exception:
            pass

    def _restore_state(self) -> Optional[UserWorkState]:
        """从 SQLite 恢复上次状态。"""
        try:
            raw = self.storage.get_scheduler_state("user_work_state")
            if raw:
                data = json.loads(raw)
                # TODO: 将 ISO 字符串转回 datetime
                return UserWorkState(**data)
        except Exception:
            pass
        return None

    def _check_detectors(self, now: datetime) -> None:
        """运行所有 detector，产出 AgentSignal。
        当前先占位，Detector 实现后填充。
        """
        if self._current_state is None:
            return
        # TODO: 调用各 detector，产出 signal 后送入 PolicyEngine
        pass
```

---

## 五、CLI 命令

```python
# src/aw_coach/cli.py

@main.command()
def state() -> None:
    """显示用户实时工作状态。"""
    config = load_config()

    # 1. 尝试从 SQLite 读取 scheduler 持久化的状态
    from aw_coach.storage import Storage
    storage = Storage(config.db_path)
    raw = storage.get_scheduler_state("user_work_state")

    if raw:
        import json
        data = json.loads(raw)
        _render_state(data)
        return

    # 2. 如果 scheduler 没运行过，现场计算
    try:
        from aw_coach.collector import DataCollector
        from aw_coach.rules.engine import RuleEngine
        from aw_coach.state import StateUpdater

        collector = DataCollector()
        engine = RuleEngine.with_all_rules()
        updater = StateUpdater(collector, engine)
        state_obj = updater.update()
        _render_state(state_obj.to_dict())
    except Exception as e:
        click.echo(f"无法计算状态: {e}", err=True)


def _render_state(data: dict) -> None:
    """渲染状态到终端。"""
    mode_emoji = {
        "coding": "💻", "debugging": "🐛", "reading": "📖",
        "meeting": "🗣️", "chatting": "💬", "browsing": "🌐",
        "idle": "💤", "unknown": "❓",
    }
    risk_emoji = {
        "focused": "🟢", "normal": "⚪", "fragmented": "🟡",
        "stuck": "🔴", "distracted": "🔴", "unknown": "⚪",
    }

    mode = data.get("likely_mode", "unknown")
    risk = data.get("risk_level", "unknown")
    block_sec = data.get("active_block_duration_sec", 0)
    block_min = block_sec // 60

    click.echo("┌────────────────────────────────────────┐")
    click.echo("│  🧠 AI Coach - 实时工作状态            │")
    click.echo("├────────────────────────────────────────┤")
    click.echo(f"│  当前应用: {data.get('current_app', 'unknown'):<28} │")
    click.echo(f"│  活动类型: {data.get('current_activity', 'unknown'):<28} │")
    click.echo(f"│  工作模式: {mode_emoji.get(mode, '❓')} {mode:<26} │")
    click.echo(f"│  风险等级: {risk_emoji.get(risk, '⚪')} {risk:<26} │")
    click.echo(f"│  专注块:   {block_min} 分钟 ({data.get('active_block_category', '-')})        │")
    click.echo("├────────────────────────────────────────┤")
    click.echo(f"│  5min 切换: {data.get('switches_last_5min', 0)} 次                      │")
    click.echo(f"│  30min 切换: {data.get('switches_last_30min', 0)} 次                     │")
    click.echo("├────────────────────────────────────────┤")
    click.echo(f"│  滚动专注度: {data.get('focus_score_rolling', 0)}/100                 │")
    click.echo(f"│  滚动生产力: {data.get('productivity_score_rolling', 0)}/100                 │")
    click.echo("└────────────────────────────────────────┘")

    if risk == "fragmented":
        click.echo("💡 提示: 你最近切换频繁，尝试关闭通知专注 25 分钟。")
    elif risk == "stuck":
        click.echo("💡 提示: 你似乎在 debug 中卡了很久，需要换个思路或休息？")
    elif risk == "distracted":
        click.echo("💡 提示: 当前活动属于娱乐/社交类，注意时间分配。")
```

---

## 六、持久化：SQLite 迁移

```python
# src/aw_coach/storage.py

# 在 _migrate() 中增加 v4：

if version < 4:
    self._conn.executescript("""
        -- 实时状态快照（每分钟覆盖）
        CREATE TABLE IF NOT EXISTS state_snapshots (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    self._conn.execute("PRAGMA user_version = 4")
    self._conn.commit()
```

可以复用已有的 `scheduler_state` 表，用 key="user_work_state" 存储 JSON，不需要新表。

---

## 七、测试策略

```python
# tests/test_state.py

import pytest
from datetime import datetime, timedelta

from aw_coach.state import StateUpdater, UserWorkState
from aw_coach.collector import ActivitySlice
from aw_coach.rules.engine import RuleEngine, RuleResult


class TestStateUpdater:
    def test_compute_active_block_continuous(self):
        """测试连续同类型工作块的计算。"""
        now = datetime.now()
        slices = [
            ActivitySlice(start=now - timedelta(minutes=5), end=now - timedelta(minutes=4), duration=60, is_afk=False, primary_app="vscode", primary_title="main.py"),
            ActivitySlice(start=now - timedelta(minutes=4), end=now - timedelta(minutes=2), duration=120, is_afk=False, primary_app="vscode", primary_title="main.py"),
            ActivitySlice(start=now - timedelta(minutes=2), end=now, duration=120, is_afk=False, primary_app="vscode", primary_title="main.py"),
        ]
        rules = [
            RuleResult("programming", 0.9, "rule"),
            RuleResult("programming", 0.9, "rule"),
            RuleResult("programming", 0.9, "rule"),
        ]
        updater = StateUpdater(None, None)
        start, cat, dur = updater._compute_active_block(slices, rules, now)
        assert cat == "programming"
        assert dur >= 300  # 5分钟

    def test_compute_active_block_afk_tolerance(self):
        """测试短 AFK 不中断活跃块。"""
        now = datetime.now()
        slices = [
            ActivitySlice(start=now - timedelta(minutes=3), end=now - timedelta(minutes=2), duration=60, is_afk=False, primary_app="vscode", primary_title="main.py"),
            ActivitySlice(start=now - timedelta(minutes=2), end=now - timedelta(seconds=100), duration=20, is_afk=True, primary_app="", primary_title=""),
            ActivitySlice(start=now - timedelta(seconds=100), end=now, duration=100, is_afk=False, primary_app="vscode", primary_title="main.py"),
        ]
        rules = [
            RuleResult("programming", 0.9, "rule"),
            RuleResult("afk", 1.0, "rule"),
            RuleResult("programming", 0.9, "rule"),
        ]
        updater = StateUpdater(None, None)
        start, cat, dur = updater._compute_active_block(slices, rules, now)
        assert cat == "programming"
        assert dur >= 160  # 第一个段 + 第三个段 ≈ 160s

    def test_infer_mode_coding(self):
        """测试 coding 模式推断。"""
        updater = StateUpdater(None, None)
        s = ActivitySlice(start=datetime.now(), end=datetime.now(), duration=60, is_afk=False, primary_app="code", primary_title="main.py")
        r = RuleResult("programming", 0.9, "rule")
        mode = updater._infer_likely_mode(s, r, block_dur=600, switches_5m=0)
        assert mode == "coding"

    def test_infer_mode_debugging(self):
        """测试 debugging 模式推断。"""
        updater = StateUpdater(None, None)
        s = ActivitySlice(start=datetime.now(), end=datetime.now(), duration=60, is_afk=False, primary_app="code", primary_title="main.py")
        r = RuleResult("programming", 0.9, "rule")
        mode = updater._infer_likely_mode(s, r, block_dur=60, switches_5m=5)
        assert mode == "debugging"

    def test_risk_focused(self):
        """测试 focused 风险评估。"""
        updater = StateUpdater(None, None)
        s = ActivitySlice(start=datetime.now(), end=datetime.now(), duration=60, is_afk=False, primary_app="code", primary_title="main.py")
        r = RuleResult("programming", 0.9, "rule")
        risk = updater._assess_risk(s, r, block_dur=1200, switches_5m=0, switches_30m=0, focus_score=80, likely_mode="coding")
        assert risk == "focused"

    def test_risk_fragmented(self):
        """测试 fragmented 风险评估。"""
        updater = StateUpdater(None, None)
        s = ActivitySlice(start=datetime.now(), end=datetime.now(), duration=60, is_afk=False, primary_app="chrome", primary_title="YouTube")
        r = RuleResult("entertainment", 0.9, "rule")
        risk = updater._assess_risk(s, r, block_dur=60, switches_5m=5, switches_30m=20, focus_score=20, likely_mode="browsing")
        assert risk == "fragmented"
```

---

## 八、实现顺序（今天就能开始）

| 步骤 | 文件 | 工作量 | 说明 |
|------|------|--------|------|
| 1 | 新建 `src/aw_coach/state.py` | 2h | 定义 `UserWorkState` + `StateUpdater` |
| 2 | 修改 `src/aw_coach/storage.py` | 30min | 用 `scheduler_state` 表存 JSON（无需新迁移）|
| 3 | 修改 `src/aw_coach/scheduler.py` | 1h | 主循环中调用 `state_updater.update()` + `_persist_state()` |
| 4 | 修改 `src/aw_coach/cli.py` | 1h | 新增 `aw-coach state` 命令 + `_render_state()` |
| 5 | 新建 `tests/test_state.py` | 1h | 写核心算法的单元测试 |
| 6 | 运行测试 + 观察 | 30min | `aw-coach state` 看看输出是否合理 |

**总计约 6 小时**，完成 Phase 1 的最小可行闭环。

---

## 九、下一步扩展方向

State Model 跑起来后，Detector 层就可以消费它了：

```python
# detector.py 中
if state.risk_level == "fragmented" and state.switches_last_5min >= 5:
    return AgentSignal(
        detector="high_switch_detector",
        type="rapid_context_switch",
        severity=0.7,
        evidence={"switches_5min": state.switches_last_5min},
        suggested_actions=["notify", "inbox"],
    )
```

Policy Engine 也可以基于 `state.risk_level` 做决策：

```python
# policy.py 中
if state.risk_level == "focused":
    return AgentDecision(action="log_only", reason="用户处于深度工作块，不打扰")
```

这才是 State Model 的价值：**成为所有上游模块的"事实来源"（single source of truth）**。
