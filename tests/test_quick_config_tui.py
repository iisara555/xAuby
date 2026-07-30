"""Pilot tests for the native Textual Quick Config screens (Milestone 1).

Each test runs against an isolated temp config (copied from the repo) so the
real bot_config.yaml/.env are never touched.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from textual.widgets import Input, OptionList

from xauby.ui.textual_tui.app import XAubyTextualApp
from xauby.ui.textual_tui.quick_config.screens import QuickConfigScreen
from xauby.ui.textual_tui.quick_config.modals import NumberModal


@pytest.fixture()
def temp_config(tmp_path, monkeypatch):
    for name in ("bot_config.yaml", "coin_whitelist.json"):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / "core" / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _load_cfg(tmp_path):
    with open(tmp_path / "bot_config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def _open_submenu(app, pilot, submenu_id):
    app.push_screen(QuickConfigScreen(app.db, submenu_id))
    await pilot.pause()
    await pilot.pause()
    return app.screen


async def _choose_option(app, pilot, option_id):
    """Select a mounted choice deterministically and drain its dismiss callback."""
    await pilot.wait_for_scheduled_animations()
    choices = app.screen.query_one("#qc-choice-list", OptionList)
    choices.highlighted = choices.get_option_index(option_id)
    await pilot.pause()
    assert choices.get_option_at_index(choices.highlighted).id == option_id
    await pilot.press("enter")
    await pilot.wait_for_scheduled_animations()


async def _edit_number(app, pilot, submenu_id, key, value):
    """Open a submenu, edit a number field, then return to a stable screen."""
    scr = await _open_submenu(app, pilot, submenu_id)
    ol = scr.query_one("#qc-list", OptionList)
    ol.highlighted = ol.get_option_index(key)
    await pilot.pause()
    ol.action_select()
    await pilot.pause()
    app.screen.query_one(Input).value = str(value)
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()
    if app.screen.__class__.__name__ == "QuickConfigScreen":
        app.pop_screen()
    await pilot.pause()


@pytest.mark.asyncio
async def test_hub_lists_available_categories(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        await pilot.press("4")  # config -> hub
        await pilot.pause()
        assert app.screen.__class__.__name__ == "QuickConfigHubScreen"
        ol = app.screen.query_one("#qc-hub-list", OptionList)
        ids = {ol.get_option_at_index(i).id for i in range(ol.option_count)}
        assert {"trading", "system_features"} <= ids
        # the hub auto-highlights the first selectable category
        assert ol.get_option_at_index(ol.highlighted).id == "trading"


@pytest.mark.asyncio
async def test_system_features_toggle_persists(temp_config):
    cfg = _load_cfg(temp_config)
    cfg.setdefault("architecture", {})["event_bus_enabled"] = False
    with open(temp_config / "bot_config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        scr = await _open_submenu(app, pilot, "system_features")
        ol = scr.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index("event_bus_enabled")
        await pilot.pause()
        ol.action_select()
        await pilot.pause()
        await pilot.pause()

    assert _load_cfg(temp_config)["architecture"]["event_bus_enabled"] is True


@pytest.mark.asyncio
async def test_number_modal_validates_range(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        scr = await _open_submenu(app, pilot, "trading")
        ol = scr.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index("risk")  # Pair risk %, range 0.1–10
        await pilot.pause()
        ol.action_select()
        await pilot.pause()
        assert isinstance(app.screen, NumberModal)

        # Out of range -> modal stays open, no write.
        inp = app.screen.query_one(Input)
        inp.value = "50"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NumberModal)

        # Valid -> dismisses and persists (5% -> risk_pct 0.05).
        inp = app.screen.query_one(Input)
        inp.value = "5"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, NumberModal)

    from xauby.launcher.quick_config import _quick_config_load_values
    assert abs(_quick_config_load_values()["max_risk"] - 0.05) < 1e-9


@pytest.mark.asyncio
async def test_main_menu_arrow_navigation(temp_config):
    """Regression: arrow keys must move the launcher menu cursor."""
    from xauby.ui.textual_tui.menu_screen import LauncherMenu

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        ol = app.screen.query_one(LauncherMenu)
        assert ol.get_option_at_index(ol.highlighted).id == "run_sim"
        await pilot.press("down")
        await pilot.pause()
        assert ol.get_option_at_index(ol.highlighted).id == "run_live"


@pytest.mark.asyncio
async def test_hub_milestone2_categories_available(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("quick_config")
        await pilot.pause()
        ol = app.screen.query_one("#qc-hub-list", OptionList)
        enabled = {
            ol.get_option_at_index(i).id
            for i in range(ol.option_count)
            if not ol.get_option_at_index(i).disabled
        }
        assert {"trading", "global_trading", "risk_guards", "portfolio",
                "strategy_params", "regime_router", "system_features"} <= enabled


@pytest.mark.asyncio
async def test_global_trading_dual_path_write(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _edit_number(app, pilot, "global_trading", "max_position_per_trade_pct", 40)
    cfg = _load_cfg(temp_config)
    assert cfg["trading"]["max_position_per_trade_pct"] == 40
    assert cfg["portfolio"]["position_sizing"]["max_position_per_trade_pct"] == 40


@pytest.mark.asyncio
async def test_global_trading_max_open_positions_matches_runtime_resolver(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _edit_number(app, pilot, "global_trading", "max_open_positions", 7)
    cfg = _load_cfg(temp_config)
    assert cfg["trading"]["max_open_positions"] == 7
    assert cfg["risk"]["max_open_positions"] == 7

    from xauby.runtime.trading_config import resolve_trading_config

    eff = resolve_trading_config(cfg, symbol="XAUUSDT", project_root=str(temp_config))
    assert eff.portfolio["max_open_positions"] == 7


@pytest.mark.asyncio
async def test_strategy_params_rsi_cross_validation(temp_config):
    from xauby.launcher.quick_config import _quick_config_load_values, _strategy_cfg_for_symbol
    from xauby.launcher.config_io import _load_bot_yaml

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _edit_number(app, pilot, "strategy_params", "rsi_max", 70)
        await _edit_number(app, pilot, "strategy_params", "rsi_min", 40)
        fs = _quick_config_load_values()["focus_symbol"]
        assert _strategy_cfg_for_symbol(_load_bot_yaml(), fs).get("rsi_min") == 40
        # rsi_min >= rsi_max is rejected (write blocked).
        await _edit_number(app, pilot, "strategy_params", "rsi_min", 80)
        assert _strategy_cfg_for_symbol(_load_bot_yaml(), fs).get("rsi_min") == 40


async def _edit_text(app, pilot, submenu_id, key, value):
    scr = await _open_submenu(app, pilot, submenu_id)
    ol = scr.query_one("#qc-list", OptionList)
    ol.highlighted = ol.get_option_index(key)
    await pilot.pause()
    ol.action_select()
    await pilot.pause()
    app.screen.query_one(Input).value = str(value)
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()
    if app.screen.__class__.__name__ == "QuickConfigScreen":
        app.pop_screen()
    await pilot.pause()


@pytest.mark.asyncio
async def test_alerts_token_textmodal_writes_env(temp_config, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _edit_text(app, pilot, "alerts", "tg_token", "999:XYZ")
    import os
    assert os.environ.get("TELEGRAM_BOT_TOKEN") == "999:XYZ"
    assert "TELEGRAM_BOT_TOKEN" in (temp_config / ".env").read_text()


def test_quick_config_load_values_refreshes_env_file(temp_config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "STALE000000")
    (temp_config / ".env").write_text("TELEGRAM_BOT_TOKEN=FRESH123456\n", encoding="utf-8")

    from xauby.launcher.quick_config import _quick_config_load_values

    assert _quick_config_load_values()["tg_tok_disp"] == "SET (...123456)"
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "FRESH123456"


@pytest.mark.asyncio
async def test_notifications_and_macro_reachable_and_write(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _edit_number(app, pilot, "notifications", "wr_hour", 9)
    assert _load_cfg(temp_config)["weekly_review"]["hour_utc"] == 9


@pytest.mark.asyncio
async def test_ai_news_provider_choice_persists(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        scr = await _open_submenu(app, pilot, "ai_news")
        ol = scr.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index("provider")
        await pilot.pause()
        ol.action_select()
        await pilot.pause()
        cl = app.screen.query_one("#qc-choice-list", OptionList)
        cl.highlighted = cl.get_option_index("openai")
        await pilot.pause()
        cl.action_select()
        await pilot.pause()
        await pilot.pause()
    assert _load_cfg(temp_config)["macro_sentiment_guard"]["news_provider"] == "openai"


@pytest.mark.asyncio
async def test_all_hub_categories_available(temp_config):
    from xauby.ui.textual_tui.quick_config.schema import HUB_GROUPS, SUBMENUS
    hub_ids = [sid for _, items in HUB_GROUPS for sid, _ in items]
    assert all(sid in SUBMENUS for sid in hub_ids)
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("quick_config")
        await pilot.pause()
        ol = app.screen.query_one("#qc-hub-list", OptionList)
        enabled = {
            ol.get_option_at_index(i).id
            for i in range(ol.option_count)
            if not ol.get_option_at_index(i).disabled
        }
        assert set(hub_ids) <= enabled  # no "(soon)" left


@pytest.mark.asyncio
async def test_regime_mapping_write(temp_config):
    from xauby.runtime.architecture_config import DEFAULT_REGIME_ROUTER_MAPPING
    reg = list(DEFAULT_REGIME_ROUTER_MAPPING.keys())[0]
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        scr = await _open_submenu(app, pilot, "regime_mapping")
        ol = scr.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index(reg)
        await pilot.pause()
        ol.action_select()
        await pilot.pause()
        await _choose_option(app, pilot, "xauby_actionzone")
    assert _load_cfg(temp_config)["regime_router"]["mapping"][reg] == "xauby_actionzone"


@pytest.mark.asyncio
async def test_regime_mapping_no_trade_stays_visible_and_runtime_no_trade(temp_config):
    from xauby.runtime.architecture_config import regime_router_mapping
    from xauby.ui.textual_tui.quick_config.schema import SUBMENUS, build_ctx

    reg = "BULL_BREAKOUT"
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        scr = await _open_submenu(app, pilot, "regime_mapping")
        ol = scr.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index(reg)
        await pilot.pause()
        ol.action_select()
        await pilot.pause()
        await _choose_option(app, pilot, "NO_TRADE")

    cfg = _load_cfg(temp_config)
    assert cfg["regime_router"]["mapping"][reg] is None
    assert regime_router_mapping(cfg)[reg] is None

    ctx = build_ctx()
    field = next(f for f in SUBMENUS["regime_mapping"].fields(ctx) if f.key == reg)
    assert field.get(ctx) == "NO_TRADE"


@pytest.mark.asyncio
async def test_pairlist_toggle_write(temp_config):
    import json
    wl_path = temp_config / "coin_whitelist.json"
    base = str(json.loads(wl_path.read_text())["assets"][0]["symbol"]).upper()
    before = bool(json.loads(wl_path.read_text())["assets"][0].get("enabled"))
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        scr = await _open_submenu(app, pilot, "pairlist")
        ol = scr.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index(f"pair_{base}")
        await pilot.pause()
        ol.action_select()
        await pilot.pause()
        await pilot.pause()
    after = bool(json.loads(wl_path.read_text())["assets"][0].get("enabled"))
    assert after != before


@pytest.mark.asyncio
async def test_system_check_screen_mounts(temp_config):
    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("system_check")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SystemCheckScreen"
        assert app.screen.query_one("#syscheck-output") is not None


@pytest.mark.asyncio
async def test_db_tools_backup_creates_file(temp_config):
    import sqlite3
    from xauby.database.db import resolve_db_path

    db_path = resolve_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE prices(symbol TEXT, timeframe TEXT, timestamp INT)")
    conn.commit()
    conn.close()

    app = XAubyTextualApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("db_tools")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DBToolsScreen"
        ol = app.screen.query_one("#qc-list", OptionList)
        ol.highlighted = ol.get_option_index("backup")
        await pilot.pause()
        ol.action_select()
        for _ in range(30):
            await pilot.pause()
    backups = list((temp_config / "backups").glob("*.bak"))
    assert len(backups) == 1
