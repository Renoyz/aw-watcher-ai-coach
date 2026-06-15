"""Process context: infer what command is running inside a terminal.

Zero-dependency (uses only `ps` subprocess call) and Linux-first.
"""

from __future__ import annotations

import os
import subprocess
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


def _find_terminal_foreground_cmd(
    processes: List[Tuple[int, int, str, str]]
) -> Optional[Tuple[str, str]]:
    """Find the most likely foreground command inside a terminal.

    Returns (comm, args) or None.
    Recursively descends past shells to find the real foreground command.
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
                    # Shells / bg services: keep recursing, don't include them
                    results.extend(collect({child_pid}, depth + 1))
                else:
                    results.append((child_pid, child_comm, child_args, depth))
        return results

    candidates = collect(term_pids)
    if not candidates:
        return None

    # Prefer deeper descendants (closer to real command) and shorter names
    candidates.sort(key=lambda x: (-x[3], len(x[1])))
    _pid, comm, args, _depth = candidates[0]
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
