"""Tests for context_parser.TitleParser."""

from __future__ import annotations

import pytest

from aw_coach.context_parser import TitleParser, WindowContext


class TestTitleParserIDE:
    """IDE/editor window titles."""

    def test_vscode_simple(self):
        p = TitleParser()
        ctx = p.parse("Code", "main.py - myproject")
        assert ctx.project == "myproject"
        assert ctx.filename == "main.py"
        assert ctx.language == "python"
        assert ctx.action_hint == "editing"

    def test_vscode_cpp(self):
        p = TitleParser()
        ctx = p.parse("Code", "OptPoseTopicTask.cpp - x_system")
        assert ctx.project == "x_system"
        assert ctx.filename == "OptPoseTopicTask.cpp"
        assert ctx.language == "cpp"

    def test_vscode_chinese_project(self):
        p = TitleParser()
        ctx = p.parse("Code", "README.md — 我的项目")
        assert ctx.project == "我的项目"
        assert ctx.filename == "README.md"
        assert ctx.language == "markdown"

    def test_cursor_agent(self):
        p = TitleParser()
        ctx = p.parse("Cursor", "composer - aw-coach")
        assert ctx.project == "aw-coach"

    def test_jetbrains(self):
        p = TitleParser()
        ctx = p.parse("idea64", "MainActivity.java - android_app")
        assert ctx.project == "android_app"
        assert ctx.filename == "MainActivity.java"
        assert ctx.language == "java"

    def test_vim(self):
        p = TitleParser()
        ctx = p.parse("nvim", "config.lua")
        assert ctx.filename == "config.lua"
        assert ctx.language == "lua"
        assert ctx.action_hint == "editing"

    def test_vscode_no_project(self):
        p = TitleParser()
        ctx = p.parse("Code", "settings.json")
        assert ctx.filename == "settings.json"
        # No separator → no project extracted
        assert ctx.project is None

    def test_vscode_noise_fallback(self):
        """When last part is 'Visual Studio Code', fall back to earlier part."""
        p = TitleParser()
        ctx = p.parse("Code", "main.py - myproject - Visual Studio Code")
        assert ctx.project == "myproject"


class TestTitleParserBrowser:
    """Browser window titles."""

    def test_github_repo(self):
        p = TitleParser()
        ctx = p.parse("chrome", "x_system - GitHub", url="https://github.com/user/x_system")
        assert ctx.site == "github"
        assert ctx.project == "x_system"

    def test_stackoverflow(self):
        p = TitleParser()
        ctx = p.parse(
            "firefox",
            "python - How to use asyncio - Stack Overflow",
            url="https://stackoverflow.com/questions/123456",
        )
        assert ctx.site == "stackoverflow"

    def test_jira(self):
        p = TitleParser()
        ctx = p.parse(
            "chrome",
            "[JIRA-123] Fix login bug - Jira",
            url="https://company.atlassian.net/browse/JIRA-123",
        )
        assert ctx.site == "jira"

    def test_notion(self):
        p = TitleParser()
        ctx = p.parse("chrome", "Design Doc - notion", url="https://notion.so/")
        assert ctx.site == "notion"

    def test_bilibili(self):
        p = TitleParser()
        ctx = p.parse("firefox", "【教程】ROS2入门 - 哔哩哔哩", url="https://www.bilibili.com/video/BV123")
        assert ctx.site == "bilibili"

    def test_browser_no_url(self):
        """Browser without URL (web watcher missing) should still detect site from title."""
        p = TitleParser()
        ctx = p.parse("chrome", "GitHub - x_system", url=None)
        assert ctx.site == "github"

    def test_browser_title_no_pseudo_project(self):
        """Browser titles must not yield app names as projects without URL."""
        p = TitleParser()
        ctx = p.parse("firefox_firefox", "Kimi — Mozilla Firefox", url=None)
        assert ctx.project is None
        assert ctx.site is None

    def test_browser_webmail_title_not_ssh_prompt(self):
        """user@mail.com: subject in a browser is not a terminal prompt."""
        p = TitleParser()
        ctx = p.parse("firefox", "yz@gmail.com: Re: 会议纪要 — Mozilla Firefox", url=None)
        assert ctx.project is None
        assert ctx.remote_host is None


class TestTitleParserTerminal:
    """Terminal window titles."""

    def test_ssh_tilde_no_project(self):
        p = TitleParser()
        ctx = p.parse("gnome-terminal", "sunrise@ubuntu: ~")
        assert ctx.project is None
        assert ctx.remote_host == "ubuntu"

    def test_ssh_remote_cwd_project(self, monkeypatch):
        monkeypatch.setattr(
            "aw_coach.context_parser.socket.gethostname", lambda: "local-laptop"
        )
        p = TitleParser()
        ctx = p.parse("gnome-terminal", "sunrise@ubuntu: ~/x_system")
        assert ctx.project == "x_system@ubuntu"
        assert ctx.remote_host == "ubuntu"

    def test_ssh_local_cwd_project(self, monkeypatch):
        monkeypatch.setattr(
            "aw_coach.context_parser.socket.gethostname",
            lambda: "yz-Legion-Y7000-IRX9",
        )
        p = TitleParser()
        ctx = p.parse("gnome-terminal", "yz@yz-Legion-Y7000-IRX9: ~/下载")
        assert ctx.project == "下载"

    def test_terminal_path(self):
        p = TitleParser()
        ctx = p.parse("gnome-terminal", "~/projects/x_system")
        assert ctx.project == "x_system"

    def test_terminal_ros_workspace(self):
        p = TitleParser()
        ctx = p.parse("alacritty", "~/ros2_ws/src/x_system")
        assert ctx.project == "x_system"

    def test_git_commit_no_project(self):
        """git commit -am 'wip' should NOT produce a project name from flags."""
        p = TitleParser()
        ctx = p.parse("alacritty", "git commit -am 'wip'")
        assert ctx.project is None
        assert ctx.action_hint == "committing"

    def test_pytest_no_project(self):
        """pytest -v should NOT produce project='v'."""
        p = TitleParser()
        ctx = p.parse("alacritty", "pytest -v")
        assert ctx.project is None
        assert ctx.action_hint == "testing"

    def test_cargo_build_no_project(self):
        """cargo build should not be split into project names."""
        p = TitleParser()
        ctx = p.parse("alacritty", "cargo build --release")
        assert ctx.project is None

    def test_docker_run_no_project(self):
        p = TitleParser()
        ctx = p.parse("alacritty", "docker run -it ubuntu")
        assert ctx.project is None

    def test_npm_install_no_project(self):
        p = TitleParser()
        ctx = p.parse("alacritty", "npm install")
        assert ctx.project is None
        assert ctx.action_hint is None


class TestTitleParserActionHints:
    """Action inference from title keywords."""

    def test_debugging(self):
        p = TitleParser()
        ctx = p.parse("Code", "debug_session.log - myproject")
        assert ctx.action_hint == "debugging"

    def test_testing(self):
        p = TitleParser()
        ctx = p.parse("Code", "test_main.py - myproject")
        assert ctx.action_hint == "testing"

    def test_building(self):
        p = TitleParser()
        ctx = p.parse("Code", "CMakeLists.txt - myproject")
        assert ctx.action_hint == "building"

    def test_reviewing(self):
        p = TitleParser()
        ctx = p.parse("chrome", "PR #42: Fix bug - x_system - GitHub")
        assert ctx.action_hint == "reviewing"

    def test_documenting(self):
        p = TitleParser()
        ctx = p.parse("Code", "doc.md - myproject")
        assert ctx.action_hint == "documenting"

    def test_no_action_editing_fallback(self):
        p = TitleParser()
        ctx = p.parse("Code", "utils.py - myproject")
        assert ctx.action_hint == "editing"

    def test_no_action_browser_no_keywords(self):
        p = TitleParser()
        ctx = p.parse("chrome", "Hacker News", url=None)
        assert ctx.action_hint is None


class TestTitleParserEdgeCases:
    """Edge cases and robustness."""

    def test_empty_title(self):
        p = TitleParser()
        ctx = p.parse("unknown", "")
        assert ctx.project is None
        assert ctx.filename is None
        assert ctx.language is None

    def test_domain_lookalike_not_filename(self):
        """youtube.com should NOT be treated as a filename."""
        p = TitleParser()
        ctx = p.parse("chrome", "youtube.com", url="https://youtube.com")
        assert ctx.filename is None
        assert ctx.site == "youtube"

    def test_chinese_debug(self):
        p = TitleParser()
        ctx = p.parse("Code", "调试主程序.py - 我的项目")
        assert ctx.action_hint == "debugging"
        assert ctx.filename == "调试主程序.py"

    def test_multiple_extensions(self):
        """Title with something.tar.gz — tar.gz is not a valid lang mapping."""
        p = TitleParser()
        ctx = p.parse("Code", "backup.tar.gz - project")
        # Should still extract *something*; tar.gz extension mapping absent
        assert ctx.filename == "backup.tar.gz"
        assert ctx.language is None

    def test_gitlab_url(self):
        p = TitleParser()
        ctx = p.parse("chrome", "Merge Request", url="https://gitlab.com/team/ros_nav/-/merge_requests/5")
        assert ctx.site == "gitlab"
        assert ctx.project == "ros_nav"

    def test_no_separator_but_filename(self):
        p = TitleParser()
        ctx = p.parse("gedit", "process.yaml")
        assert ctx.filename == "process.yaml"
        assert ctx.language == "yaml"

    def test_noise_word_in_project_position(self):
        """If the split puts a noise word as project, we should skip it."""
        p = TitleParser()
        ctx = p.parse("Code", "main.py - New Tab")
        # "New Tab" is noise → should not be project
        # But there's no earlier part to fall back to
        assert ctx.project is None or ctx.project == "main.py"

    def test_very_long_title(self):
        p = TitleParser()
        title = "a" * 500 + " - myproject"
        ctx = p.parse("Code", title)
        assert ctx.project == "myproject"

    def test_window_context_frozen(self):
        """WindowContext should be immutable."""
        ctx = WindowContext(app="Code", raw_title="test")
        with pytest.raises(AttributeError):
            ctx.project = "x"


class TestTitleParserBatch:
    """Batch regression: run a table of real-world titles."""

    BATCH = [
        # (app, title, url, expected_project, expected_filename, expected_lang, expected_site)
        ("Code", "main.py - x_system", None, "x_system", "main.py", "python", None),
        ("Code", "executor.cpp - ros_nav", None, "ros_nav", "executor.cpp", "cpp", None),
        ("Code", "README.md — aw-coach", None, "aw-coach", "README.md", "markdown", None),
        ("Cursor", "main.rs - myproject", None, "myproject", "main.rs", "rust", None),
        (
            "chrome",
            "x_system - GitHub",
            "https://github.com/user/x_system",
            "x_system",
            None,
            None,
            "github",
        ),
        (
            "firefox",
            "ROS2 executor - CSDN",
            "https://blog.csdn.net/article",
            None,
            None,
            None,
            "csdn",
        ),
        (
            "chrome",
            "[JIRA-123] Fix bug",
            "https://company.atlassian.net/browse/JIRA-123",
            None,
            None,
            None,
            "jira",
        ),
        ("gnome-terminal", "~/projects/x_system", None, "x_system", None, None, None),
        ("nvim", "init.lua", None, None, "init.lua", "lua", None),
        (
            "Code",
            "process.yaml - x2/process_config/ats_agent",
            None,
            "x2/process_config/ats_agent",
            "process.yaml",
            "yaml",
            None,
        ),
    ]

    def test_batch(self):
        p = TitleParser()
        for app, title, url, exp_proj, exp_file, exp_lang, exp_site in self.BATCH:
            ctx = p.parse(app, title, url)
            assert ctx.project == exp_proj, (
                f"project mismatch for '{title}': got {ctx.project}, expected {exp_proj}"
            )
            assert ctx.filename == exp_file, (
                f"filename mismatch for '{title}': got {ctx.filename}, expected {exp_file}"
            )
            assert ctx.language == exp_lang, (
                f"language mismatch for '{title}': got {ctx.language}, expected {exp_lang}"
            )
            assert ctx.site == exp_site, (
                f"site mismatch for '{title}': got {ctx.site}, expected {exp_site}"
            )
