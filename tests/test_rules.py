"""Tests for rule engine - classification logic."""

from importlib import resources

import pytest

import aw_coach
from aw_coach.rules.engine import RuleEngine, RuleResult


@pytest.fixture
def engine():
    """Load engine with builtin rules."""
    return RuleEngine.with_builtin_rules()


def test_builtin_rules_are_package_data():
    builtin_dir = resources.files(aw_coach).joinpath("rules", "builtin")
    assert builtin_dir.joinpath("global.yml").is_file()
    assert builtin_dir.joinpath("cn.yml").is_file()


class TestAppExactMatch:
    def test_vscode(self, engine):
        r = engine.classify("Code", "main.py - myproject", None)
        assert r.activity_type == "programming"
        assert r.confidence >= 0.85
        assert r.method == "rule_app_exact"

    def test_vscode_insiders(self, engine):
        r = engine.classify("Code - Insiders", "test.ts", None)
        assert r.activity_type == "programming"

    def test_cursor(self, engine):
        r = engine.classify("Cursor", "app.tsx - frontend", None)
        assert r.activity_type == "programming"

    def test_terminal(self, engine):
        r = engine.classify("gnome-terminal", "bash", None)
        assert r.activity_type == "programming"

    def test_intellij(self, engine):
        r = engine.classify("idea", "Main.java - myapp", None)
        assert r.activity_type == "programming"

    def test_builtin_rule_coverage_reaches_top_100_apps(self, engine):
        apps = {app.lower() for rule in engine.rules for app in rule.match_apps}
        assert len(apps) >= 100


class TestBrowserSubRules:
    def test_youtube_entertainment(self, engine):
        r = engine.classify("chrome", "YouTube - Funny cats", "https://youtube.com/watch")
        assert r.activity_type == "entertainment"
        assert r.confidence >= 0.90

    def test_github_programming(self, engine):
        r = engine.classify("firefox", "Pull Request #123 - GitHub", "https://github.com/org/repo")
        assert r.activity_type == "programming"
        assert r.confidence >= 0.75

    def test_stackoverflow_research(self, engine):
        r = engine.classify("chrome", "python - How to...", "https://stackoverflow.com/q/123")
        assert r.activity_type == "research"
        assert r.confidence >= 0.85

    def test_bilibili_entertainment(self, engine):
        r = engine.classify("chrome", "bilibili - video", "https://bilibili.com")
        assert r.activity_type == "entertainment"

    def test_generic_browser_low_confidence(self, engine):
        r = engine.classify("chrome", "Some random page", "https://example.com")
        assert r.confidence < 0.85

    def test_jira_admin_with_cn_rules_loaded(self, engine):
        """P2-1: cn.yml should not overwrite global browser rules for Jira."""
        r = engine.classify("chrome", "PROJ-123 - Jira", "https://jira.atlassian.com/browse")
        assert r.activity_type == "admin"

    def test_csdn_programming_from_cn_rules(self, engine):
        """cn.yml sub_rules for CSDN should be merged into browser."""
        r = engine.classify("chrome", "Python教程 - CSDN", "https://csdn.net/article")
        assert r.activity_type == "programming"

    def test_copilot_chat_browser_is_ai_assisted(self, engine):
        r = engine.classify(
            "chrome",
            "GitHub Copilot Chat",
            "https://github.com/copilot",
        )
        assert r.activity_type == "ai_assisted"
        assert r.confidence >= 0.85

    def test_chatgpt_ai_assisted(self, engine):
        r = engine.classify("chrome", "ChatGPT", "https://chatgpt.com/c/abc")
        assert r.activity_type == "ai_assisted"
        assert r.confidence >= 0.85

    def test_claude_ai_assisted(self, engine):
        r = engine.classify("firefox", "Claude", "https://claude.ai/chat")
        assert r.activity_type == "ai_assisted"
        assert r.confidence >= 0.85

    def test_docs_rs_research(self, engine):
        r = engine.classify("chrome", "tokio - Rust", "https://docs.rs/tokio/latest")
        assert r.activity_type == "research"
        assert r.confidence >= 0.85


class TestChineseApps:
    def test_lark_feishu(self, engine):
        r = engine.classify("Lark", "飞书会议 - Sprint Planning", None)
        assert r.activity_type == "meeting"

    def test_dingtalk(self, engine):
        r = engine.classify("DingTalk", "工作通知", None)
        assert r.activity_type == "meeting"

    def test_wechat(self, engine):
        r = engine.classify("WeChat", "聊天窗口", None)
        assert r.activity_type == "social"

    def test_wemeet(self, engine):
        r = engine.classify("wemeet", "腾讯会议", None)
        assert r.activity_type == "meeting"

    def test_wps(self, engine):
        r = engine.classify("wps", "文档1.docx - WPS", None)
        assert r.activity_type == "writing"


class TestCommunicationApps:
    def test_zoom_meeting(self, engine):
        r = engine.classify("zoom", "Zoom Meeting", None)
        assert r.activity_type == "meeting"

    def test_slack(self, engine):
        r = engine.classify("Slack", "#general - Slack", None)
        assert r.activity_type == "social"

    def test_discord(self, engine):
        r = engine.classify("Discord", "Server - Channel", None)
        assert r.activity_type == "social"


class TestAICodingAgentRules:
    def test_claude_code_app(self, engine):
        r = engine.classify("Claude Code", "project session", None)
        assert r.activity_type == "programming"
        assert r.confidence >= 0.90

    def test_codex_terminal_session(self, engine):
        r = engine.classify("gnome-terminal", "codex --ask", None)
        assert r.activity_type == "programming"
        assert r.confidence >= 0.90

    def test_aider_app(self, engine):
        r = engine.classify("aider", "main.py", None)
        assert r.activity_type == "programming"
        assert r.confidence >= 0.90

    def test_cursor_agent_title(self, engine):
        r = engine.classify("Cursor", "Agent applying changes - repo", None)
        assert r.activity_type == "programming"
        assert r.confidence >= 0.90


class TestDesignApps:
    def test_figma(self, engine):
        r = engine.classify("Figma", "My Design", None)
        assert r.activity_type == "design"


class TestSensitiveApps:
    def test_1password(self, engine):
        r = engine.classify("1Password", "Vault", None)
        assert r.activity_type == "sensitive"
        assert r.confidence == 1.0

    def test_keepassxc(self, engine):
        r = engine.classify("KeePassXC", "Database", None)
        assert r.activity_type == "sensitive"


class TestUnknownApps:
    def test_unknown_app(self, engine):
        r = engine.classify("random-binary-xyz", "MainWindow", None)
        assert r.activity_type == "unknown"
        assert r.confidence == 0.0
        assert r.method == "rule_miss"

    def test_case_insensitive(self, engine):
        r = engine.classify("CODE", "main.py", None)
        assert r.activity_type == "programming"


class TestRuleResult:
    def test_fields(self):
        r = RuleResult(
            activity_type="programming",
            confidence=0.9,
            method="rule_app_exact",
            rule_name="vscode",
        )
        assert r.activity_type == "programming"
        assert r.rule_name == "vscode"
