"""Native Textual widgets for the Backtest screen."""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static
from textual.widget import Widget

from xauby.backtest.presenter import (
    PHONE_MAX_TRADES,
    BacktestViewModel,
    build_backtest_view_model,
    format_parity_lines,
    format_summary_lines,
)
from xauby.ui.textual_tui.layout import is_phone_layout, layout_width_tier, sync_backtest_layout
from xauby.backtest.best_params import load_best_params
from xauby.backtest.engine import optimize_grid_for_symbol
from xauby.backtest.runtime_config import runtime_config_fingerprint
from xauby.backtest.service import BacktestRunResult, run_focused_backtest
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.trading_config import strategy_name_for_symbol
from xauby.ui.textual_tui.state_sync import fallback_bot_state, load_bot_state_from_disk
from xauby.ui.state_view import default_symbol_from_whitelist
from xauby.utils.colors import C_BOLD, C_GREEN, C_MUTED, C_RED, C_RESET, C_YELLOW

_log = logging.getLogger(__name__)

_CACHE_MAX_ENTRIES = 6


class _Section(Static):
    """A titled section panel that renders ANSI lines correctly."""

    def __init__(self, title: str, **kwargs):
        super().__init__("", **kwargs)
        self.border_title = title
        self.styles.border = ("round", "#3f3f46")
        self.styles.padding = (0, 1)

    def update_ansi(self, lines: list[str]) -> None:
        from rich.text import Text

        text = Text.from_ansi("\n".join(lines)) if lines else Text("")
        self.update(text)


class BacktestNativeBody(Widget):
    """Backtest screen: summary + parity check for the focused pair."""

    CAN_FOCUS = False

    state = reactive({})
    focus_symbol = reactive("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._backtest_running = False
        self._optimize_running = False
        self._cache: Dict[Tuple[str, str, str], BacktestRunResult] = {}
        self._layout_tier = ""
        self._render_fp: Tuple[Any, ...] = ()

    def is_running(self) -> bool:
        return self._backtest_running or self._optimize_running

    def show_running_state(self) -> None:
        sym = self._effective_symbol()
        strat = self._effective_strategy()
        w = max(38, self.size.width)
        running_vm = BacktestViewModel(
            symbol=sym,
            strategy_name=strat,
            period_label="",
            total_trades=0,
            net_return_pct=0.0,
            profit_factor=0.0,
            win_rate_pct=0.0,
            max_drawdown_pct=0.0,
            status="running",
        )
        self._update_panels(running_vm, w)

    def compose(self):
        with Horizontal(id="backtest-split"):
            with Vertical(id="backtest-left-col"):
                yield _Section("BACKTEST SUMMARY", id="bt-summary")
                yield _Section("PARITY CHECK", id="bt-parity")
                yield _Section("EQUITY CURVE", id="bt-curve")
            with Vertical(id="backtest-right-col"):
                yield _Section("SIMULATED TRADES", id="bt-trades")

    def on_mount(self) -> None:
        w = self.size.width
        self._layout_tier = layout_width_tier(w)
        self._sync_layout(w)
        self._render_idle()

    def on_resize(self, event) -> None:
        width = event.size.width
        tier = layout_width_tier(width)
        self._sync_layout(width)
        if tier != self._layout_tier:
            self._layout_tier = tier
            self._render_fp = ()
            if not self.is_running():
                self._render_cached_or_idle()

    def _sync_layout(self, width: int) -> None:
        try:
            split = self.query_one("#backtest-split")
            sync_backtest_layout(split, width)
            curve = self.query_one("#bt-curve", _Section)
            if is_phone_layout(width):
                curve.add_class("phone-hidden")
            else:
                curve.remove_class("phone-hidden")
        except Exception:
            pass

    def _is_phone_mode(self, width: int) -> bool:
        return is_phone_layout(max(38, width))

    def _trim_cache(self) -> None:
        if len(self._cache) <= _CACHE_MAX_ENTRIES:
            return
        oldest = next(iter(self._cache))
        del self._cache[oldest]

    def _render_fingerprint(self, vm: BacktestViewModel, width: int) -> Tuple[Any, ...]:
        tier = layout_width_tier(width)
        return (
            tier,
            vm.status,
            vm.symbol,
            vm.strategy_name,
            round(vm.net_return_pct, 2),
            vm.total_trades,
            len(vm.trades),
            tuple(sorted((vm.config_used or {}).items())),
            tuple(sorted((vm.best_params or {}).items())),
            vm.error,
        )

    def watch_focus_symbol(self, _sym: str) -> None:
        self._render_cached_or_idle()

    def _effective_symbol(self) -> str:
        if self.focus_symbol:
            return str(self.focus_symbol).upper().replace("_", "")
        state = self.state or {}
        sym = str(state.get("symbol") or state.get("focus_symbol") or "")
        if sym:
            return sym.upper().replace("_", "")
        _, _, focus, _ = load_bot_state_from_disk()
        if focus:
            return str(focus).upper().replace("_", "")
        return default_symbol_from_whitelist()

    def _effective_strategy(self) -> str:
        sym = self._effective_symbol()
        try:
            name = strategy_name_for_symbol(load_bot_config(), sym)
            if name:
                return name
        except Exception:
            pass
        state = self.state or {}
        if name := str(state.get("strategy_name") or "").strip():
            return name
        fb = fallback_bot_state(focus_symbol=sym)
        return str(fb.get("strategy_name") or "cdc_action_zone")

    def _cache_key(self) -> Tuple[str, str, str]:
        fp = runtime_config_fingerprint(self.state or {})
        return (self._effective_symbol(), self._effective_strategy(), fp)

    def _render_idle(self) -> None:
        sym = self._effective_symbol()
        w = max(38, self.size.width)
        vm = BacktestViewModel(symbol=sym, strategy_name="", period_label="", total_trades=0,
                               net_return_pct=0.0, profit_factor=0.0, win_rate_pct=0.0,
                               max_drawdown_pct=0.0, status="idle")
        self._update_panels(vm, w)

    def _render_cached_or_idle(self) -> None:
        if self.is_running():
            return
        key = self._cache_key()
        cached = self._cache.get(key)
        w = max(38, self.size.width)
        if cached is not None:
            vm = build_backtest_view_model(cached, status="done")
            vm.symbol = key[0]
            self._update_panels(vm, w)
        else:
            self._render_idle()

    def _update_panels(self, vm: BacktestViewModel, width: int, *, force: bool = False) -> None:
        fp = self._render_fingerprint(vm, width)
        if not force and fp == self._render_fp:
            return
        self._render_fp = fp
        phone = self._is_phone_mode(width)
        try:
            self.query_one("#bt-summary", _Section).update_ansi(
                format_summary_lines(vm, width, compact=phone)
            )
            self.query_one("#bt-parity", _Section).update_ansi(
                format_parity_lines(vm, width, compact=phone)
            )
            self.query_one("#bt-trades", _Section).update_ansi(
                self._format_trades_list_lines(vm, width, phone=phone)
            )
            if phone:
                self.query_one("#bt-curve", _Section).update_ansi([])
            else:
                self.query_one("#bt-curve", _Section).update_ansi(
                    self._format_curve_lines(vm, width)
                )
        except Exception as exc:
            _log.exception("Failed to update backtest panels")
            try:
                err_lines = [
                    f"{C_RED}UI update error: {exc}{C_RESET}",
                    f"{C_MUTED}Try pressing [R] again.{C_RESET}",
                ]
                self.query_one("#bt-summary", _Section).update_ansi(err_lines)
            except Exception:
                pass

    def _format_trades_list_lines(
        self,
        vm: BacktestViewModel,
        width: int,
        *,
        phone: bool = False,
    ) -> list[str]:
        if vm.status == "idle":
            return ["", f"  {C_MUTED}Run backtest to view simulated trades.{C_RESET}"]
        if vm.status == "running":
            return ["", f"  {C_YELLOW}Running simulation...{C_RESET}"]
        if vm.status == "error":
            return ["", f"  {C_RED}Error running simulation.{C_RESET}"]

        if not vm.trades:
            return ["", f"  {C_MUTED}No trades executed during the simulated period.{C_RESET}"]

        trades = vm.trades
        omitted = 0
        if phone:
            if len(trades) > PHONE_MAX_TRADES:
                omitted = len(trades) - PHONE_MAX_TRADES
                trades = trades[-PHONE_MAX_TRADES:]

        if phone:
            lines = [
                f"{C_BOLD}{'Time':<9} {'PnL':>9} {'PnL%':>7} {'Trig':<8}{C_RESET}",
                f"{C_MUTED}{'-' * 36}{C_RESET}",
            ]
            from datetime import datetime
            from xauby.utils.common import TH_TZ

            for t in trades:
                ts = t.get("exit_time", 0)
                try:
                    time_str = datetime.fromtimestamp(ts, tz=TH_TZ).strftime("%m-%d %H:%M")
                except Exception:
                    time_str = "—"
                pnl_val = t.get("pnl", 0.0)
                pnl_pct = t.get("pnl_pct", 0.0)
                row_color = C_GREEN if pnl_val >= 0 else C_RED
                trig = str(t.get("trigger") or "?")[:8]
                lines.append(
                    f"{C_MUTED}{time_str:<9}{C_RESET} "
                    f"{row_color}{pnl_val:+8.2f}{C_RESET} "
                    f"{row_color}{pnl_pct:+6.2f}%{C_RESET} "
                    f"{C_MUTED}{trig:<8}{C_RESET}"
                )
            if omitted:
                lines.append(f"{C_MUTED}  … {omitted} older trades hidden (mobile){C_RESET}")
            return lines

        # Header definition (clean text length)
        col_widths = {
            "time": 11,
            "trigger": 12,
            "entry": 11,
            "exit": 11,
            "pnl": 12,
            "pnl_pct": 10
        }

        header_time = f"{'Time':<{col_widths['time']}}"
        header_trig = f"{'Exit Trigger':<{col_widths['trigger']}}"
        header_ent  = f"{'Entry Price':>{col_widths['entry']}}"
        header_ex   = f"{'Exit Price':>{col_widths['exit']}}"
        header_pnl  = f"{'PnL (USDT)':>{col_widths['pnl']}}"
        header_pct  = f"{'PnL%':>{col_widths['pnl_pct']}}"

        header_line = f"{C_BOLD}{header_time}  {header_trig}  {header_ent}  {header_ex}  {header_pnl}  {header_pct}{C_RESET}"

        sep_len = sum(col_widths.values()) + 10  # 2 spaces between columns (5 separators)
        sep_line = f"{C_MUTED}{'-' * sep_len}{C_RESET}"

        lines = [header_line, sep_line]

        from datetime import datetime
        from xauby.utils.common import TH_TZ

        for t in trades:
            # Format time
            ts = t.get("exit_time", 0)
            try:
                dt = datetime.fromtimestamp(ts, tz=TH_TZ)
                time_str = dt.strftime("%m-%d %H:%M")
            except Exception:
                time_str = "—"

            # Format trigger
            trig_str = str(t.get("trigger") or "UNKNOWN")
            if len(trig_str) > col_widths["trigger"]:
                trig_str = trig_str[:col_widths["trigger"] - 2] + ".."

            # Format prices
            entry_p = t.get("entry_price", 0.0)
            exit_p = t.get("exit_price", 0.0)
            entry_str = f"{entry_p:,.2f}"
            exit_str = f"{exit_p:,.2f}"

            # Format PnL
            pnl_val = t.get("pnl", 0.0)
            pnl_pct = t.get("pnl_pct", 0.0)
            pnl_str = f"{pnl_val:+,.2f}"
            pnl_pct_str = f"{pnl_pct:+.2f}%"

            # Determine color based on pnl
            row_color = C_GREEN if pnl_val >= 0 else C_RED

            # Pad values to clean lengths
            time_padded = f"{time_str:<{col_widths['time']}}"
            trig_padded = f"{trig_str:<{col_widths['trigger']}}"
            entry_padded = f"{entry_str:>{col_widths['entry']}}"
            exit_padded = f"{exit_str:>{col_widths['exit']}}"
            pnl_padded = f"{pnl_str:>{col_widths['pnl']}}"
            pnl_pct_padded = f"{pnl_pct_str:>{col_widths['pnl_pct']}}"

            # Construct row with colors
            row = (
                f"{C_MUTED}{time_padded}{C_RESET}  "
                f"{C_MUTED}{trig_padded}{C_RESET}  "
                f"{entry_padded}  "
                f"{exit_padded}  "
                f"{row_color}{pnl_padded}{C_RESET}  "
                f"{row_color}{pnl_pct_padded}{C_RESET}"
            )
            lines.append(row)

        return lines

    def _format_curve_lines(self, vm: BacktestViewModel, width: int) -> list[str]:
        if vm.status == "idle":
            return ["", f"  {C_MUTED}Run backtest to view Equity Curve.{C_RESET}"]
        if vm.status == "running":
            return ["", f"  {C_YELLOW}Running simulation...{C_RESET}"]
        if vm.status == "error":
            return ["", f"  {C_RED}Error running simulation.{C_RESET}"]

        if not vm.trades:
            return ["", f"  {C_MUTED}No trades executed to plot Equity Curve.{C_RESET}"]

        from datetime import datetime
        from xauby.utils.common import TH_TZ
        from xauby.ui.chart import get_capital_curve_lines

        # Map simulated trades to format expected by get_capital_curve_lines
        chrono_trades = []
        for t in vm.trades:
            chrono_trades.append({
                "net_pnl": t.get("pnl", 0.0),
                "closed_at": datetime.fromtimestamp(t.get("exit_time", 0), tz=TH_TZ).isoformat(),
            })

        # Reverse to have newest first, since get_capital_curve_lines will reverse it back to oldest first
        chrono_trades.reverse()

        left_col_width = min(50, max(38, int(width * 0.38))) if width >= 110 else width
        curve_width = max(20, left_col_width - 18)

        try:
            return list(
                get_capital_curve_lines(
                    None,
                    vm.symbol,
                    width=curve_width,
                    height=5,
                    trades=chrono_trades,
                )
            )
        except Exception as e:
            return [f"  Failed to plot curve: {e}"]

    def show_optimizing_state(self) -> None:
        sym = self._effective_symbol()
        strat = self._effective_strategy()
        w = max(38, self.size.width)
        running_vm = BacktestViewModel(
            symbol=sym,
            strategy_name=strat,
            period_label="",
            total_trades=0,
            net_return_pct=0.0,
            profit_factor=0.0,
            win_rate_pct=0.0,
            max_drawdown_pct=0.0,
            status="running",
        )
        try:
            from xauby.backtest.presenter import format_running_summary_lines

            lines = format_running_summary_lines(
                sym, w, mode="optimize", compact=self._is_phone_mode(w)
            )
            self.query_one("#bt-summary", _Section).update_ansi(lines)
        except Exception:
            self._update_panels(running_vm, w)

    @staticmethod
    def _free_ram_mb() -> float:
        """Return available RAM in MB (0 if psutil unavailable)."""
        try:
            import psutil
            return psutil.virtual_memory().available / 1024 / 1024
        except Exception:
            return 0.0

    async def _run_backtest(self) -> None:
        if self._backtest_running:
            return

        free_mb = self._free_ram_mb()
        if free_mb > 0 and free_mb < 120:
            self._update_status_msg(
                f"\033[1;31m⚠ Low RAM ({free_mb:.0f} MB free) — backtest skipped to prevent freeze.\033[0m"
            )
            return

        self._backtest_running = True
        sym = self._effective_symbol()
        strat = self._effective_strategy()
        w = max(38, self.size.width)

        import asyncio, gc, time as _time

        _ticker_task = None

        async def _elapsed_ticker_bt():
            start = _time.monotonic()
            while True:
                await asyncio.sleep(1)
                elapsed = int(_time.monotonic() - start)
                self._update_status_msg(
                    f"\033[1;36m⏳ Running {sym}... {elapsed}s\033[0m"
                )

        try:
            _ticker_task = asyncio.create_task(_elapsed_ticker_bt())
            result = await asyncio.to_thread(
                run_focused_backtest,
                sym,
                strategy_name=strat,
                bot_state=self.state or None,
            )
            self._cache[self._cache_key()] = result
            self._trim_cache()
            vm = build_backtest_view_model(result, status="done")
            self._update_panels(vm, w, force=True)
        except Exception as exc:
            err_vm = BacktestViewModel(
                symbol=sym,
                strategy_name=strat,
                period_label="",
                total_trades=0,
                net_return_pct=0.0,
                profit_factor=0.0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
                status="error",
                error=str(exc),
            )
            self._update_panels(err_vm, w, force=True)
        finally:
            if _ticker_task is not None:
                _ticker_task.cancel()
            self._backtest_running = False
            gc.collect()

    async def _run_optimize(self) -> None:
        if self._optimize_running:
            return

        free_mb = self._free_ram_mb()
        if free_mb > 0 and free_mb < 150:
            self._update_status_msg(
                f"\033[1;31m⚠ Low RAM ({free_mb:.0f} MB free) — optimization skipped to prevent freeze.\033[0m"
            )
            return

        self._optimize_running = True
        sym = self._effective_symbol()
        strat = self._effective_strategy()
        w = max(38, self.size.width)

        import asyncio, gc, time as _time

        _ticker_task = None

        async def _elapsed_ticker_opt():
            start = _time.monotonic()
            while True:
                await asyncio.sleep(2)
                elapsed = int(_time.monotonic() - start)
                self._update_status_msg(
                    f"\033[1;36m⏳ Optimizing {sym}... {elapsed}s\033[0m"
                )

        phone = self._is_phone_mode(w)
        try:
            _ticker_task = asyncio.create_task(_elapsed_ticker_opt())

            def _progress(run_idx: int, total_runs: int) -> None:
                self.app.call_from_thread(
                    self._update_status_msg,
                    f"\033[1;36mOptimizing {sym}: {run_idx}/{total_runs} runs...\033[0m",
                )

            best = await asyncio.to_thread(
                optimize_grid_for_symbol,
                sym,
                strategy_name=strat,
                bot_state=self.state or None,
                progress_callback=_progress,
                compact=phone,
            )
            if not best:
                self._update_status_msg(
                    "\033[1;31mOptimization produced no results. Try [R] replay first.\033[0m"
                )
                return
            result = await asyncio.to_thread(
                run_focused_backtest,
                sym,
                strategy_name=strat,
                bot_state=self.state or None,
                strat_cfg_override={
                    "sl_atr_mult": best.get("sl_atr_mult"),
                    "rsi_min": best.get("rsi_min"),
                    "rsi_max": best.get("rsi_max"),
                    "vol_min_ratio": best.get("vol_min_ratio"),
                },
            )
            self._cache[self._cache_key()] = result
            self._trim_cache()
            vm = build_backtest_view_model(result, status="done")
            self._update_panels(vm, w, force=True)
            self._update_status_msg(
                f"\033[1;32mOptimization done for {sym}. "
                f"Best NP {best.get('net_profit_pct', 0.0):+.1f}%\033[0m"
            )
        except Exception as exc:
            err_vm = BacktestViewModel(
                symbol=sym,
                strategy_name=strat,
                period_label="",
                total_trades=0,
                net_return_pct=0.0,
                profit_factor=0.0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
                status="error",
                error=str(exc),
            )
            self._update_panels(err_vm, w, force=True)
        finally:
            if _ticker_task is not None:
                _ticker_task.cancel()
            self._optimize_running = False
            gc.collect()

    def apply_best_config(self) -> None:
        sym = self._effective_symbol()
        best_params = load_best_params(sym)
        if not best_params:
            self._update_status_msg(
                f"\033[1;31mNo best params for {sym}! Press [G] to optimize first.\033[0m"
            )
            return

        strat = self._effective_strategy()
        success, msg = _apply_best_config_to_yaml(sym, strat, best_params)
        if success:
            self._update_status_msg(f"\033[1;32m{msg}\033[0m")
            self._render_cached_or_idle()
        else:
            self._update_status_msg(f"\033[1;31m{msg}\033[0m")

    def _update_status_msg(self, msg: str) -> None:
        try:
            sym = self._effective_symbol()
            w = max(38, self.size.width)
            key = self._cache_key()
            cached = self._cache.get(key)
            if cached is not None:
                vm = build_backtest_view_model(cached, status="done")
                vm.symbol = key[0]
            else:
                vm = BacktestViewModel(symbol=sym, strategy_name="", period_label="", total_trades=0,
                                       net_return_pct=0.0, profit_factor=0.0, win_rate_pct=0.0,
                                       max_drawdown_pct=0.0, status="idle")

            lines = format_summary_lines(vm, w, compact=self._is_phone_mode(w))
            notification_lines = ["", f"  {msg}", ""] + lines
            self.query_one("#bt-summary", _Section).update_ansi(notification_lines)
            self._render_fp = ()
        except Exception:
            pass


def _apply_best_config_to_yaml(symbol: str, strategy_name: str, best_params: dict) -> tuple[bool, str]:
    import os
    from ruamel.yaml import YAML

    config_path = "bot_config.yaml"
    if not os.path.exists(config_path):
        return False, "bot_config.yaml not found"

    try:
        config_keys = {
            "ap_smoothing",
            "require_fresh_zone",
            "fresh_zone_window",
            "sl_atr_mult",
            "rsi_min",
            "rsi_max",
            "vol_min_ratio",
            "trailing_atr_mult",
            "breakeven_sl_enabled",
            "breakeven_activation_atr_mult",
            "breakeven_buffer_atr_mult",
        }
        filtered = {
            key: val
            for key, val in best_params.items()
            if key in config_keys
        }
        if not filtered:
            return False, f"No strategy parameters found in best config for {symbol}"

        updated_targets = []

        yaml = YAML()
        yaml.preserve_quotes = True
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                doc = yaml.load(f)

            strategy_root = doc.setdefault("strategy", {})
            symbol_overrides = strategy_root.setdefault("symbols", {})
            sym = str(symbol or "").upper().replace("_", "")
            params = symbol_overrides.setdefault(sym, {})
            params.update(filtered)
            updated_targets.append(f"bot_config.yaml:strategy.symbols.{sym}")

            strat_block = doc.get("strategies", {}).get(strategy_name)
            if strat_block is not None:
                updated_keys = set()
                for key, new_val in filtered.items():
                    if key in strat_block:
                        strat_block[key] = new_val
                        updated_keys.add(key)

                if updated_keys:
                    updated_targets.append(f"bot_config.yaml:{strategy_name}")

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(doc, f)

        if not updated_targets:
            return False, f"No matching parameters found for {symbol} / {strategy_name}"

        return True, f"Saved recommended parameters to {', '.join(updated_targets)}!"
    except Exception as e:
        return False, f"Error updating config: {e}"
