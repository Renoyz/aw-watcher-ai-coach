"""aw-coach CLI entry point."""

from __future__ import annotations

import json
import logging
import shutil
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

    reporter = ReportGenerator(config)
    report_text = reporter.generate_daily(target, analysis, use_ai=use_ai)

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
def rule_suggest() -> None:
    """Generate rule suggestions from correction history."""
    import yaml

    from aw_coach.rules.engine import RuleEngine
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)
    engine = RuleEngine.with_builtin_rules()

    counts = storage.get_correction_counts()
    if not counts:
        click.echo("No corrections stored yet. Use `aw-coach correct` to build samples.")
        return

    suggestions = []
    for (app, ctype), count in sorted(counts.items(), key=lambda x: -x[1]):
        if count >= 3 and not engine.has_confident_rule(app):
            confidence = min(0.70 + count * 0.05, 0.95)
            suggestions.append((app, ctype, count, confidence))

    if not suggestions:
        click.echo("Not enough correction patterns yet (need 3+ for same app+type).")
        click.echo(f"Current corrections: {sum(counts.values())} total.")
        return

    click.echo("Based on your corrections, suggested rules:\n")
    for i, (app, ctype, count, conf) in enumerate(suggestions, 1):
        click.echo(f"  [{i}] app=\"{app}\" -> {ctype} (corrected {count}x, confidence {conf:.2f})")

    click.echo()
    choice = click.prompt("Accept? [a]ll / numbers (e.g. 1,2) / [n]one", default="a")

    if choice.lower() == "n":
        click.echo("No rules added.")
        return

    if choice.lower() == "a":
        accepted = suggestions
    else:
        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
        accepted = [suggestions[i] for i in indices if 0 <= i < len(suggestions)]

    if not accepted:
        click.echo("No valid selection.")
        return

    rules_dir = config.data_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    user_rules_path = rules_dir / "user.yml"

    existing_rules = []
    if user_rules_path.exists():
        data = yaml.safe_load(user_rules_path.read_text()) or {}
        existing_rules = data.get("rules", [])

    for app, ctype, count, conf in accepted:
        existing_rules.append({
            "name": f"user_{app}",
            "match_apps": [app],
            "default_type": ctype,
            "confidence": round(conf, 2),
        })

    user_rules_path.write_text(
        yaml.dump({"rules": existing_rules}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    click.echo(f"\n✅ {len(accepted)} rule(s) added to {user_rules_path}")
    click.echo("Rules take effect on next analysis cycle.")


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
@click.argument("activity_type", required=False)
def correct(
    correct_last: bool,
    time_range: Optional[str],
    interactive: bool,
    activity_type: Optional[str],
) -> None:
    """Correct AI classification results."""
    from aw_coach.storage import Storage

    config = load_config()
    storage = Storage(config.db_path)

    valid_types = {
        "programming",
        "writing",
        "meeting",
        "research",
        "design",
        "entertainment",
        "admin",
        "social",
    }

    if interactive:
        _correct_interactive(config, storage, valid_types)
        return

    if not activity_type:
        click.echo("Usage: aw-coach correct --last <type>")
        click.echo("       aw-coach correct --time 14:00-15:00 <type>")
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


def _correct_interactive(config: Config, storage, valid_types: set) -> None:
    """Interactive review of low-confidence classifications."""
    from aw_coach.collector import DataCollector
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

    uncertain = []
    for s in slices:
        r = engine.classify(s.primary_app, s.primary_title, s.web_url)
        if r.confidence < 0.70 or r.activity_type == "unknown":
            uncertain.append((s, r))

    if not uncertain:
        click.echo("✅ All today's classifications are confident. Nothing to review.")
        return

    click.echo(f"Found {len(uncertain)} low-confidence items:\n")
    corrected = 0
    type_shortcuts = {t[0]: t for t in sorted(valid_types)}

    for i, (s, r) in enumerate(uncertain[:20], 1):
        start = s.start.strftime("%H:%M")
        end = s.end.strftime("%H:%M")
        click.echo(
            f"  [{i}] {start}-{end}  app={s.primary_app}  "
            f'title="{s.primary_title[:50]}"'
        )
        click.echo(f"       current: {r.activity_type} (confidence {r.confidence:.2f})")

        shortcuts = " ".join(f"[{t[0]}]{t[1:]}" for t in sorted(valid_types))
        response = click.prompt(
            f"       correct type? ({shortcuts} / [s]kip)",
            default="s",
        )

        if response.lower() == "s":
            continue

        chosen = type_shortcuts.get(response.lower(), response.lower())
        if chosen in valid_types:
            storage.add_correction(
                timestamp=s.start.isoformat(),
                app=s.primary_app,
                title=s.primary_title,
                original_type=r.activity_type,
                corrected_type=chosen,
            )
            corrected += 1

    click.echo(f"\n✅ Saved {corrected} correction(s).")
    counts = storage.get_correction_counts()
    if sum(counts.values()) >= 3:
        click.echo("💡 Run `aw-coach rule-suggest` to generate rules.")


@main.command("open")
def open_dashboard() -> None:
    """Open HTML report dashboard in browser."""
    import time
    import webbrowser

    config = load_config()
    target = date_type.today()

    try:
        analysis = _get_analysis(target, config)
    except SystemExit:
        return

    if analysis is None:
        click.echo("No data available to generate dashboard.")
        return

    from aw_coach.report import generate_html_dashboard
    from aw_coach.webserver import ReportServer

    html_path = generate_html_dashboard(config, target, analysis)
    server = ReportServer(html_path.parent)
    server.start()
    click.echo(f"Dashboard server: {server.dashboard_url}")
    click.echo("Press Ctrl+C to stop.")
    webbrowser.open(server.dashboard_url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        click.echo("\nDashboard server stopped.")


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
