"""Tests for tmux session 'dashboard' rule when restarting TUI."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_launcher_tmux_helpers_exist():
    """Launcher should expose tmux helper functions."""
    import launcher

    assert hasattr(launcher, "_is_inside_tmux")
    assert hasattr(launcher, "_tmux_available")
    assert hasattr(launcher, "_has_tmux_session")
    assert hasattr(launcher, "_kill_tmux_session")
    assert hasattr(launcher, "run_in_tmux_dashboard")
    assert hasattr(launcher, "run_textual_tui")


def test_launcher_tmux_session_default():
    """Default tmux session name should be 'dashboard'."""
    import launcher

    assert launcher._XAUBY_TMUX_SESSION == "dashboard"


def test_launcher_tmux_availability():
    """Tmux availability check should not crash."""
    import launcher

    avail = launcher._tmux_available()
    assert isinstance(avail, bool)


def test_app_restart_uses_launcher():
    """App main should contain logic to exec launcher.py on restart."""
    import inspect
    from xauby.ui.textual_tui import app

    source = inspect.getsource(app.main)
    assert "launcher.py" in source
    assert 'os.execv' in source
    assert "XAUBY_START_SCREEN" in source
