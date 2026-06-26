"""Process context: infer what command is running inside a terminal.

Zero-dependency (uses only `ps` subprocess call) and Linux-first.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Terminal emulator process names (lowercase)
# Note: ps "comm" is truncated to 15 chars, so include truncated forms.
_TERMINAL_PATTERNS = frozenset({
    "gnome-terminal-server", "gnome-terminal-", "gnome-terminal", "terminal",
    "alacritty", "kitty", "wezterm", "wezterm-gui",
    "warp", "tabby", "hyper", "terminator", "tilix",
    "konsole", "xterm", "st", "rxvt", "urxvt",
    "iterm2", "iterm",
})

# Shell names to exclude (we want the command *inside* the shell)
_SHELL_NAMES = frozenset({
    "bash", "sh", "zsh", "fish", "csh", "tcsh", "dash",
})

# Common background services to exclude
_BG_SERVICES = frozenset({
    "ssh-agent", "gpg-agent", "dbus-daemon", "at-spi-bus-launcher",
    "at-spi2-registryd", "pulseaudio", "pipewire", "xdg-desktop-portal",
})

# Command → action hint mapping (key is lowercase)
_CMD_ACTION_MAP: Dict[str, str] = {
    # Testing
    "pytest": "testing",
    "py.test": "testing",
    "tox": "testing",
    "unittest": "testing",
    "jest": "testing",
    "mocha": "testing",
    "ava": "testing",
    "tap": "testing",
    "cargo test": "testing",
    "go test": "testing",
    "npm test": "testing",
    "yarn test": "testing",
    "pnpm test": "testing",
    "ctest": "testing",
    "prove": "testing",
    # Debugging
    "gdb": "debugging",
    "lldb": "debugging",
    "pdb": "debugging",
    "node inspect": "debugging",
    # Building / compiling
    "make": "building",
    "cmake": "building",
    "ninja": "building",
    "cargo build": "building",
    "cargo check": "building",
    "cargo clippy": "building",
    "go build": "building",
    "npm run build": "building",
    "yarn build": "building",
    "pnpm build": "building",
    "webpack": "building",
    "vite": "building",
    "tsc": "building",
    "gradle": "building",
    "mvn": "building",
    "ant": "building",
    "sbt": "building",
    "meson": "building",
    "bazel": "building",
    "buck": "building",
    # Deploying / DevOps
    "docker": "deploying",
    "docker-compose": "deploying",
    "kubectl": "deploying",
    "helm": "deploying",
    "ansible": "deploying",
    "ansible-playbook": "deploying",
    "terraform": "deploying",
    "pulumi": "deploying",
    "flyctl": "deploying",
    "vercel": "deploying",
    "netlify": "deploying",
    "gcloud": "deploying",
    "aws": "deploying",
    "az": "deploying",
    # Version control
    "git": "committing",
    "gh": "collaborating",
    "glab": "collaborating",
    "hg": "committing",
    "svn": "committing",
    # Terminal editors
    "nvim": "editing",
    "vim": "editing",
    "vi": "editing",
    "emacs": "editing",
    "nano": "editing",
    "micro": "editing",
    "helix": "editing",
    "hx": "editing",
    # Reading docs
    "man": "reading",
    "less": "reading",
    "more": "reading",
    "info": "reading",
    "lynx": "browsing",
    "w3m": "browsing",
    "links": "browsing",
    # Package managers (usually = building/dependencies)
    "npm": "building",
    "yarn": "building",
    "pnpm": "building",
    "pip": "building",
    "poetry": "building",
    "conda": "building",
    "mamba": "building",
    "cargo": "building",
    "go get": "building",
    # Monitoring
    "htop": "monitoring",
    "top": "monitoring",
    "btm": "monitoring",
    "nvtop": "monitoring",
    # Searching
    "rg": "researching",
    "grep": "researching",
    "ack": "researching",
    "ag": "researching",
    "fd": "researching",
    "find": "researching",
    "fzf": "researching",
}


@dataclass(frozen=True)
class ProcessContextSnapshot:
    process_name: Optional[str] = None
    process_cwd: Optional[str] = None
    git_repo: Optional[str] = None
    git_branch: Optional[str] = None
    terminal_command_summary: Optional[str] = None
    terminal_action: Optional[str] = None


def _parse_ps_output(stdout: str) -> List[Tuple[int, int, str, str]]:
    """Parse ps output into (pid, ppid, comm, args) tuples."""
    processes: List[Tuple[int, int, str, str]] = []
    for line in stdout.strip().split("\n"):
        parts = line.split(None, 3)
        if len(parts) >= 4:
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
                comm = parts[2]
                args = parts[3]
                processes.append((pid, ppid, comm, args))
            except ValueError:
                continue
    return processes


def is_terminal_app(app: Optional[str]) -> bool:
    if not app:
        return False
    app_lower = app.lower()
    return any(pattern in app_lower for pattern in _TERMINAL_PATTERNS)


def _find_terminal_foreground_process(
    processes: List[Tuple[int, int, str, str]]
) -> Optional[Tuple[int, str, str]]:
    """Find the most likely foreground command inside a terminal.

    Returns (pid, comm, args) or None.  Prefer the real command inside a shell;
    if the terminal is idle, fall back to the deepest shell so cwd is still useful.
    """
    # Build children map: pid -> list of (child_pid, comm, args)
    children: dict[int, List[Tuple[int, str, str]]] = {}
    for pid, ppid, comm, args in processes:
        children.setdefault(ppid, []).append((pid, comm, args))

    # Find terminal emulator PIDs
    term_pids: set[int] = set()
    for pid, _ppid, comm, _args in processes:
        if comm.lower() in _TERMINAL_PATTERNS:
            term_pids.add(pid)

    # Recursively collect descendants of terminal emulators
    def collect(pids: set[int], depth: int = 0) -> List[Tuple[int, str, str, int]]:
        results: List[Tuple[int, str, str, int]] = []
        for pid in pids:
            for child_pid, child_comm, child_args in children.get(pid, []):
                child_comm_lower = child_comm.lower()
                if child_comm_lower in _SHELL_NAMES or child_comm_lower in _BG_SERVICES:
                    if child_comm_lower in _SHELL_NAMES:
                        results.append((child_pid, child_comm, child_args, depth))
                    results.extend(collect({child_pid}, depth + 1))
                else:
                    results.append((child_pid, child_comm, child_args, depth))
        return results

    candidates = collect(term_pids)
    if not candidates:
        return None

    real_commands = [
        c for c in candidates
        if c[1].lower() not in _SHELL_NAMES and c[1].lower() not in _BG_SERVICES
    ]
    pool = real_commands or candidates
    # Prefer deeper descendants (closer to real command) and shorter names.
    pool.sort(key=lambda x: (-x[3], len(x[1])))
    pid, comm, args, _depth = pool[0]
    return pid, comm, args


def _find_terminal_foreground_cmd(
    processes: List[Tuple[int, int, str, str]]
) -> Optional[Tuple[str, str]]:
    result = _find_terminal_foreground_process(processes)
    if result is None:
        return None
    _pid, comm, args = result
    if comm.lower() in _SHELL_NAMES or comm.lower() in _BG_SERVICES:
        return None
    return comm, args


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_terminal_foreground_command() -> Optional[Tuple[str, str]]:
    """Get the foreground command running in a terminal emulator.

    Returns (command_name, full_args) or None.
    Safe to call frequently (lightweight `ps` invocation).
    """
    try:
        result = subprocess.run(
            ["ps", "-u", str(os.getuid()), "-o", "pid,ppid,comm,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        processes = _parse_ps_output(result.stdout)
        return _find_terminal_foreground_cmd(processes)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _get_user_processes() -> Optional[List[Tuple[int, int, str, str]]]:
    try:
        result = subprocess.run(
            ["ps", "-u", str(os.getuid()), "-o", "pid,ppid,comm,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return _parse_ps_output(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _read_process_cwd(pid: int) -> Optional[str]:
    if not Path("/proc").exists():
        return None
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def summarize_command(
    comm: Optional[str],
    args: Optional[str],
    mode: str = "summary",
) -> Optional[str]:
    if not comm:
        return None
    if mode == "off":
        return None
    if mode == "full":
        return args or comm

    try:
        tokens = shlex.split(args or comm)
    except ValueError:
        tokens = (args or comm).split()
    if not tokens:
        return comm

    first = Path(tokens[0]).name
    if first != comm and comm:
        first = comm

    def first_non_flag(start: int = 1) -> Optional[str]:
        for token in tokens[start:]:
            if token.startswith("-"):
                continue
            if "/" in token or token.startswith(("~", ".")):
                continue
            return token
        return None

    if first in {"python", "python3"} and len(tokens) >= 3 and tokens[1] == "-m":
        return f"{first} -m {tokens[2]}"
    if first in {"npm", "yarn", "pnpm"} and len(tokens) >= 3 and tokens[1] == "run":
        return f"{first} run {tokens[2]}"
    sub = first_non_flag(1)
    if sub and first in {
        "git", "gh", "glab", "cargo", "go", "docker", "kubectl",
        "make", "cmake", "pytest", "tox", "poetry",
    }:
        return f"{first} {sub}"
    return first


def capture_process_context(
    *,
    active_app: Optional[str] = None,
    command_args_mode: str = "summary",
    capture_cwd: bool = True,
    capture_git: bool = True,
) -> Optional[ProcessContextSnapshot]:
    """Capture lightweight local context for the active terminal window."""
    if active_app is not None and not is_terminal_app(active_app):
        return None

    processes = _get_user_processes()
    if not processes:
        return None

    found = _find_terminal_foreground_process(processes)
    if found is None:
        return None
    pid, comm, args = found

    cwd = _read_process_cwd(pid) if capture_cwd else None
    git_repo = git_branch = None
    if cwd and capture_git:
        try:
            from aw_coach.git_context import get_git_context_from_path

            git_ctx = get_git_context_from_path(cwd)
            if git_ctx:
                git_repo = git_ctx.repo_name
                git_branch = git_ctx.branch
        except Exception:
            pass

    return ProcessContextSnapshot(
        process_name=comm,
        process_cwd=cwd,
        git_repo=git_repo,
        git_branch=git_branch,
        terminal_command_summary=summarize_command(comm, args, command_args_mode),
        terminal_action=infer_action_from_command(comm, args),
    )


def infer_action_from_command(
    cmd: Optional[str], args: Optional[str] = None
) -> Optional[str]:
    """Map a command name (+ optional args) to an action hint."""
    if not cmd:
        return None

    cmd_lower = cmd.lower()

    # 1. Check args for compound commands first (higher specificity)
    # e.g. "cargo test" overrides generic "cargo", "go build" overrides generic "go"
    if args:
        args_lower = args.lower()
        # Try two-word patterns first
        for pattern, action in _CMD_ACTION_MAP.items():
            if " " in pattern and pattern in args_lower:
                return action
        # Then try single-word prefix match (e.g. "python -m pytest" contains "pytest")
        for word, action in _CMD_ACTION_MAP.items():
            if " " not in word and word in args_lower.split():
                return action

    # 2. Direct single-word match
    if cmd_lower in _CMD_ACTION_MAP:
        return _CMD_ACTION_MAP[cmd_lower]

    return None
