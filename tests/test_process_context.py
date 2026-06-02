"""Tests for process_context module.

These tests mock `subprocess.run` to avoid actually calling `ps`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from aw_coach.process_context import (
    _find_terminal_foreground_cmd,
    _parse_ps_output,
    get_terminal_foreground_command,
    infer_action_from_command,
)

# ---------------------------------------------------------------------------
# _parse_ps_output
# ---------------------------------------------------------------------------

class TestParsePsOutput:
    def test_basic(self):
        stdout = (
            " 1234  1000 bash   bash\n"
            " 1235  1234 pytest pytest -v\n"
            " 1236  1234 python python manage.py runserver\n"
        )
        procs = _parse_ps_output(stdout)
        assert len(procs) == 3
        assert procs[0] == (1234, 1000, "bash", "bash")
        assert procs[1] == (1235, 1234, "pytest", "pytest -v")

    def test_empty(self):
        assert _parse_ps_output("") == []

    def test_malformed_lines(self):
        stdout = (
            " 1234  1000 bash   bash\n"
            "bad line\n"
            " 1235  1234 pytest pytest -v\n"
        )
        procs = _parse_ps_output(stdout)
        assert len(procs) == 2


# ---------------------------------------------------------------------------
# _find_terminal_foreground_cmd
# ---------------------------------------------------------------------------

class TestFindTerminalForegroundCmd:
    def test_finds_child_of_terminal(self):
        procs = [
            (1000, 1, "gnome-terminal-server", "gnome-terminal-server"),
            (1001, 1000, "bash", "bash"),
            (1002, 1000, "pytest", "pytest -v"),
        ]
        result = _find_terminal_foreground_cmd(procs)
        assert result is not None
        assert result[0] == "pytest"

    def test_excludes_shells(self):
        procs = [
            (1000, 1, "gnome-terminal-server", "gnome-terminal-server"),
            (1001, 1000, "zsh", "zsh"),
            (1002, 1000, "bash", "bash"),
        ]
        result = _find_terminal_foreground_cmd(procs)
        assert result is None

    def test_excludes_background_services(self):
        procs = [
            (1000, 1, "gnome-terminal-server", "gnome-terminal-server"),
            (1001, 1000, "ssh-agent", "ssh-agent"),
        ]
        result = _find_terminal_foreground_cmd(procs)
        assert result is None

    def test_no_terminal_present(self):
        procs = [
            (1000, 1, "chrome", "chrome"),
            (1001, 1000, "python", "python app.py"),
        ]
        result = _find_terminal_foreground_cmd(procs)
        assert result is None


# ---------------------------------------------------------------------------
# get_terminal_foreground_command
# ---------------------------------------------------------------------------

class TestGetTerminalForegroundCommand:
    @patch("aw_coach.process_context.subprocess.run")
    def test_returns_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                " 1000     1 gnome-terminal-server gnome-terminal-server\n"
                " 1001  1000 bash                 bash\n"
                " 1002  1000 pytest               pytest -v\n"
            ),
        )
        result = get_terminal_foreground_command()
        assert result is not None
        assert result[0] == "pytest"
        assert result[1] == "pytest -v"

    @patch("aw_coach.process_context.subprocess.run")
    def test_no_terminal(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                " 1000     1 chrome   chrome\n"
                " 1001  1000 python   python app.py\n"
            ),
        )
        result = get_terminal_foreground_command()
        assert result is None

    @patch("aw_coach.process_context.subprocess.run")
    def test_ps_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_terminal_foreground_command()
        assert result is None


# ---------------------------------------------------------------------------
# infer_action_from_command
# ---------------------------------------------------------------------------

class TestInferActionFromCommand:
    def test_direct_match(self):
        assert infer_action_from_command("pytest") == "testing"
        assert infer_action_from_command("gdb") == "debugging"
        assert infer_action_from_command("make") == "building"

    def test_compound_from_args(self):
        assert infer_action_from_command("cargo", "cargo test --release") == "testing"
        assert infer_action_from_command("go", "go build ./...") == "building"
        assert infer_action_from_command("npm", "npm run build") == "building"
        assert infer_action_from_command("docker", "docker compose up") == "deploying"

    def test_no_match(self):
        assert infer_action_from_command("foobar") is None
        assert infer_action_from_command(None) is None

    def test_case_insensitive(self):
        assert infer_action_from_command("PyTest") == "testing"
        assert infer_action_from_command("GDB") == "debugging"

    def test_python_with_module(self):
        assert infer_action_from_command("python", "python -m pytest") == "testing"
        assert infer_action_from_command("python3", "python3 -m unittest") == "testing"

    def test_editor(self):
        assert infer_action_from_command("nvim") == "editing"
        assert infer_action_from_command("vim") == "editing"
        assert infer_action_from_command("hx") == "editing"
