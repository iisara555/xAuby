import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple

from xauby.runtime.trading_config import resolve_trading_config

logger = logging.getLogger("lite_bot")


class RiskMixin:
    # ---- Portfolio drawdown circuit-breaker -------------------------------
    def _drawdown_guard_cfg(self) -> Dict[str, Any]:
        return (self.config.get("risk", {}) or {}).get("drawdown_guard", {}) or {}

    def _equity_peak_scope(self, symbol: Optional[str] = None) -> str:
        try:
            mode = self._risk_execution_mode(symbol or getattr(self, "_sym", lambda: "")())
        except Exception:
            mode = ""
        if mode not in {"sim", "live"}:
            mode = "sim" if bool(getattr(self, "simulate_only", True)) else "live"
        if mode == "live":
            account_fp = str(getattr(self, "_account_fp", "") or "").strip()
            exchange_cfg = self.config.get("exchange") or {}
            exchange_id = ""
            if isinstance(exchange_cfg, dict):
                exchange_id = str(
                    exchange_cfg.get("ccxt_id")
                    or exchange_cfg.get("name")
                    or exchange_cfg.get("provider")
                    or ""
                ).strip().lower()
            if account_fp:
                return ":".join(part for part in ("live", exchange_id, account_fp) if part)
        return mode

    def _equity_peak_fallback_scopes(self, scope: str) -> list[str]:
        if scope.startswith("live:"):
            return [scope, "live"]
        return [scope]

    def _capital_flow_rebase_cfg(self) -> Dict[str, Any]:
        cfg = self._drawdown_guard_cfg()
        return {
            "enabled": bool(cfg.get("auto_rebase_capital_flows", True)),
            "min_abs": float(cfg.get("capital_flow_min_abs_usdt", 20.0) or 0.0),
            "min_pct": float(cfg.get("capital_flow_min_pct", 5.0) or 0.0) / 100.0,
        }

    def _capital_flow_symbols(self, symbol: Optional[str]) -> list[str]:
        try:
            specs = self._pair_registry.active()  # type: ignore[attr-defined]
            syms = [str(s.symbol).upper().replace("_", "") for s in specs]
            return syms or [str(symbol or self._sym()).upper().replace("_", "")]
        except Exception:
            sym = str(symbol or "").upper().replace("_", "")
            return [sym] if sym else []

    def _portfolio_flat_for_capital_flow(self, symbol: Optional[str] = None) -> bool:
        if not hasattr(self, "db"):
            return False
        symbols = self._capital_flow_symbols(symbol)
        if not symbols:
            return False
        for sym in symbols:
            if not sym:
                return False
            try:
                state = self.db.get_trade_state(sym)  # type: ignore[attr-defined]
            except Exception:
                return False
            if state.get("state") == "bought":
                return False
        return True

    def _latest_closed_trade_marker(self, symbol: Optional[str] = None) -> str:
        try:
            trades = self.db.get_closed_trades(None, limit=20)  # type: ignore[attr-defined]
        except Exception:
            trades = []
            for sym in self._capital_flow_symbols(symbol):
                try:
                    trades.extend(self.db.get_closed_trades(sym, limit=5))  # type: ignore[attr-defined]
                except Exception:
                    pass
        mode = self._risk_execution_mode(symbol or "")
        trades = self._filter_closed_trades_by_execution_mode(trades, mode)
        if not trades:
            return ""
        latest = max(
            trades,
            key=lambda t: str(t.get("closed_at") or t.get("timestamp") or ""),
        )
        return "|".join(
            str(latest.get(k, ""))
            for k in ("symbol", "closed_at", "net_pnl", "execution_mode")
        )

    def _load_equity_peak_data(self) -> Dict[str, Any]:
        from xauby.runtime.paths import equity_peak_path
        try:
            with open(equity_peak_path(), "r", encoding="utf-8") as f:
                data = json.load(f) or {}
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _load_equity_peak(self, symbol: Optional[str] = None) -> float:
        data = self._load_equity_peak_data()
        scope = self._equity_peak_scope(symbol)
        peaks = data.get("peaks")
        if isinstance(peaks, dict):
            for key in self._equity_peak_fallback_scopes(scope):
                try:
                    peak = float(peaks.get(key, 0.0) or 0.0)
                except (TypeError, ValueError):
                    peak = 0.0
                if peak > 0:
                    return peak
            return 0.0
        try:
            return float(data.get("peak", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _save_equity_peak(self, peak: float, symbol: Optional[str] = None) -> None:
        from xauby.runtime.paths import ensure_runtime_dir, equity_peak_path
        try:
            ensure_runtime_dir()
            data = self._load_equity_peak_data()
            scope = self._equity_peak_scope(symbol)
            peaks = data.get("peaks")
            if not isinstance(peaks, dict):
                peaks = {}
            peaks[scope] = float(peak)
            data["peaks"] = peaks
            data["peak"] = float(peak)
            with open(equity_peak_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Could not persist equity peak: %s", e)

    def _save_equity_peak_data(self, data: Dict[str, Any]) -> None:
        from xauby.runtime.paths import ensure_runtime_dir, equity_peak_path
        from xauby.utils.atomic_io import atomic_json_write

        try:
            ensure_runtime_dir()
            atomic_json_write(equity_peak_path(), data, indent=None)
        except Exception as e:
            logger.warning("Could not persist equity peak data: %s", e)

    def _maybe_rebase_peak_for_capital_flow(
        self,
        equity: float,
        symbol: Optional[str] = None,
    ) -> None:
        cfg = self._capital_flow_rebase_cfg()
        if not cfg["enabled"] or equity <= 0:
            return
        if not self._portfolio_flat_for_capital_flow(symbol):
            return

        scope = self._equity_peak_scope(symbol)
        marker = self._latest_closed_trade_marker(symbol)
        data = self._load_equity_peak_data()
        baselines = data.get("capital_flow_baselines")
        if not isinstance(baselines, dict):
            baselines = {}
        baseline = baselines.get(scope)
        if not isinstance(baseline, dict):
            baselines[scope] = {"equity": float(equity), "closed_trade_marker": marker}
            data["capital_flow_baselines"] = baselines
            self._save_equity_peak_data(data)
            return

        baseline_equity = float(baseline.get("equity", 0.0) or 0.0)
        baseline_marker = str(baseline.get("closed_trade_marker", "") or "")
        if marker != baseline_marker:
            baselines[scope] = {"equity": float(equity), "closed_trade_marker": marker}
            data["capital_flow_baselines"] = baselines
            self._save_equity_peak_data(data)
            return
        if baseline_equity <= 0:
            baselines[scope] = {"equity": float(equity), "closed_trade_marker": marker}
            data["capital_flow_baselines"] = baselines
            self._save_equity_peak_data(data)
            return

        delta = float(equity) - baseline_equity
        abs_delta = abs(delta)
        pct_delta = abs_delta / max(baseline_equity, 1.0)
        if abs_delta >= cfg["min_abs"] and pct_delta >= cfg["min_pct"]:
            peak = self._load_equity_peak(symbol)
            new_peak = max(float(equity), peak + delta)
            peaks = data.get("peaks")
            if not isinstance(peaks, dict):
                peaks = {}
            peaks[scope] = new_peak
            mode = self._risk_execution_mode(symbol or "")
            if mode in {"live", "sim"}:
                peaks[mode] = new_peak
            data["peaks"] = peaks
            data["peak"] = new_peak
            logger.info(
                "Drawdown guard capital-flow rebase: scope=%s baseline=%.2f equity=%.2f delta=%+.2f peak %.2f -> %.2f",
                scope,
                baseline_equity,
                equity,
                delta,
                peak,
                new_peak,
            )

        baselines[scope] = {"equity": float(equity), "closed_trade_marker": marker}
        data["capital_flow_baselines"] = baselines
        self._save_equity_peak_data(data)
        if abs_delta >= cfg["min_abs"] and pct_delta >= cfg["min_pct"]:
            caches = getattr(self, "_equity_peak_cache_by_scope", None)
            if not isinstance(caches, dict):
                caches = {}
            caches[scope] = float(data.get("peak", equity) or equity)
            self._equity_peak_cache_by_scope = caches
            self._equity_peak_cache = caches[scope]

    def update_equity_peak(self, equity: float, symbol: Optional[str] = None) -> float:
        """Grow and persist the equity high-water mark; return current peak."""
        eq = float(equity or 0.0)
        scope = self._equity_peak_scope(symbol)
        caches = getattr(self, "_equity_peak_cache_by_scope", None)
        if not isinstance(caches, dict):
            caches = {}
        cached = caches.get(scope)
        if cached is None:
            cached = self._load_equity_peak(symbol)
        data = self._load_equity_peak_data()
        peaks = data.get("peaks")
        scope_missing = not isinstance(peaks, dict) or scope not in peaks
        if eq > cached:
            cached = eq
            self._save_equity_peak(cached, symbol)
        elif scope_missing and cached > 0:
            self._save_equity_peak(cached, symbol)
        caches[scope] = cached
        self._equity_peak_cache_by_scope = caches
        self._equity_peak_cache = cached
        return cached

    def drawdown_pct(self, equity: float, symbol: Optional[str] = None) -> float:
        """Current drawdown from the high-water mark, as a positive percent."""
        eq = float(equity or 0.0)
        peak = self.update_equity_peak(eq, symbol)
        if peak <= 0 or eq <= 0:
            return 0.0
        return max(0.0, (peak - eq) / peak * 100.0)

    def check_drawdown_guard(self, equity: float, symbol: Optional[str] = None) -> Tuple[bool, str]:
        """Return (allowed, reason). Blocks when drawdown >= max_drawdown_pct."""
        cfg = self._drawdown_guard_cfg()
        if not cfg.get("enabled", False):
            return True, ""
        eq = float(equity or 0.0)
        if eq <= 0:
            return True, ""
        try:
            max_dd = float(cfg.get("max_drawdown_pct", 20.0) or 0.0)
        except (TypeError, ValueError):
            max_dd = 0.0
        if max_dd <= 0:
            self.update_equity_peak(eq, symbol)
            return True, ""
        self._maybe_rebase_peak_for_capital_flow(eq, symbol)
        dd = self.drawdown_pct(eq, symbol)
        if dd >= max_dd:
            peak = getattr(self, "_equity_peak_cache", eq)
            return False, (
                f"Drawdown guard: equity {eq:.2f} is -{dd:.2f}% from peak "
                f"{peak:.2f} (limit -{max_dd:.1f}%)"
            )
        return True, ""

    def _risk_execution_mode(self, symbol: str) -> str:
        """Execution mode used to scope trade-history risk counters.

        Per-symbol engines can run some pairs in sim while others are live. Risk
        history must not cross that boundary, or a sim loss can block live
        entries for the same symbol.
        """
        try:
            mode = self._execution_mode(symbol)  # type: ignore[attr-defined]
        except Exception:
            mode = ""
        mode = str(mode or "").lower()
        return mode if mode in {"sim", "live"} else ""

    @staticmethod
    def _filter_closed_trades_by_execution_mode(
        trades: list[Dict[str, Any]],
        execution_mode: str,
    ) -> list[Dict[str, Any]]:
        if execution_mode not in {"sim", "live"}:
            return trades
        return [
            t
            for t in trades
            if str(t.get("execution_mode") or "").lower() == execution_mode
        ]


    def _build_risk_block(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = symbol or getattr(self, "symbol", "")
        active_strategy = None
        if hasattr(self, "_strategy_name_for_symbol"):
            active_strategy = self._strategy_name_for_symbol(sym)
        eff = resolve_trading_config(
            self.config,
            active_strategy,
            symbol=sym or "",
            for_live=True,
        )
        strat_cfg = eff.strategy
        portfolio_cfg = eff.portfolio
        sc = self._sc(symbol) if symbol else None

        def _f(d: Dict[str, Any], key: str, default: float) -> float:
            try:
                return float(d.get(key, default))
            except (TypeError, ValueError):
                return default

        use_d1 = sc.use_d1_regime_filter if sc else self.use_d1_regime_filter
        risk = {
            "risk_pct": _f(portfolio_cfg, "risk_pct", 0.01),
            "sl_atr_mult": _f(strat_cfg, "sl_atr_mult", 2.0),
            "trailing_atr_mult": _f(strat_cfg, "trailing_atr_mult", 1.5),
            "breakeven_sl_enabled": bool(strat_cfg.get("breakeven_sl_enabled", False)),
            "breakeven_activation_atr_mult": _f(
                strat_cfg, "breakeven_activation_atr_mult", 1.5
            ),
            "breakeven_buffer_atr_mult": _f(strat_cfg, "breakeven_buffer_atr_mult", 0.1),
            "use_d1_regime_filter": use_d1,
        }
        # Expose only gates owned by the active strategy. Injecting CDC-shaped
        # defaults into every strategy made the dashboard describe gates that
        # were not actually used (for example BBRSI uses rsi_oversold).
        for key in (
            "rsi_min",
            "rsi_max",
            "rsi_oversold",
            "rsi_overbought",
            "rsi_exit",
            "vol_min_ratio",
            "fresh_zone_window",
            "require_fresh_zone",
            "ap_smoothing",
            "release_window",
            "momentum_min",
        ):
            if key in strat_cfg:
                risk[key] = strat_cfg[key]
        return risk

    @staticmethod
    def _closed_trade_dt(trade: Dict[str, Any]) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat(str(trade.get("closed_at", "")).replace("Z", ""))
        except Exception:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _closed_trade_is_stop_loss(trade: Dict[str, Any]) -> bool:
        trigger = str(trade.get("trigger") or "").lower()
        try:
            pnl = float(trade.get("net_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        return pnl < 0 or "stop loss" in trigger or "stop_loss" in trigger or " sl" in trigger

    @staticmethod
    def _closed_trade_is_take_profit(trade: Dict[str, Any]) -> bool:
        trigger = str(trade.get("trigger") or "").lower()
        try:
            pnl = float(trade.get("net_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        return pnl >= 0 or "take profit" in trigger or "fixed tp" in trigger or "tp reached" in trigger

    def _latest_closed_trade_for_cooldown(self, symbol: str) -> Optional[Dict[str, Any]]:
        execution_mode = self._risk_execution_mode(symbol)
        closed = self.db.get_closed_trades(symbol, limit=10)
        closed = self._filter_closed_trades_by_execution_mode(closed, execution_mode)
        return closed[0] if closed else None

    def _strategy_reentry_guard_config(self, symbol: str) -> Dict[str, Any]:
        try:
            strategy_name = self._strategy_name_for_symbol(symbol)
            cfg = self._get_strategy_config(symbol)
        except Exception:
            strategy_name = ""
            cfg = {}
        if strategy_name != "sol_ema_pullback":
            return {}
        if not bool(cfg.get("reentry_guard_enabled", False)):
            return {}
        return cfg

    def _is_buy_blocked_by_cooldown(self, symbol: Optional[str] = None) -> Tuple[bool, str]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        last = self._latest_closed_trade_for_cooldown(sym)
        if not last:
            return False, ""

        guard_cfg = self._strategy_reentry_guard_config(sym)
        if guard_cfg:
            closed_at = self._closed_trade_dt(last)
            if closed_at is None:
                return False, ""
            minutes = 0.0
            label = "exit"
            if self._closed_trade_is_stop_loss(last):
                minutes = float(guard_cfg.get("cooldown_after_sl_minutes", 0.0) or 0.0)
                label = "SL"
            elif self._closed_trade_is_take_profit(last):
                minutes = float(guard_cfg.get("cooldown_after_tp_minutes", 0.0) or 0.0)
                label = "TP"
            if minutes > 0:
                cooldown_end = closed_at + timedelta(minutes=minutes)
                now = datetime.now(timezone.utc)
                if now < cooldown_end:
                    until = cooldown_end.replace(tzinfo=None).isoformat()
                    return True, (
                        f"{sym} {label} re-entry cooldown active until {until} "
                        f"(last exit at {closed_at.replace(tzinfo=None).isoformat()})"
                    )

        if float(last.get("net_pnl", 0.0)) >= 0:
            return False, ""
        closed_at = self._closed_trade_dt(last)
        if closed_at is None:
            return False, ""

        tf = "4h"
        if sym:
            try:
                tf = self._sc(sym).primary_timeframe
            except Exception:
                pass

        # Parse tf to minutes
        unit = tf[-1].lower()
        try:
            val = int(tf[:-1])
        except ValueError:
            val = 4
        if unit == 'm':
            tf_mins = val
        elif unit == 'h':
            tf_mins = val * 60
        elif unit == 'd':
            tf_mins = val * 1440
        else:
            tf_mins = 240

        cooldown_bars = int(self.config.get("trading", {}).get("cooldown_bars", 1))

        now_ts = int(datetime.now(timezone.utc).timestamp())
        closed_at_ts = int(closed_at.timestamp())
        closed_bar_start_ts = (closed_at_ts // (tf_mins * 60)) * (tf_mins * 60)
        cooldown_end_ts = closed_bar_start_ts + (cooldown_bars * tf_mins * 60)

        if now_ts < cooldown_end_ts:
            cooldown_end_dt = datetime.fromtimestamp(cooldown_end_ts, tz=timezone.utc).replace(tzinfo=None)
            return True, f"Loss cooldown active until {cooldown_end_dt.isoformat()} (last loss at {closed_at.isoformat()})"
        return False, ""

    def check_daily_protections(
        self,
        current_equity: float,
        symbol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        max_consecutive_losses = self.config.get("trading", {}).get("max_consecutive_losses")
        per_symbol_losses = self.config.get("trading", {}).get("max_consecutive_losses_by_symbol") or {}
        if isinstance(per_symbol_losses, dict):
            symbol_limit = per_symbol_losses.get(sym)
            if symbol_limit is None:
                symbol_limit = per_symbol_losses.get(sym.replace("USDT", ""))
            if symbol_limit is not None:
                max_consecutive_losses = symbol_limit
        max_daily_loss_pct = self.config.get("trading", {}).get("max_daily_loss_pct")

        if max_consecutive_losses is None and max_daily_loss_pct is None:
            return True, ""

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        execution_mode = self._risk_execution_mode(sym)

        # 1. Check consecutive losses in the last 24 hours (per-pair streak protection)
        if max_consecutive_losses is not None:
            closed_trades_pair = self.db.get_closed_trades(sym, limit=50)
            closed_trades_pair = self._filter_closed_trades_by_execution_mode(
                closed_trades_pair,
                execution_mode,
            )
            day_trades_pair = []
            for t in closed_trades_pair:
                try:
                    closed_dt = datetime.fromisoformat(t["closed_at"])
                    if (now_naive - closed_dt).total_seconds() <= 86400:
                        day_trades_pair.append(t)
                except Exception as e:
                    logger.error(f"Error parsing closed_at date '{t.get('closed_at')}': {e}")

            consec_losses = 0
            for t in day_trades_pair:
                pnl = t.get("net_pnl", 0.0)
                if pnl < 0:
                    consec_losses += 1
                    if consec_losses >= int(max_consecutive_losses):
                        mode_note = f" ({execution_mode})" if execution_mode else ""
                        return False, f"Hit max consecutive losses{mode_note} ({consec_losses} >= {max_consecutive_losses}) in the last 24h"
                else:
                    break

        # 2. Check total daily loss percentage (global portfolio-wide protection)
        if max_daily_loss_pct is not None and current_equity > 0:
            closed_trades_global = self.db.get_closed_trades(None, limit=200)
            closed_trades_global = self._filter_closed_trades_by_execution_mode(
                closed_trades_global,
                execution_mode,
            )
            day_trades_global = []
            for t in closed_trades_global:
                try:
                    closed_dt = datetime.fromisoformat(t["closed_at"])
                    if (now_naive - closed_dt).total_seconds() <= 86400:
                        day_trades_global.append(t)
                except Exception as e:
                    logger.error(f"Error parsing closed_at date '{t.get('closed_at')}': {e}")

            total_net_pnl = sum(t.get("net_pnl", 0.0) for t in day_trades_global)
            if total_net_pnl < 0:
                loss_pct = (abs(total_net_pnl) / current_equity) * 100.0
                if loss_pct >= float(max_daily_loss_pct):
                    mode_note = f" ({execution_mode})" if execution_mode else ""
                    return False, f"Daily PnL{mode_note} ({total_net_pnl:.2f} USDT, -{loss_pct:.2f}%) exceeds limit (-{max_daily_loss_pct}%) in the last 24h"

        return True, ""
