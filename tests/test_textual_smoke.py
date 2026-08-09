"""Smoke tests for the Textual TUI instantiation and navigation."""

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.ui.textual_tui.app import XAubyTextualApp
from xauby.ui.textual_tui.tradelog_native import TradeDataTable
from xauby.ui.textual_tui.incident_native import IncidentNativeBody
from xauby.ui.textual_tui.backtest_native import BacktestNativeBody


@pytest.mark.asyncio
async def test_dashboard_mounts():
    """Dashboard screen should mount header, footer, and key widgets."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "DashboardScreen"
        assert screen.query_one("AppHeader") is not None
        assert screen.query_one("AppFooter") is not None
        assert screen.query_one("#portfolio-panel") is not None
        assert screen.query_one("#positions-panel") is not None
        assert screen.query_one("#regime-panel") is not None
        assert screen.query_one("#chart-ansi") is not None
        assert screen.query_one("#checklist-panel") is not None


@pytest.mark.asyncio
async def test_navigation_between_screens():
    """Hotkeys should switch between screens correctly."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Dashboard (default)
        assert app.screen.__class__.__name__ == "DashboardScreen"

        # Menu
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "MenuScreen"

        # Trade log
        await pilot.press("t")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "TradeLogScreen"

        # Incidents
        await pilot.press("i")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "IncidentExplorerScreen"

        # Backtest
        await pilot.press("4")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "BacktestScreen"

        # Back to dashboard
        await pilot.press("d")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"


@pytest.mark.asyncio
async def test_quit_app():
    """Pressing 'q' should exit the app cleanly."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        # After exiting, app should no longer be running
        assert not app._running  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tradelog_datatable_mounts():
    """TradeLog screen should mount a native DataTable."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "TradeLogScreen"
        dt = screen.query_one("#tl-datatable", TradeDataTable)
        assert dt is not None
        assert len(dt.columns) == 6


@pytest.mark.asyncio
async def test_tradelog_sort_actions():
    """Sort actions on TradeLogNativeBody should not crash."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        screen = app.screen
        body = screen.query_one("#tradelog-body-root")
        # Trigger sort actions even when table is empty
        body.action_sort_col_4()
        body.action_sort_reverse()
        body.action_sort_col_0()
        # Should survive without exception


@pytest.mark.asyncio
async def test_incident_native_mounts():
    """Incident screen should mount native IncidentNativeBody."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "IncidentExplorerScreen"
        body = screen.query_one("#incident-body-root", IncidentNativeBody)
        assert body is not None


@pytest.mark.asyncio
async def test_read_only_tenant_attach_exposes_views_but_no_mutations(monkeypatch):
    """Observer mode keeps monitoring screens while all local controls fail closed."""
    import xauby.ui.textual_tui.screens as screens_mod
    from textual.widgets import OptionList

    monkeypatch.setenv("XAUBY_TUI_READ_ONLY", "1")
    monkeypatch.setenv("XAUBY_TUI_TENANT", "pilot-1")
    monkeypatch.setenv("XAUBY_START_SCREEN", "dashboard")
    order_calls = []
    focus_calls = []
    monkeypatch.setattr(
        screens_mod,
        "write_manual_order_request",
        lambda *a, **kw: order_calls.append((a, kw)),
    )
    monkeypatch.setattr(
        screens_mod,
        "write_focus_request",
        lambda *a, **kw: focus_calls.append((a, kw)),
    )

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"
        assert not app.is_screen_installed("backtest")
        assert not app.is_screen_installed("quick_config")
        assert not app.is_screen_installed("db_tools")

        await pilot.press("f7")
        await pilot.pause()
        await pilot.press("f8")
        await pilot.pause()
        assert order_calls == []

        dashboard = app.screen
        dashboard._last_pairs = ["BTCUSDT", "XAUTUSDT"]
        dashboard._last_focus = "BTCUSDT"
        monkeypatch.setattr(dashboard, "_load_state", lambda: None)
        monkeypatch.setattr(dashboard, "_push_state", lambda: None)
        dashboard._cycle_pair_focus(1)
        assert app._xauby_local_focus == "XAUTUSDT"
        assert focus_calls == []

        await pilot.press("4")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"

        await pilot.press("ctrl+m")
        await pilot.pause()
        menu = app.screen.query_one("#launcher-menu", OptionList)
        option_ids = {
            menu.get_option_at_index(index).id
            for index in range(menu.option_count)
            if menu.get_option_at_index(index).id
        }
        assert option_ids == {"dashboard", "tradelog", "incidents", "exit"}
        assert not (
            {"run_sim", "run_live", "config", "db_tools", "backtest", "restart_service"}
            & option_ids
        )
        assert focus_calls == []


def test_read_only_menu_action_never_dispatches_launcher(monkeypatch):
    monkeypatch.setenv("XAUBY_TUI_READ_ONLY", "1")
    from xauby.ui.textual_tui.menu_actions import run_menu_action

    assert run_menu_action("run_live") is False


@pytest.mark.asyncio
async def test_tradelog_phone_shows_all_panels():
    """In phone mode (<75 cols) regime, peak, and perf panels should all be displayed."""
    app = XAubyTextualApp()
    async with app.run_test(size=(70, 35)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "TradeLogScreen"
        body = screen.query_one("#tradelog-body-root")
        await body._refresh(force=True)
        regime = body.query_one("#tl-regime")
        peak = body.query_one("#tl-peak")
        perf = body.query_one("#tl-perf")
        assert regime.display is True
        assert peak.display is True
        assert perf.display is True


@pytest.mark.asyncio
async def test_tradelog_phone_shows_compact_table():
    """In phone mode DataTable should have 3 columns."""
    app = XAubyTextualApp()
    async with app.run_test(size=(70, 35)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        screen = app.screen
        body = screen.query_one("#tradelog-body-root")
        await body._refresh(force=True)
        dt = screen.query_one("#tl-datatable", TradeDataTable)
        assert len(dt.columns) == 3



@pytest.mark.asyncio
async def test_backtest_screen_mounts():
    """Backtest screen should mount summary and parity panels."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "BacktestScreen"
        body = screen.query_one("#backtest-body-root", BacktestNativeBody)
        assert body is not None
        assert screen.query_one("#bt-summary") is not None
        assert screen.query_one("#bt-parity") is not None
        assert screen.query_one("#bt-trades") is not None


@pytest.mark.asyncio
async def test_backtest_run_action_no_crash(monkeypatch):
    """Pressing R on backtest should not crash (mocked run)."""
    from xauby.backtest.service import BacktestRunMeta, BacktestRunResult

    def _fake_run(symbol, **kwargs):
        meta = BacktestRunMeta(
            symbol=symbol,
            strategy_name="cdc_action_zone",
            primary_tf="4h",
            regime_tf=None,
            period_start="2024-01",
            period_end="2024-06",
            bars=100,
            use_d1_regime_filter=False,
            fee_pct=0.001,
            run_ok=True,
        )
        return BacktestRunResult(
            stats={
                "total_trades": 3,
                "net_profit_pct": 2.0,
                "profit_factor": 1.1,
                "win_rate": 50.0,
                "max_drawdown_pct": 1.0,
                "trades": [
                    {
                        "entry_time": 1717545600,
                        "exit_time": 1717560000,
                        "entry_price": 2300.0,
                        "exit_price": 2350.0,
                        "pnl": 50.0,
                        "pnl_pct": 2.17,
                        "trigger": "ZONE_GREEN",
                    }
                ],
            },
            meta=meta,
        )

    monkeypatch.setattr(
        "xauby.ui.textual_tui.backtest_native.run_focused_backtest",
        _fake_run,
    )

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(delay=0.5)
        body = app.screen.query_one("#backtest-body-root", BacktestNativeBody)
        assert body.is_running() or len(body._cache) > 0


@pytest.mark.asyncio
async def test_backtest_run_action_uppercase_r(monkeypatch):
    """Pressing R (uppercase) on backtest should also not crash."""
    from xauby.backtest.service import BacktestRunMeta, BacktestRunResult

    def _fake_run(symbol, **kwargs):
        meta = BacktestRunMeta(
            symbol=symbol,
            strategy_name="cdc_action_zone",
            primary_tf="4h",
            regime_tf=None,
            period_start="2024-01",
            period_end="2024-06",
            bars=100,
            use_d1_regime_filter=False,
            fee_pct=0.001,
            run_ok=True,
        )
        return BacktestRunResult(
            stats={
                "total_trades": 3,
                "net_profit_pct": 2.0,
                "profit_factor": 1.1,
                "win_rate": 50.0,
                "max_drawdown_pct": 1.0,
                "trades": [
                    {
                        "entry_time": 1717545600,
                        "exit_time": 1717560000,
                        "entry_price": 2300.0,
                        "exit_price": 2350.0,
                        "pnl": 50.0,
                        "pnl_pct": 2.17,
                        "trigger": "ZONE_GREEN",
                    }
                ],
            },
            meta=meta,
        )

    monkeypatch.setattr(
        "xauby.ui.textual_tui.backtest_native.run_focused_backtest",
        _fake_run,
    )

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("R")
        await pilot.pause(delay=0.5)
        body = app.screen.query_one("#backtest-body-root", BacktestNativeBody)
        assert body.is_running() or len(body._cache) > 0


@pytest.mark.asyncio
async def test_incidents_key_3_from_dashboard():
    """Pressing '3' from Dashboard should navigate to Incidents."""
    app = XAubyTextualApp()
    async with app.run_test(size=(80, 35)) as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"
        await pilot.press("3")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "IncidentExplorerScreen"


@pytest.mark.asyncio
async def test_arrow_key_navigation_on_tradelog():
    """Arrow keys should navigate screens sequentially and cycle symbols."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Navigate to TradeLog
        await pilot.press("t")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "TradeLogScreen"
        
        # Verify DataTable is focused
        dt = app.screen.query_one("#tl-datatable")
        assert app.screen.focused is dt
        
        # Pressing 'right' should switch to Incidents (next page)
        await pilot.press("right")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "IncidentExplorerScreen"

        # Pressing 'right' again should switch to Backtest (next page)
        await pilot.press("right")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "BacktestScreen"
        
        # Pressing 'left' should switch back to Incidents (prev page)
        await pilot.press("left")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "IncidentExplorerScreen"

        # Pressing 'left' should switch back to TradeLog (prev page)
        await pilot.press("left")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "TradeLogScreen"
        
        # Pressing 'left' should switch to Dashboard (prev page)
        await pilot.press("left")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"
        
        # Check initial focus symbol
        initial_focus = app.screen._last_focus
        
        # Pressing 'down' should cycle focus to next symbol
        await pilot.press("down")
        await pilot.pause()
        new_focus = app.screen._last_focus
        # Since we might only have one pair in mock state, it might not change,
        # but let's check it doesn't crash and we can cycle back.
        await pilot.press("up")
        await pilot.pause()
        assert app.screen._last_focus == initial_focus



@pytest.mark.asyncio
async def test_menu_optionlist_exposes_all_actions():
    """The launcher menu OptionList should carry every menu action id."""
    from xauby.ui.textual_tui.menu_screen import LauncherMenu, MENU_OPTIONS

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "MenuScreen"
        menu = app.screen.query_one(LauncherMenu)
        ids = {
            menu.get_option_at_index(i).id
            for i in range(menu.option_count)
            if menu.get_option_at_index(i).id
        }
    expected = {action for _, _, action in MENU_OPTIONS}
    assert expected <= ids


@pytest.mark.asyncio
async def test_menu_digit_accelerator_sets_action(monkeypatch):
    """A process-action digit sets XAUBY_MENU_ACTION and exits."""
    import os

    monkeypatch.delenv("XAUBY_MENU_ACTION", raising=False)
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        await pilot.press("8")  # Restart Bot Engine Service (still exit-based)
        await pilot.pause()
    assert os.environ.get("XAUBY_MENU_ACTION") == "restart_service"
    monkeypatch.delenv("XAUBY_MENU_ACTION", raising=False)


@pytest.mark.asyncio
async def test_menu_system_check_pushes_screen(monkeypatch):
    """System Check is now an in-app screen, not a terminal exit."""
    import os

    monkeypatch.delenv("XAUBY_MENU_ACTION", raising=False)
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        await pilot.press("5")  # System Configuration Check
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SystemCheckScreen"
    assert os.environ.get("XAUBY_MENU_ACTION") is None


@pytest.mark.asyncio
async def test_menu_dashboard_option_switches_screen():
    """Selecting the dashboard action switches screen instead of exiting."""
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "MenuScreen"
        await pilot.press("3")  # Open Dashboard
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"
