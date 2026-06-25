"""Tests for the `xauby` CLI entry point."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_cli_help_does_not_crash():
    """CLI --help should print usage and exit 0."""
    from xauby.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_cli_config_flag_opens_tui_hub(monkeypatch):
    """--config opens the native Textual config hub by default."""
    import launcher

    monkeypatch.delenv("XAUBY_CONFIG_TERMINAL", raising=False)
    calls = []
    monkeypatch.setattr(launcher, "run_textual_tui", lambda **kw: calls.append(kw))

    from xauby.cli import main
    code = main(["--config"])
    assert code == 0
    assert calls == [{"start_screen": "quick_config"}]


def test_cli_config_flag_terminal_fallback(monkeypatch):
    """XAUBY_CONFIG_TERMINAL=1 routes --config to the legacy terminal editor."""
    import launcher

    monkeypatch.setenv("XAUBY_CONFIG_TERMINAL", "1")
    calls = []
    monkeypatch.setattr(launcher, "quick_config_editor", lambda: calls.append(True))

    from xauby.cli import main
    code = main(["--config"])
    assert code == 0
    assert calls == [True]


def test_cli_engine_running_goes_straight_to_tui(monkeypatch):
    """If engine is already running, CLI should call run_textual_tui."""
    import launcher
    import xauby.ui.menu as menu_mod

    monkeypatch.setattr(menu_mod, "check_engine_status", lambda: ("RUNNING", True))
    tui_calls = []
    monkeypatch.setattr(launcher, "run_textual_tui", lambda **kw: tui_calls.append(kw))

    from xauby.cli import main
    code = main([])
    assert code == 0
    assert tui_calls == [{"start_screen": "dashboard"}]


def test_cli_engine_offline_starts_engine(monkeypatch):
    """If engine is offline, CLI should call run_engine_with_tui."""
    import launcher
    import xauby.ui.menu as menu_mod

    monkeypatch.setattr(menu_mod, "check_engine_status", lambda: ("OFFLINE", None))
    engine_calls = []
    monkeypatch.setattr(launcher, "run_engine_with_tui", lambda live_mode: engine_calls.append(live_mode))

    from xauby.cli import main
    code = main(["--sim"])
    assert code == 0
    assert engine_calls == [False]
