import os
from unittest.mock import MagicMock

from xauby import cli


CONTROLLED_RESTART_SCRIPT = os.path.join("scripts", "controlled_restart_engine.sh")
DEPLOY_SCRIPT = os.path.join("scripts", "deploy_from_github.sh")


def test_live_restart_uses_controlled_restart_script(monkeypatch):
    run = MagicMock(return_value=type("Result", (), {"returncode": 0})())
    tui = MagicMock()

    monkeypatch.setattr(
        cli.os.path, "isfile", lambda path: path == CONTROLLED_RESTART_SCRIPT
    )
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("launcher.run_textual_tui", tui)

    assert cli._do_restart(live_mode=True) == 0
    run.assert_called_once_with([CONTROLLED_RESTART_SCRIPT])
    tui.assert_called_once_with(start_screen="dashboard")


def test_live_restart_stops_when_controlled_restart_fails(monkeypatch):
    run = MagicMock(return_value=type("Result", (), {"returncode": 2})())
    tui = MagicMock()

    monkeypatch.setattr(
        cli.os.path, "isfile", lambda path: path == CONTROLLED_RESTART_SCRIPT
    )
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("launcher.run_textual_tui", tui)

    assert cli._do_restart(live_mode=True) == 2
    run.assert_called_once_with([CONTROLLED_RESTART_SCRIPT])
    tui.assert_not_called()


def test_update_runs_deploy_from_main_with_restart(monkeypatch):
    run = MagicMock(return_value=type("Result", (), {"returncode": 0})())

    monkeypatch.setattr(cli.os.path, "isfile", lambda path: path == DEPLOY_SCRIPT)
    monkeypatch.setattr("subprocess.run", run)

    assert cli._do_update() == 0
    run.assert_called_once_with([DEPLOY_SCRIPT, "--restart", "--branch=main"])


def test_update_returns_deploy_failure(monkeypatch):
    run = MagicMock(return_value=type("Result", (), {"returncode": 1})())

    monkeypatch.setattr(cli.os.path, "isfile", lambda path: path == DEPLOY_SCRIPT)
    monkeypatch.setattr("subprocess.run", run)

    assert cli._do_update() == 1
    run.assert_called_once_with([DEPLOY_SCRIPT, "--restart", "--branch=main"])
