"""Tests for the `xauby` CLI entry point."""

import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_cli_tenant_tui_attach_is_scoped_and_secret_free(tmp_path, monkeypatch):
    """Hosted TUI attach must spawn only an observation-only tenant process."""
    config_root = tmp_path / "config"
    runtime_root = tmp_path / "runtime"
    (config_root / "pilot-1").mkdir(parents=True)
    (runtime_root / "pilot-1").mkdir(parents=True)
    monkeypatch.setenv("XAUBY_TENANT_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("XAUBY_TENANT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("OKX_API_SECRET", "must-not-leak")
    monkeypatch.setenv("XAUBY_MENU_ACTION", "run_live")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("xauby.cli.subprocess.run", fake_run)

    from xauby.cli import main

    assert main(["tui", "--tenant", "pilot-1", "--read-only"]) == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [sys.executable, "-m", "xauby.ui.textual_tui.app"]
    env = kwargs["env"]
    assert env["XAUBY_CONFIG_DIR"] == str(config_root / "pilot-1")
    assert env["XAUBY_HOME"] == str(runtime_root)
    assert env["XAUBY_INSTANCE_ID"] == "pilot-1"
    assert env["SQLITE_DB_PATH"] == str(runtime_root / "pilot-1" / "xauby.db")
    assert env["XAUBY_TUI_READ_ONLY"] == "1"
    assert env["FROM_LAUNCHER"] == "true"
    assert "OKX_API_SECRET" not in env
    assert "XAUBY_MENU_ACTION" not in env


@pytest.mark.parametrize(
    "args",
    [
        ["tui", "--tenant", "pilot-1"],
        ["tui", "--read-only"],
        ["--tenant", "pilot-1", "--read-only"],
        ["tui", "--tenant", "../pilot", "--read-only"],
    ],
)
def test_cli_tenant_tui_attach_fails_closed(args, tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    runtime_root = tmp_path / "runtime"
    (config_root / "pilot-1").mkdir(parents=True)
    (runtime_root / "pilot-1").mkdir(parents=True)
    monkeypatch.setenv("XAUBY_TENANT_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("XAUBY_TENANT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(
        "xauby.cli.subprocess.run",
        lambda *a, **kw: pytest.fail("unsafe TUI attach spawned a child"),
    )

    from xauby.cli import main

    assert main(args) == 2
