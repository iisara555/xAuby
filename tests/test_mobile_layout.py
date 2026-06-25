"""Mobile layout smoke tests — simulate iPhone/Termius sizes."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.ui.textual_tui.app import XAubyTextualApp


@pytest.mark.asyncio
async def test_dashboard_mounts_on_phone():
    """Dashboard should mount at iPhone width (~80 cols)."""
    app = XAubyTextualApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "DashboardScreen"
        assert screen.query_one("AppHeader") is not None
        assert screen.query_one("AppFooter") is not None


@pytest.mark.asyncio
async def test_dashboard_mounts_on_tablet():
    """Dashboard should mount at tablet width (~100 cols)."""
    app = XAubyTextualApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "DashboardScreen"


@pytest.mark.asyncio
async def test_tradelog_mounts_on_phone():
    """TradeLog should mount at phone width without crash."""
    app = XAubyTextualApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "TradeLogScreen"
        dt = screen.query_one("#tl-datatable")
        assert dt is not None


@pytest.mark.asyncio
async def test_incidents_mounts_on_phone():
    """Incidents should mount at phone width without crash."""
    app = XAubyTextualApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "IncidentExplorerScreen"
        body = screen.query_one("#incident-body-root")
        assert body is not None


@pytest.mark.asyncio
async def test_menu_mounts_on_phone():
    """Menu should mount at phone width without crash."""
    app = XAubyTextualApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "MenuScreen"


@pytest.mark.asyncio
async def test_narrow_phone_dashboard():
    """Dashboard should survive ultra-narrow width (< 75 cols)."""
    app = XAubyTextualApp()
    async with app.run_test(size=(60, 35)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "DashboardScreen"
