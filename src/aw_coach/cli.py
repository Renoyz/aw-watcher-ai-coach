"""aw-coach CLI entry point."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

import click

from aw_coach import __version__
from aw_coach.config import DEFAULT_CONFIG_PATH, Config, load_config
from aw_coach.time_utils import format_local_timestamp, parse_stored_timestamp

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> date_type:
    if date_str == "today":
        return date_type.today()
    elif date_str == "yesterday":
        return date_type.today() - timedelta(days=1)
    else:
        return date_type.fromisoformat(date_str)


def _read_raw_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    with open(path, "rb") as f:
        return tomllib.load(f)


def _known_config_key(parts: list[str]) -> bool:
    if not parts or any(not part for part in parts):
        return False

    node: Any = Config().model_dump(mode="json")
    for index, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            return False
        if index == len(parts) - 1:
            return True
        node = node[part]
    return False


def _parse_config_value(value: str) -> Any:
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _set_nested_value(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    if not _known_config_key(parts):
        raise click.ClickException(f"Unknown config key: {dotted_key}")

    cursor = data
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise click.ClickException(f"Config key is not a table: {part}")
        cursor = child
    cursor[parts[-1]] = value


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _write_raw_config(path: Path, data: dict[str, Any]) -> None:
    lines: list[str] = []

    def emit(prefix: str, table: dict[str, Any]) -> None:
        scalar_items = [
            (key, value) for key, value in table.items() if not isinstance(value, dict)
        ]
        nested_items = [
            (key, value) for key, value in table.items() if isinstance(value, dict)
        ]

        if prefix and scalar_items:
            lines.append(f"[{prefix}]")
        for key, value in scalar_items:
            lines.append(f"{key} = {_toml_value(value)}")
        if scalar_items:
            lines.append("")

        for key, value in nested_items:
            next_prefix = f"{prefix}.{key}" if prefix else key
            emit(next_prefix, value)

    emit("", data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _try_read_from_bucket(target_date: date_type, config: Config):
    """P0-2: Try to read pre-analyzed results from ai-coach bucket."""
    from aw_coach.analyzer import AnalysisResult
    from aw_coach.collector import DataCollector, _utc_to_local

    try:
        collector = DataCollector()
        bucket_id = f"ai-coach_{collector.hostname}"
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        events = collector.client.get_events(bucket_id, start=start, end=end)

        if not events:
            return None

        # Aggregate hourly events into a daily summary
        hourly_events = [e for e in events if e.data.get("type") == "hourly_analysis"]
        if not hourly_events:
            return None

        total_effective = sum(e.data.get("effective_hours", 0) for e in hourly_events)
        total_deep = sum(e.data.get("deep_work_hours", 0) for e in hourly_events)
        total_switches = sum(e.data.get("switch_count", 0) for e in hourly_events)
        focus_scores = [e.data.get("focus_score", 0) for e in hourly_events]
        avg_focus = int(sum(focus_scores) / len(focus_scores)) if focus_scores else 0
        productivity_scores = [e.data.get("productivity_score", 0) for e in hourly_events]
        avg_productivity = (
            int(sum(productivity_scores) / len(productivity_scores))
            if productivity_scores
            else 0
        )

        # Merge activity breakdowns
        from collections import defaultdict
        breakdown: dict = defaultdict(float)
        for e in hourly_events:
            for k, v in e.data.get("activity_breakdown", {}).items():
                breakdown[k] += v

        total_hours = sum(breakdown.values())

        # Merge death loops
        death_loops: List[Dict] = []
        for e in hourly_events:
            loops = e.data.get("death_loops", [])
            if loops:
                death_loops.extend(loops)

        hourly_scores = [
            (_utc_to_local(e.timestamp).hour, e.data.get("focus_score", 0))
            for e in hourly_events
        ]

        return AnalysisResult(
            total_hours=total_hours,
            effective_hours=total_effective,
            deep_work_hours=total_deep,
            focus_score=avg_focus,
            switch_count=total_switches,
            activity_breakdown=dict(breakdown),
            hourly_scores=sorted(hourly_scores),
            productivity_score=avg_productivity,
            death_loops=death_loops,
        )
    except Exception:
        logger.debug(
            "Could not read pre-analyzed bucket; falling back to live data.",
            exc_info=True,
        )
        return None


def _classify_slices(config: Config, slices):
    """Classify slices using the configured classifier service."""
    from aw_coach.classifier import create_classifier

    def warn_fallback(e: Exception) -> None:
        click.echo(
            f"Warning: hybrid backend failed ({e}), falling back to rules.",
            err=True,
        )

    return create_classifier(config, on_hybrid_fallback=warn_fallback).batch_classify(slices)


def _get_analysis(target_date: date_type, config: Config):
    """Get analysis result: try bucket first, fallback to live computation."""
    from aw_coach.analyzer import PatternAnalyzer
    from aw_coach.collector import DataCollector

    # 1. Try pre-computed results from ai-coach bucket
    result = _try_read_from_bucket(target_date, config)
    if result is not None:
        return result

    # 2. Fallback: compute from raw data
    try:
        collector = DataCollector()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Is ActivityWatch running? Try `aw-coach doctor`.", err=True)
        sys.exit(1)

    start = datetime.combine(target_date, datetime.min.time())
    end = datetime.combine(target_date, datetime.max.time())
    if target_date == date_type.today():
        end = datetime.now()

    slices = collector.fetch_range(start, end)
    if not slices:
        return None

    rules = _classify_slices(config, slices)
    analyzer = PatternAnalyzer(config.analysis)
    return analyzer.analyze(slices, rules)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="aw-coach")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output (DEBUG level)")
@click.option("-q", "--quiet", is_flag=True, help="Quiet output (WARNING level only)")
@click.pass_context
def main(ctx: click.Context, verbose: bool, quiet: bool) -> None:
    """ActivityWatch AI Coach - work pattern analysis tool."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet

    # P1-7: Configure logging based on flags
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(level=level, format="[%(levelname)s] %(name)s: %(message)s")
    logging.getLogger("aw_coach").setLevel(level)
    if not verbose:
        for noisy_logger in ("httpx", "httpcore", "persistqueue"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    if ctx.invoked_subcommand is None:
        click.echo(f"aw-coach {__version__}")
        click.echo()
        click.echo("Usage: aw-coach <command> [options]")
        click.echo()
        click.echo("Commands:")
        click.echo("  status      Show current work status")
        click.echo("  report      View daily report")
        click.echo("  doctor      Diagnose system health")
        click.echo("  config      Show or update configuration")
        click.echo("  purge       Delete local generated data")
        click.echo("  calibrate   Classify unknown apps interactively")
        click.echo("  rule-test   Test rule engine matching")
        click.echo("  cost         View AI API cost usage")
        click.echo("  notify-test  Send a test notification")
        click.echo("  serve       Start interactive local dashboard")
        click.echo()
        click.echo("Run `aw-coach --help` for all commands.")


@main.group(name="config")
def config_group() -> None:
    """Show or update configuration."""


@config_group.command("path")
def config_path() -> None:
    """Print the active config file path."""
    click.echo(str(DEFAULT_CONFIG_PATH))


@config_group.command("show")
def config_show() -> None:
    """Print effective configuration as JSON."""
    config = load_config()
    click.echo(json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2))


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config value using a dotted key."""
    path = Path(DEFAULT_CONFIG_PATH)
    raw = _read_raw_config(path)
    parsed_value = _parse_config_value(value)
    _set_nested_value(raw, key, parsed_value)

    try:
        Config(**raw)
    except Exception as e:
        raise click.ClickException(f"Invalid config value: {e}") from e

    _write_raw_config(path, raw)
    click.echo(f"Updated {path}: {key} = {_toml_value(parsed_value)}")


@main.command()
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation")
def purge(yes: bool) -> None:
    """Delete local generated data while preserving configuration."""
    config = load_config()
    targets = [
        ("database", Path(config.db_path)),
        ("reports", Path(config.reports_dir)),
        ("screenshots", Path(config.data_dir) / "screenshots"),
    ]
    existing = [(label, path) for label, path in targets if path.exists()]

    if not existing:
        click.echo("No local data found to purge.")
        return

    click.echo("This will delete local generated data:")
    for label, path in existing:
        click.echo(f"  {label}: {path}")

    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Purge cancelled.")
        return

    for _, path in existing:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    click.echo(f"✅ Purged {len(existing)} item(s). Config preserved: {DEFAULT_CONFIG_PATH}")


@main.command()
def status() -> None:
    """Show real-time work status."""
    from aw_coach.report import ReportGenerator

    config = load_config()
    try:
        analysis = _get_analysis(date_type.today(), config)
    except SystemExit:
        return

    if analysis is None:
        click.echo("暂无今日数据。尝试拉取历史记录...")
        analysis = _first_run_historical(config)
        if analysis is None:
            click.echo("ActivityWatch 暂无足够数据。运行数小时后再试。")
            click.echo("提示: `aw-coach doctor` 确认环境正常。")
            return
        click.echo("(以下为过去 7 天汇总)\n")

    reporter = ReportGenerator(config)
    click.echo(reporter.generate_status(analysis))


def _first_run_historical(config: Config):
    """Pull 7 days of history for first-run experience."""
    from aw_coach.analyzer import PatternAnalyzer
    from aw_coach.collector import DataCollector
    try:
        collector = DataCollector()
    except Exception:
        return None

    week_ago = datetime.now() - timedelta(days=7)
    slices = collector.fetch_range(week_ago, datetime.now())
    if not slices or len(slices) < 10:
        return None

    rules = _classify_slices(config, slices)
    analyzer = PatternAnalyzer(config.analysis)
    return analyzer.analyze(slices, rules)


# ---------------------------------------------------------------------------
# Semantic state command (Phase 1)
# ---------------------------------------------------------------------------


def _compute_active_block_minutes(slices, rule_engine):
    """Roughly compute how many minutes the current activity block has lasted."""
    if not slices:
        return 0
    sorted_slices = sorted(slices, key=lambda s: s.end)
    latest = sorted_slices[-1]
    latest_type = rule_engine.classify(
        latest.primary_app, latest.primary_title, latest.web_url
    ).activity_type

    total_sec = 0
    for s in reversed(sorted_slices):
        s_type = rule_engine.classify(
            s.primary_app, s.primary_title, s.web_url
        ).activity_type
        if s_type == latest_type:
            total_sec += getattr(s, "duration", 60)
        else:
            break
    return total_sec // 60


def _count_switches_in_slices(slices, rule_engine):
    """Count activity-type switches in the given slices."""
    if len(slices) < 2:
        return 0
    sorted_slices = sorted(slices, key=lambda s: s.end)
    types = [
        rule_engine.classify(s.primary_app, s.primary_title, s.web_url).activity_type
        for s in sorted_slices
    ]
    return sum(1 for i in range(len(types) - 1) if types[i] != types[i + 1])


def _render_semantic_state(state_obj, chain_result, context_stack=None, screenshot=None) -> None:
    """Render SemanticWorkState + ChainAnalysisResult to terminal."""
    mode_emoji = {
        "coding": "💻",
        "debugging": "🐛",
        "testing": "🧪",
        "researching": "📖",
        "collaborating": "🤝",
        "writing": "✍️",
        "meeting": "🗣️",
        "chatting": "💬",
        "browsing": "🌐",
        "terminal": "⌨️",
        "deploying": "🚀",
        "idle": "💤",
        "unknown": "❓",
    }
    risk_emoji = {
        "focused": "🟢",
        "normal": "⚪",
        "fragmented": "🟡",
        "stuck": "🔴",
        "distracted": "🔴",
        "unknown": "⚪",
    }
    pattern_emoji = {
        "deep_coding": "🎯",
        "debug_cycle": "🐛",
        "research_loop": "📚",
        "context_switching": "🔄",
        "meeting_block": "🗣️",
        "idle": "💤",
        "insufficient_data": "❓",
    }

    mode = state_obj.likely_mode
    risk = state_obj.risk_level
    pattern = chain_result.pattern if chain_result else "insufficient_data"

    # Context Stack info
    cs_primary_mode = None
    cs_primary_project = None
    cs_depth = 0
    if context_stack:
        cs_primary_mode = context_stack.get("primary_mode")
        cs_primary_project = context_stack.get("primary_project")
        cs_depth = context_stack.get("depth", 0)
    show_cs = bool(cs_primary_mode and cs_depth > 0)

    click.echo("┌──────────────────────────────────────────┐")
    click.echo("│  🧠 AI Coach — 实时语义状态              │")
    click.echo("├──────────────────────────────────────────┤")
    click.echo(f"│  应用:      {state_obj.current_app:<30} │")
    title = (
        state_obj.current_title[:28] + "…"
        if len(state_obj.current_title) > 29
        else state_obj.current_title
    )
    click.echo(f"│  标题:      {title:<30} │")
    if state_obj.task_label:
        click.echo(f"│  当前任务:  {state_obj.task_label:<30} │")
    if state_obj.task_id:
        tid = (
            state_obj.task_id[:28] + "…"
            if len(state_obj.task_id) > 29
            else state_obj.task_id
        )
        click.echo(f"│  任务ID:    {tid:<30} │")
    if state_obj.semantic_project:
        click.echo(f"│  项目:      {state_obj.semantic_project:<30} │")
    if state_obj.semantic_filename:
        lang = f" ({state_obj.semantic_language})" if state_obj.semantic_language else ""
        click.echo(f"│  文件:      {state_obj.semantic_filename}{lang:<28} │")
    if state_obj.semantic_site:
        click.echo(f"│  网站:      {state_obj.semantic_site:<30} │")
    if state_obj.git_repo:
        dirty = "*" if state_obj.git_is_dirty else ""
        click.echo(f"│  Git:       {state_obj.git_repo}@{state_obj.git_branch}{dirty:<28} │")
    if show_cs:
        click.echo("├──────────────────────────────────────────┤")
        cs_mode_str = cs_primary_mode or "-"
        cs_proj_str = cs_primary_project or "-"
        click.echo(f"│  主上下文:  {cs_mode_str:<28} │")
        click.echo(f"│  主项目:    {cs_proj_str:<28} │")

    click.echo("├──────────────────────────────────────────┤")
    click.echo(f"│  工作模式:  {mode_emoji.get(mode, '❓')} {mode:<28} │")
    click.echo(f"│  风险等级:  {risk_emoji.get(risk, '⚪')} {risk:<28} │")
    click.echo(f"│  活动模式:  {pattern_emoji.get(pattern, '❓')} {pattern:<28} │")
    block_text = (
        f"{state_obj.active_block_minutes} 分钟"
        if state_obj.active_block_minutes >= 1
        else "不足 1 分钟"
    )
    click.echo(f"│  专注块:    {block_text:<30} │")
    if state_obj.switches_last_5min:
        click.echo(f"│  5分钟切换: {state_obj.switches_last_5min} 次{'':<29} │")
    if screenshot:
        click.echo("├──────────────────────────────────────────┤")
        diff_pct = int(screenshot.get("diff_ratio", 0) * 100)
        ctype = screenshot.get("content_type", "unknown")
        ctype_label = {
            "static": "静态内容",
            "scrolling": "滚动/打字",
            "video": "视频/动画",
            "major_change": "大幅变化",
        }.get(ctype, ctype)
        click.echo(f"│  屏幕变化:  {diff_pct}% ({ctype_label})")
        ocr = screenshot.get("ocr_text")
        if ocr:
            ocr_preview = ocr[:35] + "…" if len(ocr) > 35 else ocr
            click.echo(f"│  OCR文本:   {ocr_preview}")
    click.echo("└──────────────────────────────────────────┘")

    if chain_result and chain_result.insight:
        click.echo(f"💡 {chain_result.insight}")

    if risk == "fragmented":
        click.echo("💡 提示: 最近切换频繁，尝试番茄工作法聚焦 25 分钟。")
    elif risk == "stuck":
        click.echo("💡 提示: 你似乎卡住了，站起来活动一下或换个思路？")
    elif risk == "distracted":
        click.echo("💡 提示: 当前活动偏休闲，注意时间分配。")


@main.command()
def state() -> None:
    """Show semantic-enriched real-time work state."""
    from aw_coach.chain_analyzer import ChainAnalysisResult, ChainAnalyzer
    from aw_coach.collector import DataCollector
    from aw_coach.enriched_state import EnrichedStateAssembler, SemanticWorkState
    from aw_coach.rules.engine import RuleEngine
    from aw_coach.storage import Storage

    config = load_config()

    # 1. Try to read persisted state from scheduler
    try:
        storage = Storage(config.db_path)
        raw = storage.get_scheduler_state("semantic_state")
        if raw:
            import json

            data = json.loads(raw)
            state_dict = data.get("state", {})
            chain_dict = data.get("chain", {})
            cs_dict = data.get("context_stack", {})
            screenshot_dict = data.get("screenshot")
            # Parse updated_at back to datetime if present
            if "updated_at" in state_dict and isinstance(state_dict["updated_at"], str):
                state_dict["updated_at"] = datetime.fromisoformat(state_dict["updated_at"])
            state_obj = SemanticWorkState(**state_dict)
            chain_result = ChainAnalysisResult(
                pattern=chain_dict.get("pattern", "insufficient_data"),
                depth_score=chain_dict.get("depth_score", 0.0),
                fragmentation_score=chain_dict.get("fragmentation_score", 0.0),
                insight=chain_dict.get("insight"),
                confidence=1.0,
            )
            _render_semantic_state(state_obj, chain_result, cs_dict, screenshot_dict)
            click.echo("\n(数据来自 scheduler 缓存)")
            return
    except Exception:
        pass

    # 2. Fallback: compute live from aw data
    try:
        collector = DataCollector()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Is ActivityWatch running? Try `aw-coach doctor`.", err=True)
        return

    start = datetime.now() - timedelta(minutes=30)
    slices = [
        s for s in collector.fetch_range(start, datetime.now())
        if not s.is_afk and getattr(s, "duration", 0) >= 3
    ]

    if not slices:
        click.echo("暂无活动数据。ActivityWatch 是否正在运行？")
        return

    # Align timezone with slice timestamps for consistent comparison
    _tz = getattr(slices[0].end, "tzinfo", None)
    now = datetime.now(_tz) if _tz else datetime.now()

    engine = RuleEngine.with_all_rules()
    latest = max(slices, key=lambda s: s.end)
    rule = engine.classify(latest.primary_app, latest.primary_title, latest.web_url)

    block_minutes = _compute_active_block_minutes(slices, engine)

    recent_start = now - timedelta(minutes=5)
    recent_slices = [s for s in slices if s.end >= recent_start]
    switches_5m = _count_switches_in_slices(recent_slices, engine)

    assembler = EnrichedStateAssembler()
    state_obj = assembler.assemble(
        app=latest.primary_app,
        title=latest.primary_title,
        url=getattr(latest, "web_url", None),
        active_block_minutes=block_minutes,
        rule_activity=rule.activity_type,
        switches_last_5min=switches_5m,
    )

    # Build a tiny chain for analysis (last up to 10 slices)
    chain_records = []
    for s in sorted(slices, key=lambda s: s.end)[-10:]:
        s_rule = engine.classify(s.primary_app, s.primary_title, s.web_url)
        chain_records.append(
            assembler.assemble(
                app=s.primary_app,
                title=s.primary_title,
                url=getattr(s, "web_url", None),
                active_block_minutes=getattr(s, "duration", 60) // 60,
                rule_activity=s_rule.activity_type,
            )
        )

    chain_analyzer = ChainAnalyzer()
    chain_result = chain_analyzer.analyze(chain_records)

    _render_semantic_state(state_obj, chain_result)


@main.command()
@click.option("--full", is_flag=True, help="Full report with AI suggestions")
@click.option("--dry-run", is_flag=True, help="Show LLM prompt without calling API")
@click.argument("date", default="today")
def report(full: bool, dry_run: bool, date: str) -> None:
    """View daily report."""
    from aw_coach.report import ReportGenerator

    config = load_config()
    target = _parse_date(date)

    try:
        analysis = _get_analysis(target, config)
    except SystemExit:
        return

    if analysis is None:
        click.echo(f"{target.isoformat()} 暂无数据。")
        return

    # --dry-run must NOT trigger any real LLM call.
    # Decide whether AI suggestions are requested.
    use_ai = full and not dry_run and config.ai.backend != "rule_only"

    # Task/project breakdown
    project_breakdown = None
    inbox_items = None
    try:
        from aw_coach.storage import Storage

        storage = Storage(config.db_path)
        inbox_items = storage.get_inbox_items(dismissed=False, limit=10)
        task_rows = storage.get_task_daily_summary(target.isoformat())
        if task_rows:
            project_breakdown = {
                row["label"]: row["total_sec"] / 3600 for row in task_rows
            }
        else:
            from collections import defaultdict

            from aw_coach.collector import DataCollector
            from aw_coach.context_parser import TitleParser

            collector = DataCollector()
            start = datetime.combine(target, datetime.min.time())
            end = datetime.combine(target, datetime.max.time())
            if target == date_type.today():
                end = datetime.now()
            slices = collector.fetch_range(start, end)
            parser = TitleParser()
            proj_dur = defaultdict(float)
            for s in slices:
                if s.is_afk:
                    continue
                ctx = parser.parse(s.primary_app, s.primary_title, s.web_url)
                if ctx.project:
                    proj_dur[ctx.project] += s.duration / 3600
            if proj_dur:
                project_breakdown = dict(proj_dur)
    except Exception:
        logger.debug("Project breakdown failed", exc_info=True)

    reporter = ReportGenerator(config)
    report_text = reporter.generate_daily(
        target,
        analysis,
        use_ai=use_ai,
        project_breakdown=project_breakdown,
        inbox_items=inbox_items,
    )

    if dry_run and config.ai.backend != "rule_only":
        from aw_coach.ai.suggestions import _build_prompt
        prompt = _build_prompt(analysis, is_weekly=False)
        click.echo("=== DRY RUN: LLM Prompt ===")
        click.echo(prompt)
        click.echo("=== END PROMPT ===")
        return

    if full and config.ai.backend != "rule_only":
        click.echo("[INFO] AI-enhanced suggestions enabled via DeepSeek.")

    click.echo(report_text)

    # P1-5: Save report to file
    reports_dir = config.reports_dir / "daily"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{target.isoformat()}.md"
    if not report_path.exists():
        report_path.write_text(report_text, encoding="utf-8")


def _build_report_prompt(analysis) -> str:
    """Build LLM prompt for --full --dry-run."""
    return f"""你是一位专业的工作效率教练。请根据以下用户今日数据，生成 3-5 条具体、可执行的建议。

今日概况：
- 总工作时长: {analysis.total_hours:.1f}h
- 有效工作时长: {analysis.effective_hours:.1f}h
- 深度工作时长: {analysis.deep_work_hours:.1f}h
- 任务切换次数: {analysis.switch_count}
- 专注得分: {analysis.focus_score}/100
- 各类型时间分布: {analysis.activity_breakdown}

要求：
1. 建议要具体，避免空泛
2. 优先指出可立即改进的点
3. 若表现优秀，给予正面鼓励
4. 返回 JSON 数组，每条包含：type, message, priority(1-3)"""


@main.command()
def weekly() -> None:
    """View weekly report (last 7 days)."""
    from aw_coach.report import ReportGenerator

    config = load_config()
    today = date_type.today()
    week_start = today - timedelta(days=6)

    daily_results = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        analysis = _get_analysis(day, config)
        if analysis and analysis.total_hours > 0:
            daily_results.append(analysis)

    if not daily_results:
        click.echo("过去 7 天暂无足够数据生成周报。")
        return

    reporter = ReportGenerator(config)
    report_text = reporter.generate_weekly(week_start, daily_results)
    click.echo(report_text)

    # Save
    reports_dir = config.reports_dir / "weekly"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{week_start.isoformat()}.md"
    report_path.write_text(report_text, encoding="utf-8")


@main.command()
@click.option(
    "--calibrate",
    "run_calibrate",
    is_flag=True,
    help="Run interactive app calibration after health checks",
)
def doctor(run_calibrate: bool) -> None:
    """Diagnose system health."""
    import platform as plat

    from aw_coach.rules.engine import RuleEngine

    config = load_config()

    # 1. Check aw-server
    try:
        from aw_coach.collector import DataCollector
        collector = DataCollector()
        click.echo(
            click.style("✅ aw-server", fg="green")
            + f"  reachable (host: {collector.hostname})"
        )
    except Exception as e:
        click.echo(click.style("🔴 aw-server", fg="red") + f"  {e}")
        return

    # 2. Check ai-coach bucket
    try:
        bucket_id = f"ai-coach_{collector.hostname}"
        buckets = collector.client.get_buckets()
        if bucket_id in buckets:
            click.echo(click.style("✅ ai-coach bucket", fg="green") + f"  {bucket_id}")
        else:
            click.echo(
                click.style("ℹ️  ai-coach bucket", fg="blue")
                + "  not yet created (will auto-create on first scheduler run)"
            )
    except Exception:
        pass

    # 3. Check rule engine
    try:
        engine = RuleEngine.with_builtin_rules()
        rule_count = len(engine.rules)
        click.echo(
            click.style("✅ rule engine", fg="green") + f"  {rule_count} rules loaded"
        )
    except Exception as e:
        click.echo(click.style("🔴 rule engine", fg="red") + f"  {e}")

    # 4. Platform/screenshot check
    system = plat.system()
    if system == "Linux":
        import os
        if os.environ.get("WAYLAND_DISPLAY"):
            click.echo(
                click.style("⚠️  platform", fg="yellow")
                + "    Wayland detected, screenshot requires xdg-desktop-portal"
            )
        else:
            click.echo(click.style("✅ platform", fg="green") + "    X11 (screenshot OK)")
    elif system == "Darwin":
        click.echo(
            click.style("ℹ️  platform", fg="blue")
            + "    macOS (check Screen Recording permission)"
        )
    else:
        click.echo(click.style("✅ platform", fg="green") + f"    {system}")

    # 5. AI backend + cost
    click.echo(click.style("ℹ️  ai backend", fg="blue") + f"  {config.ai.backend}")
    click.echo(
        click.style("ℹ️  cost", fg="blue")
        + f"       budget: ${config.cost.monthly_budget_usd:.2f}/month"
    )

    # 6. Correction feedback loop
    try:
        from aw_coach.correction import build_pending_rule_suggestions
        from aw_coach.storage import Storage

        storage = Storage(config.db_path)
        correction_count = sum(storage.get_correction_counts().values())
        pending_count = len(build_pending_rule_suggestions(storage, engine))
        click.echo(
            click.style("ℹ️  corrections", fg="blue")
            + f" {correction_count} stored, {pending_count} pending rule suggestions"
        )
    except Exception as e:
        click.echo(click.style("⚠️  corrections", fg="yellow") + f" {e}")

    if run_calibrate:
        click.echo()
        _run_calibrate(config)


@main.command("rule-test")
@click.option("--app", required=True, help="Application name to test")
@click.option("--title", default="", help="Window title")
@click.option("--url", default=None, help="Browser URL")
def rule_test(app: str, title: str, url: Optional[str]) -> None:
    """Test rule engine matching for given inputs."""
    from aw_coach.rules.engine import RuleEngine

    engine = RuleEngine.with_builtin_rules()
    result = engine.classify(app, title, url)

    click.echo(f"  app:          {app}")
    click.echo(f"  title:        {title or '(empty)'}")
    click.echo(f"  url:          {url or '(none)'}")
    click.echo("  ─────────────────────────────")
    click.echo(f"  activity:     {result.activity_type}")
    click.echo(f"  confidence:   {result.confidence:.2f}")
    click.echo(f"  method:       {result.method}")
    click.echo(f"  rule:         {result.rule_name or '(none)'}")


@main.command("rule-suggest")
@click.option(
    "--from-corrections",
    is_flag=True,
    help="Generate suggestions from correction history (default)",
)
@click.option("--min-count", default=3, show_default=True, help="Minimum corrections needed")
def rule_suggest(from_corrections: bool, min_count: int) -> None:
    """Generate rule suggestions from correction history."""
    from aw_coach.correction import (
        append_user_rule,
        build_pending_rule_suggestions,
        resolve_type,
    )
    from aw_coach.rules.engine import RuleEngine
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)
    engine = RuleEngine.with_builtin_rules()

    suggestions = build_pending_rule_suggestions(storage, engine, min_count=min_count)
    if not suggestions:
        counts = storage.get_correction_counts()
        if not counts:
            click.echo("No corrections stored yet. Use `aw-coach correct --review` first.")
            return
        click.echo("No pending rule suggestions.")
        click.echo(f"Current corrections: {sum(counts.values())} total.")
        return

    rules_dir = config.data_dir / "rules"
    user_rules_path = rules_dir / "user.yml"

    click.echo(f"Pending rule suggestions ({len(suggestions)}):\n")
    added = 0
    rejected = 0

    for i, suggestion in enumerate(suggestions, 1):
        original_suggestion = suggestion
        _print_rule_suggestion(i, suggestion)
        action = click.prompt(
            "Action [a]ccept / [e]dit / [r]eject / [s]kip / [q]uit",
            default="s",
        ).strip().lower()

        if action in {"q", "quit"}:
            break
        if action in {"s", "skip", ""}:
            continue
        if action in {"r", "reject", "n", "no"}:
            storage.set_rule_suggestion_status(
                suggestion.app,
                suggestion.corrected_type,
                "rejected",
            )
            rejected += 1
            click.echo("  Rejected.")
            continue
        if action in {"e", "edit"}:
            suggestion = _edit_rule_suggestion(suggestion, resolve_type)
            if suggestion is None:
                click.echo("  Edit cancelled.")
                continue

        if action in {"a", "accept", "y", "yes", "e", "edit"}:
            append_user_rule(user_rules_path, suggestion)
            storage.set_rule_suggestion_status(
                original_suggestion.app,
                original_suggestion.corrected_type,
                "accepted",
                rule_name=suggestion.rule_name,
            )
            added += 1
            click.echo(f"  Accepted: {suggestion.rule_name}")
            continue

        click.echo(f"  Unknown action: {action}")

    click.echo()
    click.echo(f"✅ Added {added} rule(s), rejected {rejected} suggestion(s).")
    if added:
        click.echo(f"Rules saved to {user_rules_path}")
        click.echo("Rules take effect on next analysis cycle.")


def _print_rule_suggestion(index: int, suggestion) -> None:
    original = ", ".join(suggestion.original_types) if suggestion.original_types else "unknown"
    click.echo(f"[{index}] app=\"{suggestion.app}\" -> {suggestion.corrected_type}")
    click.echo(
        f"    source: {suggestion.correction_count} corrections, "
        f"latest {suggestion.latest_corrected_at}"
    )
    click.echo(f"    confidence: {suggestion.confidence:.2f}")
    click.echo(f"    original types: {original}")


def _edit_rule_suggestion(suggestion, resolve_type):
    from aw_coach.correction import RuleSuggestion

    app = click.prompt("    app", default=suggestion.app).strip()
    raw_type = click.prompt("    type", default=suggestion.corrected_type).strip()
    corrected_type = resolve_type(raw_type)
    if corrected_type is None:
        click.echo(f"    Invalid type: {raw_type}")
        return None
    confidence = click.prompt(
        "    confidence",
        default=round(suggestion.confidence, 2),
        type=float,
    )
    confidence = max(0.0, min(1.0, confidence))
    return RuleSuggestion(
        app=app,
        corrected_type=corrected_type,
        correction_count=suggestion.correction_count,
        latest_corrected_at=suggestion.latest_corrected_at,
        confidence=confidence,
        original_types=suggestion.original_types,
    )


@main.command("notify-test")
def notify_test() -> None:
    """Send a test notification to verify the system works."""
    from aw_coach.notify import send_notification
    from aw_coach.report import generate_html_dashboard

    config = load_config()
    method = config.report.notification_method

    # Generate a demo dashboard for the click-to-open test
    target = date_type.today()
    try:
        analysis = _get_analysis(target, config)
    except SystemExit:
        analysis = None

    detail_url = None
    if analysis is not None:
        html_path = generate_html_dashboard(config, target, analysis)
        # Start a temporary webserver for Snap Firefox compatibility
        from aw_coach.webserver import ReportServer
        server = ReportServer(html_path.parent)
        server.start()
        detail_url = server.dashboard_url
        click.echo(f"📊 Dashboard generated: {html_path}")

    send_notification(
        "AI Coach 测试通知",
        "如果您看到这条通知，说明推送系统工作正常。\n点击可查看今日仪表盘。",
        detail_url=detail_url,
    )
    click.echo(f"✅ Test notification sent via '{method}'.")
    if detail_url:
        click.echo("   👆 点击通知上的「查看详情」按钮可打开仪表盘。")
    if method == "cli_only":
        click.echo("   Note: notification_method = 'cli_only', only logs are shown.")
        click.echo("   Edit config to 'both' or 'desktop' for real notifications.")


@main.command()
def health() -> None:
    """Show daemon, scheduling, delivery, and cost health."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)

    service_state = "unknown"
    if shutil.which("systemctl"):
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", "aw-coach.service"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            service_state = proc.stdout.strip() or proc.stderr.strip() or "unknown"
        except Exception:
            service_state = "unknown"

    last_hourly = storage.get_scheduler_state("last_hourly", "unknown")
    last_summary = storage.get_scheduler_state("last_summary", "unknown")
    next_summary = "unknown"
    if last_summary and last_summary != "unknown":
        try:
            next_dt = parse_stored_timestamp(last_summary) + timedelta(
                hours=config.report.instant_summary_interval_hours
            )
            next_summary = next_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            next_summary = "unknown"

    daily_done = storage.get_scheduler_state(
        f"daily_report_done:{date_type.today().isoformat()}", "0"
    )
    recent_delivery = storage.get_recent_delivery_logs(limit=1)
    recent_issue = storage.get_recent_delivery_issue()
    total_cost = storage.get_monthly_cost()

    click.echo(f"service:        {service_state}")
    click.echo(f"last_hourly:    {_format_health_time(last_hourly)}")
    click.echo(f"last_summary:   {_format_health_time(last_summary)}")
    click.echo(f"next_summary:   {next_summary}")
    click.echo(f"daily_report:   {'done' if daily_done == '1' else 'pending'}")
    click.echo(f"delivery_mode:  summary={config.report.delivery.instant_summary}, "
               f"daily={config.report.delivery.daily_report}, "
               f"medium={config.report.delivery.medium_signal}")
    click.echo(f"llm_timeout:    {config.report.llm_timeout_seconds}s")
    click.echo(
        f"cost:           ${total_cost:.2f}/${config.cost.monthly_budget_usd:.2f}"
    )

    if recent_delivery:
        item = recent_delivery[0]
        click.echo(
            "last_delivery: "
            f"{format_local_timestamp(item['timestamp'])} "
            f"{item['kind']}/{item['channel']} {item['status']} "
            f"{item.get('reason') or ''}".rstrip()
        )
    else:
        click.echo("last_delivery: none")

    if recent_issue:
        click.echo(
            "last_issue:    "
            f"{format_local_timestamp(recent_issue['timestamp'])} "
            f"{recent_issue['kind']}/{recent_issue['channel']} "
            f"{recent_issue['status']} {recent_issue.get('reason') or ''}".rstrip()
        )
    else:
        click.echo("last_issue:    none")


def _format_health_time(value: Optional[str]) -> str:
    if not value or value == "unknown":
        return "unknown"
    try:
        return format_local_timestamp(value)
    except Exception:
        return value


@main.command()
def cost() -> None:
    """View AI API cost usage."""
    config = load_config()
    from aw_coach.storage import Storage

    storage = Storage(config.db_path)
    total = storage.get_monthly_cost()
    breakdown = storage.get_cost_breakdown()
    budget = config.cost.monthly_budget_usd

    click.echo(f"  AI backend:   {config.ai.backend}")
    click.echo(f"  月度预算:     ${budget:.2f}")
    click.echo(f"  本月已用:     ${total:.2f} ({total/budget*100:.1f}%)" if budget > 0 else "")
    click.echo(f"  剩余:         ${budget - total:.2f}")

    if breakdown:
        click.echo()
        click.echo("  调用明细:")
        for op, cost_val in sorted(breakdown.items(), key=lambda x: -x[1]):
            click.echo(f"    {op:<20} ${cost_val:.4f}")
    elif config.ai.backend == "rule_only":
        click.echo()
        click.echo("  rule_only 模式无 API 调用。升级到 hybrid 启用 AI 增强。")


@main.command()
@click.option(
    "--last", "correct_last", is_flag=True, help="Correct the most recent classification"
)
@click.option("--time", "time_range", default=None, help="Time range to correct (e.g. 14:00-15:00)")
@click.option(
    "--interactive",
    "interactive",
    is_flag=True,
    help="Review low-confidence items interactively",
)
@click.option(
    "--review",
    "review",
    is_flag=True,
    help="Review low-confidence slices with confirm/skip/type prompts",
)
@click.argument("activity_type", required=False)
def correct(
    correct_last: bool,
    time_range: Optional[str],
    interactive: bool,
    review: bool,
    activity_type: Optional[str],
) -> None:
    """Correct AI classification results."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)

    from aw_coach.correction import VALID_ACTIVITY_TYPES

    valid_types = set(VALID_ACTIVITY_TYPES)

    if review or interactive:
        _correct_review(config, storage, valid_types, include_all=interactive)
        return

    if not activity_type:
        click.echo("Usage: aw-coach correct --last <type>")
        click.echo("       aw-coach correct --time 14:00-15:00 <type>")
        click.echo("       aw-coach correct --review")
        click.echo("       aw-coach correct --interactive")
        click.echo()
        click.echo("Types: " + ", ".join(sorted(valid_types)))
        return

    if activity_type not in valid_types:
        click.echo(
            f"Invalid type '{activity_type}'. Must be one of: "
            f"{', '.join(sorted(valid_types))}"
        )
        return

    if correct_last:
        try:
            latest = _latest_classification()
        except Exception as e:
            click.echo(f"Could not read latest activity: {e}")
            return
        if latest is None:
            click.echo("No recent activity found to correct.")
            return

        latest_slice, latest_rule = latest
        storage.add_correction(
            timestamp=latest_slice.start.isoformat(),
            app=latest_slice.primary_app,
            title=latest_slice.primary_title,
            original_type=latest_rule.activity_type,
            corrected_type=activity_type,
        )
        click.echo(
            f"✅ Correction stored: {latest_slice.primary_app} "
            f"({latest_rule.activity_type}) → {activity_type}"
        )
        click.echo("   (Stored for future rule generation. Past reports unchanged.)")
    elif time_range:
        try:
            start, end = _parse_time_range(time_range)
            items = _classifications_for_range(start, end)
        except Exception as e:
            click.echo(f"Could not read activity for {time_range}: {e}")
            return

        if not items:
            click.echo(f"No activity found in {time_range}.")
            return

        for s, r in items:
            storage.add_correction(
                timestamp=max(s.start, start).isoformat(),
                app=s.primary_app,
                title=s.primary_title,
                original_type=r.activity_type,
                corrected_type=activity_type,
            )

        click.echo(f"✅ {len(items)} correction(s) stored: {time_range} → {activity_type}")
        click.echo("   (Stored for future rule generation. Past reports unchanged.)")
    else:
        click.echo("Specify --last, --time, or --interactive. See `aw-coach correct --help`.")
        return

    # Show correction stats
    counts = storage.get_correction_counts()
    total_corrections = sum(counts.values())
    click.echo(f"📊 Total corrections: {total_corrections}")
    if total_corrections >= 3:
        click.echo("💡 Run `aw-coach rule-suggest` to generate rules from corrections.")


def _latest_classification():
    """Return the most recent non-AFK slice and current rule classification."""
    from aw_coach.collector import DataCollector
    from aw_coach.rules.engine import RuleEngine

    collector = DataCollector()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    slices = [s for s in collector.fetch_range(start, datetime.now()) if not s.is_afk]
    if not slices:
        return None

    latest_slice = max(slices, key=lambda s: s.end)
    engine = RuleEngine.with_all_rules()
    latest_rule = engine.classify(
        latest_slice.primary_app,
        latest_slice.primary_title,
        latest_slice.web_url,
    )
    return latest_slice, latest_rule


def _parse_time_range(time_range: str):
    """Parse a same-day HH:MM-HH:MM range, allowing ranges that cross midnight."""
    if "-" not in time_range:
        raise ValueError("expected format HH:MM-HH:MM")

    raw_start, raw_end = [part.strip() for part in time_range.split("-", 1)]
    start_time = datetime.strptime(raw_start, "%H:%M").time()
    end_time = datetime.strptime(raw_end, "%H:%M").time()

    start = datetime.combine(date_type.today(), start_time)
    end = datetime.combine(date_type.today(), end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _classifications_for_range(start: datetime, end: datetime):
    """Return non-AFK slices in a time range with their current rule classification."""
    from aw_coach.collector import DataCollector
    from aw_coach.rules.engine import RuleEngine

    collector = DataCollector()
    slices = [
        s
        for s in collector.fetch_range(start, end)
        if not s.is_afk and s.end > start and s.start < end
    ]
    if not slices:
        return []

    engine = RuleEngine.with_all_rules()
    return [
        (s, engine.classify(s.primary_app, s.primary_title, s.web_url))
        for s in slices
    ]


def _correct_review(
    config: Config,
    storage,
    valid_types: set,
    include_all: bool = False,
) -> None:
    """Review classifications and store user corrections."""
    from aw_coach.collector import DataCollector
    from aw_coach.correction import resolve_type
    from aw_coach.rules.engine import RuleEngine

    engine = RuleEngine.with_builtin_rules()
    try:
        collector = DataCollector()
    except Exception as e:
        click.echo(f"Error: {e}")
        return

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    slices = collector.fetch_range(today, datetime.now())
    if not slices:
        click.echo("No data today to review.")
        return

    review_items = []
    for s in slices:
        r = engine.classify(s.primary_app, s.primary_title, s.web_url)
        if include_all or r.confidence < 0.70 or r.activity_type == "unknown":
            review_items.append((s, r))

    if not review_items:
        click.echo("✅ All today's classifications are confident. Nothing to review.")
        return

    label = "items" if include_all else "low-confidence item(s)"
    click.echo(f"Found {len(review_items)} {label}:\n")
    corrected = 0

    for i, (s, r) in enumerate(review_items[:30], 1):
        start = s.start.strftime("%H:%M")
        end = s.end.strftime("%H:%M")
        click.echo(
            f"  [{i}] {start}-{end}  app={s.primary_app}  "
            f'title="{s.primary_title[:50]}"'
        )
        click.echo(
            f"       current: {r.activity_type} "
            f"({r.method}, confidence {r.confidence:.2f})"
        )
        response = click.prompt(
            "       looks correct? [Y]es / [n]o / [s]kip / TYPE",
            default="y",
        ).strip().lower()

        if response in {"y", "yes", ""}:
            continue
        if response in {"s", "skip"}:
            continue
        if response in {"n", "no"}:
            shortcuts = " ".join(f"[{t[0]}]{t[1:]}" for t in sorted(valid_types))
            response = click.prompt(f"       correct type? ({shortcuts})").strip().lower()

        chosen = resolve_type(response, valid_types)
        if chosen is None:
            click.echo(f"       invalid type '{response}', skipped.")
            continue

        storage.add_correction(
            timestamp=s.start.isoformat(),
            app=s.primary_app,
            title=s.primary_title,
            original_type=r.activity_type,
            corrected_type=chosen,
        )
        corrected += 1
        click.echo(f"       saved: {r.activity_type} → {chosen}")

    click.echo(f"\n✅ Saved {corrected} correction(s).")
    counts = storage.get_correction_counts()
    if sum(counts.values()) >= 3:
        click.echo("💡 Run `aw-coach rule-suggest` to generate rules.")


@main.command()
@click.option("--port", default=5601, show_default=True, help="Local server port")
@click.option("--open/--no-open", "open_browser", default=True, help="Open browser automatically")
@click.argument("date", default="today")
def serve(port: int, open_browser: bool, date: str) -> None:
    """Start an interactive local dashboard with correction API."""
    import time
    import webbrowser

    config = load_config()
    target = _parse_date(date)

    from aw_coach.web.server import InteractiveReportServer

    try:
        server = InteractiveReportServer(config, target, port=port)
        server.start()
    except Exception as e:
        click.echo(f"Could not start dashboard server: {e}", err=True)
        return

    click.echo("AI Coach Web 已启动")
    click.echo(f"访问地址: {server.url}")
    click.echo(f"纠错接口: POST {server.url}api/corrections")
    click.echo("提示: 点击时间线条目可修改分类，纠错会写入本地 SQLite。")
    click.echo("停止服务: Ctrl+C")
    if open_browser:
        webbrowser.open(server.url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        click.echo("\nAI Coach Web stopped.")


@main.command()
def calibrate() -> None:
    """Scan recent activity and classify unknown apps interactively."""
    config = load_config()
    _run_calibrate(config)


def _run_calibrate(config: Config) -> None:
    """Scan recent activity and classify unknown apps interactively."""
    import yaml

    from aw_coach.collector import DataCollector
    from aw_coach.rules.engine import RuleEngine

    engine = RuleEngine.with_all_rules()

    try:
        collector = DataCollector()
    except Exception as e:
        click.echo(f"Error: {e}")
        return

    # Fetch last 7 days
    week_ago = datetime.now() - timedelta(days=7)
    slices = collector.fetch_range(week_ago, datetime.now())
    if not slices:
        click.echo("No data available. Run ActivityWatch for a while first.")
        return

    # Find unknown apps (deduplicate)
    unknown_apps: dict = {}  # app -> total_duration
    for s in slices:
        if s.is_afk:
            continue
        r = engine.classify(s.primary_app, s.primary_title, s.web_url)
        if r.activity_type == "unknown":
            app = s.primary_app
            unknown_apps[app] = unknown_apps.get(app, 0) + s.duration

    if not unknown_apps:
        click.echo("✅ All apps recognized! No calibration needed.")
        return

    # Sort by time spent (most used unknown apps first)
    sorted_apps = sorted(unknown_apps.items(), key=lambda x: -x[1])

    valid_types = [
        "programming",
        "writing",
        "meeting",
        "research",
        "design",
        "entertainment",
        "admin",
        "social",
    ]
    type_shortcuts = {t[0]: t for t in valid_types}

    click.echo(f"Found {len(sorted_apps)} unrecognized app(s):\n")
    new_rules = []

    for app, duration in sorted_apps[:15]:
        hours = duration / 3600
        click.echo(f"  {app} ({hours:.1f}h total)")
        shortcuts = " ".join(f"[{t[0]}]{t[1:]}" for t in valid_types)
        response = click.prompt(
            f"    classify as? ({shortcuts} / [s]kip)", default="s"
        )

        if response.lower() == "s":
            continue

        chosen = type_shortcuts.get(response.lower(), response.lower())
        if chosen in valid_types:
            from aw_coach.rules.engine import DEFAULT_WEIGHTS
            new_rules.append({
                "name": f"user_{app.lower().replace(' ', '_')}",
                "match_apps": [app],
                "default_type": chosen,
                "confidence": 0.85,
                "weight": DEFAULT_WEIGHTS.get(chosen, 0.0),
            })

    if not new_rules:
        click.echo("\nNo apps classified. Run again anytime with `aw-coach calibrate`.")
        return

    # Write rules
    rules_dir = config.data_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    user_rules_path = rules_dir / "user.yml"

    existing_rules = []
    if user_rules_path.exists():
        data = yaml.safe_load(user_rules_path.read_text()) or {}
        existing_rules = data.get("rules", [])

    existing_rules.extend(new_rules)
    user_rules_path.write_text(
        yaml.dump({"rules": existing_rules}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    click.echo(f"\n✅ {len(new_rules)} rule(s) saved to {user_rules_path}")
    click.echo("Rules take effect immediately on next analysis.")


@main.command()
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", default=None, help="End date (default: today)")
def reclassify(from_date: str, to_date: Optional[str]) -> None:
    """Re-analyze historical data with current rules."""
    from aw_coach.analyzer import PatternAnalyzer
    from aw_coach.collector import DataCollector
    from aw_coach.report import ReportGenerator
    from aw_coach.rules.engine import RuleEngine

    config = load_config()
    engine = RuleEngine.with_all_rules()
    analyzer = PatternAnalyzer(config.analysis)
    reporter = ReportGenerator(config)

    start = date_type.fromisoformat(from_date)
    end = date_type.fromisoformat(to_date) if to_date else date_type.today()

    try:
        collector = DataCollector()
    except Exception as e:
        click.echo(f"Error: {e}")
        return

    click.echo(f"Reclassifying {from_date} → {end.isoformat()} with latest rules...")
    total_days = 0
    total_slices = 0

    current = start
    while current <= end:
        day_start = datetime.combine(current, datetime.min.time())
        day_end = datetime.combine(current, datetime.max.time())
        slices = collector.fetch_range(day_start, day_end)

        if slices:
            rules = [engine.classify(s.primary_app, s.primary_title, s.web_url) for s in slices]
            analysis = analyzer.analyze(slices, rules)

            # Save updated report
            reports_dir = config.reports_dir / "daily"
            reports_dir.mkdir(parents=True, exist_ok=True)
            report_text = reporter.generate_daily(current, analysis)
            report_path = reports_dir / f"{current.isoformat()}.md"
            report_path.write_text(report_text, encoding="utf-8")

            total_days += 1
            total_slices += len(slices)
            click.echo(
                f"  {current.isoformat()}: {len(slices)} slices, "
                f"focus={analysis.focus_score}"
            )

        current += timedelta(days=1)

    click.echo(f"\n✅ Reclassified {total_days} day(s), {total_slices} slices total.")
    click.echo("Reports updated in ~/.local/share/activitywatch/aw-watcher-ai-coach/reports/daily/")


@main.group()
def inbox() -> None:
    """View and manage agent inbox items."""


@inbox.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include dismissed items")
@click.option("--limit", default=20, show_default=True)
def inbox_list(show_all: bool, limit: int) -> None:
    """List inbox messages from the background agent."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)
    if show_all:
        open_items = storage.get_inbox_items(dismissed=False, limit=limit)
        done_items = storage.get_inbox_items(dismissed=True, limit=limit)
        items = sorted(
            open_items + done_items,
            key=lambda x: parse_stored_timestamp(x["timestamp"]),
            reverse=True,
        )[:limit]
    else:
        items = storage.get_inbox_items(dismissed=False, limit=limit)

    if not items:
        click.echo("Inbox 为空。")
        return

    for item in items:
        status = "dismissed" if item.get("dismissed") else "open"
        click.echo(
            f"[{item['id']}] {format_local_timestamp(item['timestamp'])} "
            f"| {item['signal_type']} "
            f"| sev={item['severity']:.1f} | {status}"
        )
        if item.get("evidence"):
            click.echo(f"    {item['evidence']}")
        if item.get("reason"):
            click.echo(f"    ({item['reason']})")


@inbox.command("dismiss")
@click.argument("item_id", type=int)
def inbox_dismiss(item_id: int) -> None:
    """Dismiss an inbox item by id."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)
    item = storage.get_inbox_item(item_id)
    if item is None:
        raise click.ClickException(f"Inbox item {item_id} not found")
    storage.dismiss_inbox_item(item_id)
    click.echo(f"已忽略 inbox #{item_id}")


@inbox.command("accept")
@click.argument("item_id", type=int)
def inbox_accept(item_id: int) -> None:
    """Acknowledge an inbox item (dismiss after logging)."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)
    item = storage.get_inbox_item(item_id)
    if item is None:
        raise click.ClickException(f"Inbox item {item_id} not found")
    storage.dismiss_inbox_item(item_id)
    click.echo(f"已确认 inbox #{item_id}: {item.get('evidence', '')}")


@main.group()
def task() -> None:
    """Task perception: list, confirm, and set daily goals."""


@task.command("list")
@click.option("--date", "target_date", default="today")
def task_list(target_date: str) -> None:
    """List task sessions for a day."""
    from aw_coach.storage import Storage

    config = load_config()
    target = _parse_date(target_date)
    storage = Storage(config.db_path)
    rows = storage.get_task_daily_summary(target.isoformat())
    if not rows:
        click.echo(f"{target.isoformat()} 暂无任务汇总。")
        return
    click.echo(f"任务分布 — {target.isoformat()}")
    for row in rows:
        hours = row["total_sec"] / 3600
        click.echo(f"  {row['label']:<28} {hours:>5.1f}h  ({row['task_id']})")


@task.command("confirm")
@click.option("--label", default=None, help="Override task label")
def task_confirm(label: Optional[str]) -> None:
    """Confirm the current task from live semantic state."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)
    raw = storage.get_scheduler_state("semantic_state")
    if not raw:
        raise click.ClickException("无实时语义状态。请先运行 aw-coach-daemon。")
    payload = json.loads(raw)
    state = payload.get("state", {})
    task_id = state.get("task_id") or "user:confirmed"
    task_label = label or state.get("task_label") or click.prompt("任务名称")
    config.tasks.user_task_id = task_id
    config.tasks.user_task_label = task_label
    click.echo(f"已记录任务确认: {task_label} ({task_id})")
    click.echo("提示: 持久化请写入 config: aw-coach config set tasks.user_task_label \"...\"")


@task.command("set")
@click.argument("goal")
def task_set(goal: str) -> None:
    """Set today's primary task goal (stored in config)."""
    click.echo(f"今日目标: {goal}")
    escaped = goal.replace('"', '\\"')
    click.echo(f'请运行: aw-coach config set tasks.user_task_label "{escaped}"')


@task.command("review")
@click.option("--date", "target_date", default="today")
@click.option("--sample", default=5, show_default=True)
def task_review(target_date: str, sample: int) -> None:
    """Sample task sessions for manual accuracy review."""
    from aw_coach.storage import Storage

    config = load_config()
    target = _parse_date(target_date)
    storage = Storage(config.db_path)
    sessions = storage.get_task_sessions_for_day(target.isoformat())
    if not sessions:
        click.echo("无可抽查的任务会话。")
        return
    import random

    picked = random.sample(sessions, min(sample, len(sessions)))
    correct = 0
    for row in picked:
        hours = row["accumulated_sec"] / 3600
        click.echo(f"\n[{row['id']}] {row['label']} — {hours:.1f}h ({row['intent']})")
        if click.confirm("归属是否正确?", default=True):
            correct += 1
    rate = correct / len(picked) * 100
    click.echo(f"\n准确率抽样: {correct}/{len(picked)} ({rate:.0f}%)")
