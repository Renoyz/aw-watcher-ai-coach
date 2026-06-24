"""Window title and URL semantic parsing — extract project, file, language, site, action."""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse


@dataclass(frozen=True)
class WindowContext:
    """Structured context extracted from a window title + URL."""

    app: str
    raw_title: str
    project: Optional[str] = None
    filename: Optional[str] = None
    language: Optional[str] = None
    site: Optional[str] = None
    action_hint: Optional[str] = None
    remote_host: Optional[str] = None


class TitleParser:
    """Parse window titles and URLs into structured WindowContext."""

    # IDE/editor title separators: "file - project" or "file — project"
    _IDE_SEPARATORS = re.compile(r"[-—|·•]")

    # File extension → programming language
    _EXT_LANG = {
        ".py": "python",
        ".rs": "rust",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".c": "c",
        ".h": "cpp",
        ".hpp": "cpp",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".java": "java",
        ".kt": "kotlin",
        ".md": "markdown",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".fish": "shell",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".sql": "sql",
        ".dart": "dart",
        ".swift": "swift",
        ".scala": "scala",
        ".r": "r",
        ".m": "matlab",
        ".lua": "lua",
        ".vim": "vim",
        ".dockerfile": "dockerfile",
        ".cmake": "cmake",
        ".proto": "protobuf",
    }
    _SPECIAL_FILENAMES = {"cmakelists.txt", "makefile", "dockerfile"}

    # Browser domain → site name
    _SITE_PATTERNS = {
        "github.com": "github",
        "gitlab.com": "gitlab",
        "stackoverflow.com": "stackoverflow",
        "stackexchange.com": "stackexchange",
        "juejin.cn": "juejin",
        "csdn.net": "csdn",
        "segmentfault.com": "segmentfault",
        "zhihu.com": "zhihu",
        "bilibili.com": "bilibili",
        "youtube.com": "youtube",
        "reddit.com": "reddit",
        "twitter.com": "twitter",
        "x.com": "twitter",
        "jira.atlassian.com": "jira",
        "confluence.atlassian.com": "confluence",
        "notion.so": "notion",
        "linear.app": "linear",
        "asana.com": "asana",
        "trello.com": "trello",
        "figma.com": "figma",
        "google.com": "google.com",
        "docs.google.com": "google_docs",
        "mail.google.com": "gmail",
        "chatgpt.com": "chatgpt",
        "claude.ai": "claude",
        "gemini.google.com": "gemini",
        "kimi.com": "kimi",
        "code.tars-ai.com": "tars_code",
        "xrobot-jenkins.tars-ai.com": "jenkins",
        "tarsrobot.feishu.cn": "feishu_docs",
    }

    # Title/URL keywords → action hint
    _ACTION_KEYWORDS = {
        "debug": "debugging",
        "调试": "debugging",
        "test": "testing",
        "pytest": "testing",
        "jest": "testing",
        "mocha": "testing",
        "unittest": "testing",
        "测试": "testing",
        "build": "building",
        "编译": "building",
        "cmake": "building",
        "make": "building",
        "doc": "documenting",
        "文档": "documenting",
        "review": "reviewing",
        "评审": "reviewing",
        "pr": "reviewing",
        "pull request": "reviewing",
        "merge": "reviewing",
        "commit": "committing",
        "push": "committing",
        "deploy": "deploying",
        "发布": "deploying",
        "ci": "building",
        "pipeline": "building",
    }

    # Browser apps (lowercase)
    _BROWSER_APPS = frozenset(
        [
            "chrome",
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "firefox",
            "firefox_firefox",
            "safari",
            "edge",
            "arc",
            "brave",
            "vivaldi",
            "opera",
            "librewolf",
            "firefox-esr",
        ]
    )

    # IDE/editor apps (lowercase)
    _IDE_APPS = frozenset(
        [
            "code",
            "code - insiders",
            "vscodium",
            "cursor",
            "idea",
            "idea64",
            "pycharm",
            "webstorm",
            "goland",
            "clion",
            "rustrover",
            "android studio",
            "xcode",
            "vim",
            "nvim",
            "neovim",
            "gvim",
            "sublime_text",
            "subl",
            "emacs",
            "emacs-gtk",
            "hbuilderx",
            "hbuilder",
        ]
    )

    def parse(self, app: str, title: str, url: Optional[str] = None) -> WindowContext:
        """Parse a window title (+ optional URL) into structured context."""
        ctx = WindowContext(app=app, raw_title=title)

        # 1. Extract filename and language
        filename = self._extract_filename(title)
        language = self._detect_language(filename)

        # 2. Extract project name
        project = self._extract_project(title, url, app=app)
        remote_host = None
        if not self._is_browser(app):
            prompt = self._parse_terminal_prompt(title)
            if prompt is not None:
                remote_host = prompt[1]

        # 3. Detect site (browser)
        site = None
        if url:
            site = self._detect_site(url)
        if site is None and self._is_browser(app):
            site = self._detect_site_from_title(title)

        # 4. Infer action
        action = self._infer_action(app, title, filename)

        # Build frozen dataclass via object.__setattr__ (dataclass is frozen)
        object.__setattr__(ctx, "filename", filename)
        object.__setattr__(ctx, "language", language)
        object.__setattr__(ctx, "project", project)
        object.__setattr__(ctx, "site", site)
        object.__setattr__(ctx, "action_hint", action)
        object.__setattr__(ctx, "remote_host", remote_host)

        return ctx

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_filename(cls, title: str) -> Optional[str]:
        """Look for something like main.py, ProcessConfig.cpp, README.md."""
        # Match word chars + optional dots in name, then a dot, then 1-6 letter extension
        match = re.search(r"[\w\-\._]+\.[a-zA-Z0-9]{1,6}\b", title)
        if match:
            candidate = match.group(0)
            if candidate.lower() in cls._SPECIAL_FILENAMES:
                return candidate
            # Reject if it looks like a domain (e.g. github.com, youtube.com)
            if re.match(r"^[a-zA-Z0-9\-]+\.[a-zA-Z]{2,6}$", candidate):
                # Could be a domain; check if it's a known file extension
                ext = "." + candidate.rsplit(".", 1)[-1].lower()
                if ext not in cls._EXT_LANG:
                    return None
            return candidate
        return None

    @classmethod
    def _detect_language(cls, filename: Optional[str]) -> Optional[str]:
        if not filename:
            return None
        parts = filename.rsplit(".", 1)
        if len(parts) < 2:
            return None
        ext = "." + parts[-1].lower()
        return cls._EXT_LANG.get(ext)

    # Common IDE/app suffixes to strip before extracting project name
    _IDE_SUFFIXES = re.compile(
        r"\s+[-—|·•]\s*(?:Visual Studio Code|VS Code|Code(?: - Insiders)?|VSCodium|Cursor|"
        r"IntelliJ IDEA|PyCharm|WebStorm|GoLand|CLion|RustRover|Android Studio|Xcode|"
        r"JetBrains|NVIM?|Vim|Emacs|Sublime Text|Sublime|Gedit|New Tab|新标签页)$",
        re.IGNORECASE,
    )

    # Terminal command prefixes: when title starts with these, it's a command line,
    # not a "file - project" pattern. Skip naive split to avoid interpreting flags
    # (e.g. "git commit -am 'wip'", "pytest -v") as project names.
    _TERMINAL_PROMPT = re.compile(r"^(\w[\w.-]*)@([\w.-]+):\s*(.*)$")

    _BROWSER_TITLE_SUFFIXES = re.compile(
        r"\s+[-—|·•]\s*(?:Mozilla Firefox|Google Chrome|Chromium|Microsoft Edge|"
        r"Safari|Brave|Arc|Vivaldi|Opera|LibreWolf)$",
        re.IGNORECASE,
    )

    _TERMINAL_COMMAND_PREFIXES = frozenset({
        "git ", "pytest", "cargo ", "npm ", "yarn ", "pnpm ", "docker ", "kubectl ",
        "make", "cmake", "python ", "python3 ", "node ", "ruby ", "go ", "rust ",
        "java ", "javac ", "gcc ", "g++ ", "clang ", "bash ", "sh ", "zsh ",
        "ssh ", "scp ", "rsync ", "tar ", "zip ", "unzip ", "curl ", "wget ",
        "ls ", "cd ", "cat ", "less ", "more ", "tail ", "head ", "grep ",
        "find ", "awk ", "sed ", "sort ", "uniq ", "diff ", "patch ", "vim ",
        "nvim ", "emacs ", "nano ", "code ", "htop", "top", "ps ", "kill ",
    })

    @classmethod
    def _parse_terminal_prompt(cls, title: str) -> Optional[Tuple[str, str, str]]:
        """Return (user, host, cwd) from SSH/terminal prompt titles."""
        match = cls._TERMINAL_PROMPT.match(title.strip())
        if not match:
            return None
        return match.group(1), match.group(2), match.group(3).strip()

    @classmethod
    def _cwd_to_dir(cls, cwd: str) -> Optional[str]:
        """Last path component from a terminal cwd; None for home-only."""
        cwd = cwd.strip()
        if not cwd or cwd in ("~", "~ "):
            return None
        if cwd.startswith("~"):
            cwd = cwd[1:].lstrip("/\\")
        cwd = cwd.replace("\\", "/").strip("/")
        if not cwd:
            return None
        return cwd.rsplit("/", 1)[-1]

    @classmethod
    def _project_from_terminal_prompt(cls, title: str) -> Optional[str]:
        parsed = cls._parse_terminal_prompt(title)
        if parsed is None:
            return None
        _user, host, cwd = parsed
        dir_name = cls._cwd_to_dir(cwd)
        if not dir_name:
            return None
        local_host = socket.gethostname().lower()
        host_lower = host.lower()
        if host_lower != local_host and not local_host.startswith(host_lower):
            return f"{dir_name}@{host}"
        return dir_name

    @classmethod
    def _url_to_project(cls, url: str) -> Optional[str]:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if parsed.netloc.lower() in ("github.com", "gitlab.com", "bitbucket.org"):
            segments = path.split("/")
            if len(segments) >= 2:
                return segments[1]
        return None

    @classmethod
    def _extract_project(cls, title: str, url: Optional[str], app: str = "") -> Optional[str]:
        """Try to find a project name from title or URL."""
        # Strategy A (browsers): extract from URL path only.
        # Checked first so webmail-like titles (user@mail.com: subject)
        # never reach the terminal-prompt strategy.
        if cls._is_browser(app):
            if url:
                return cls._url_to_project(url)
            return None

        # Strategy 0: SSH/terminal prompt (user@host: cwd)
        prompt_project = cls._project_from_terminal_prompt(title)
        if prompt_project is not None:
            return prompt_project
        if cls._parse_terminal_prompt(title) is not None:
            return None

        if url:
            return cls._url_to_project(url)

        # Strategy B: strip known IDE/app suffixes, then take the last segment
        stripped = cls._IDE_SUFFIXES.sub("", title).strip()
        if stripped:
            # After stripping suffix, find the last separator
            # Require whitespace around separator to avoid matching hyphens inside words
            match = re.search(r"\s+[-—|·•]\s+(.+)$", stripped)
            if match:
                candidate = match.group(1).strip()
                if candidate and not cls._is_noise_word(candidate):
                    return candidate
            # No separator left → the whole stripped string might be the project
            # (but only if we actually stripped something; otherwise fall through)
            if stripped != title and not cls._is_noise_word(stripped):
                return stripped

        # Strategy C: terminal path → last directory name
        # e.g. ~/projects/x_system  or  ~/ros2_ws/src/x_system
        # Skip user@host: prompts (handled above); require path-like titles
        if (
            cls._parse_terminal_prompt(title) is None
            and ("/" in title or "~" in title or title.startswith("\\"))
        ):
            path_match = re.search(r"(?:^|/)([^/]+)$", title.strip())
            if path_match:
                candidate = path_match.group(1).strip()
                if candidate and not cls._is_noise_word(candidate):
                    # Make sure it doesn't look like a file with extension
                    if "." not in candidate or len(candidate.rsplit(".", 1)[-1]) > 6:
                        return candidate

        # Strategy C+: terminal command → skip project extraction
        # Command lines like "git commit -am 'wip'" or "pytest -v" should not
        # be split naively; the flags are not project names.
        title_stripped = title.strip()
        lower_title = title_stripped.lower()
        if any(lower_title.startswith(prefix) for prefix in cls._TERMINAL_COMMAND_PREFIXES):
            return None

        # Strategy D: fallback to naive split
        parts = cls._IDE_SEPARATORS.split(title)
        if len(parts) >= 2:
            candidate = parts[-1].strip()
            if candidate and not cls._is_noise_word(candidate):
                return candidate
            candidate = parts[-2].strip()
            if candidate and not cls._is_noise_word(candidate):
                return candidate

        return None

    @classmethod
    def _detect_site(cls, url: str) -> Optional[str]:
        if not url:
            return None
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix for matching
        if domain.startswith("www."):
            domain = domain[4:]
        # Direct match
        if domain in cls._SITE_PATTERNS:
            return cls._SITE_PATTERNS[domain]
        # Sub-domain match (e.g. mycompany.atlassian.net)
        for pattern, site in cls._SITE_PATTERNS.items():
            if domain == pattern or domain.endswith(f".{pattern}"):
                return site
        return None

    @classmethod
    def _detect_site_from_title(cls, title: str) -> Optional[str]:
        lower = title.lower()
        if "github" in lower:
            return "github"
        if "gitlab" in lower:
            return "gitlab"
        if "stackoverflow" in lower:
            return "stackoverflow"
        if "jira" in lower:
            return "jira"
        if "confluence" in lower:
            return "confluence"
        if "notion" in lower:
            return "notion"
        if "bilibili" in lower or "哔哩哔哩" in title:
            return "bilibili"
        if "youtube" in lower:
            return "youtube"
        if "zhihu" in lower or "知乎" in title:
            return "zhihu"
        return None

    @classmethod
    def _infer_action(cls, app: str, title: str, filename: Optional[str]) -> Optional[str]:
        lower = title.lower()
        if filename and cls._is_ide(app) and title.strip() == filename:
            return "editing"

        for keyword, action in cls._ACTION_KEYWORDS.items():
            # Chinese keywords → direct substring match
            if any("\u4e00" <= c <= "\u9fff" for c in keyword):
                if keyword in lower:
                    return action
            else:
                # English keywords → word boundary match...
                pattern = r"\b" + re.escape(keyword) + r"\b"
                if re.search(pattern, lower):
                    return action
                # ...or prefix match with underscore (test_main, debug_session)
                pattern = r"(?:^|_)" + re.escape(keyword) + r"(?:_|\b)"
                if re.search(pattern, lower):
                    return action

        # If we have a filename and it's an IDE/editor → likely editing
        if filename and cls._is_ide(app):
            if filename.lower() in cls._SPECIAL_FILENAMES:
                return "building"
            return "editing"

        return None

    @classmethod
    def _is_browser(cls, app: str) -> bool:
        return app.lower() in cls._BROWSER_APPS

    @classmethod
    def _is_ide(cls, app: str) -> bool:
        return app.lower() in cls._IDE_APPS

    @classmethod
    def _is_noise_word(cls, word: str) -> bool:
        """Return True if the word is a generic UI label, not a project name."""
        noise = {
            # IDE / Browser labels
            "visual studio code",
            "vscode",
            "code",
            "jetbrains",
            "intellij",
            "pycharm",
            "webstorm",
            "goland",
            "clion",
            "rustrover",
            "android studio",
            "xcode",
            "vim",
            "neovim",
            "nvim",
            "sublime text",
            "sublime",
            "emacs",
            "new tab",
            "新标签页",
            "google",
            "chrome",
            "chromium",
            "firefox",
            "mozilla firefox",
            "google chrome",
            "safari",
            "edge",
            "brave",
            "obsidian",
            "桌面",
            "kimi",
            "github",
            "gitlab",
            "bitbucket",
            "stackoverflow",
            "jira",
            "confluence",
            "notion",
            "settings",
            "设置",
            "preferences",
            "首选项",
            "about",
            "关于",
            "extensions",
            "扩展",
            "downloads",
            "下载",
            "history",
            "历史",
            "bookmarks",
            "书签",
            # Common short command-line flags / arguments that should never be project names
            "v", "vv", "vvv",
            "am", "m", "a", "f", "force",
            "h", "help",
            "q", "quiet",
            "n", "dry-run",
            "i", "interactive",
            "r", "recursive",
            "p", "port",
            "u", "user",
            "b", "branch",
            "o", "output",
            "e", "env",
            "s", "server",
            "d", "dev",
            "t", "tag",
            "y", "yes",
        }
        return word.lower().strip() in noise
