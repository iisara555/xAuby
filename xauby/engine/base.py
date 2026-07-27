import os
import time
import yaml
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from dotenv import load_dotenv

from xauby.runtime.paths import config_root, env_file

# Load this instance's .env (under XAUBY_CONFIG_DIR when set; cwd otherwise).
# Falls back to dotenv's default upward search when the resolved file is absent
# so existing single-instance layouts are unchanged.
_dotenv_path = env_file()
load_dotenv(_dotenv_path if os.path.exists(_dotenv_path) else None)

from xauby.observability import (
    get_run_id,
)
from xauby.observability.emitter import EventEmitter
from xauby.observability.state import StateExporter
from xauby.observability.store import EventStore

logger = logging.getLogger("lite_bot")

from xauby.database import create_database
from xauby.meta import PRODUCT_NAME
from xauby.strategies import load_strategy
from xauby.strategies.base import strategy_maturity
from xauby.strategies.sandbox import StrategyRunner

from xauby.api.interface import IExchangeGateway
from xauby.api.utils import find_symbol_entry
from xauby.storage.interface import IDatabaseRepository
from xauby.notifications.interface import INotificationService
from xauby.notifications.settings import NotificationSettings
from xauby.notifications.scheduler import ReportScheduler
from xauby.runtime.pair_registry import PairRegistry
from xauby.runtime.architecture_config import whitelist_strict
from xauby.runtime.config_error import ConfigError
from xauby.runtime.exchange_config import resolve_fee_pct, resolve_quote_asset
from xauby.runtime.trading_config import (
    portfolio_config_block,
    resolve_trading_config,
    strategy_name_for_symbol,
)
from xauby.engine.symbol_context import SymbolContext
from xauby.engine.regime_router import RegimeRouter


class BaseEngine:
    def __init__(
        self,
        config_path: str = "bot_config.yaml",
        client: Optional[IExchangeGateway] = None,
        db: Optional[IDatabaseRepository] = None,
        notification_service: Optional[INotificationService] = None,
    ):
        # Per-instance config dir (XAUBY_CONFIG_DIR) scopes bot_config.yaml,
        # coin_whitelist.json and .env together. Only re-root a relative
        # config_path when an instance dir is set, so default/test paths and
        # absolute paths behave exactly as before.
        if (
            not os.path.isabs(config_path)
            and os.environ.get("XAUBY_CONFIG_DIR")
        ):
            config_path = os.path.join(config_root(), config_path)
        self.config_path = config_path
        self.config = {}
        self.load_config()

        # Multi-pair registry — whitelist resolves under the same config root.
        self._project_root = config_root()
        self._pair_registry = PairRegistry(
            self.config, self.config_path, project_root=self._project_root
        )
        self.contexts: Dict[str, SymbolContext] = {}
        self._symbol_snapshots: Dict[str, Dict[str, Any]] = {}
        self._last_price_flush_mono: float = 0.0
        self._price_flush_interval_s: float = self._load_price_flush_interval()
        self._active_tick_symbol: Optional[str] = None
        self._candle_sync_last: Dict[tuple[str, str], float] = {}
        self._chart_cache_keys: Dict[str, tuple] = {}
        self._latency_metrics: Dict[str, Any] = {
            "tick_duration_ms": 0,
            "sync_candles_ms": 0,
            "state_export_ms": 0,
            "strategy_ms": 0,
            "regime_ms": 0,
            "ws_tick_age_ms": 0,
            "event_store_ms": 0,
        }

        self.simulate_only = self.config.get("simulate_only", True)
        self.read_only = self.config.get("read_only", False)
        
        # Override simulate_only from env if present
        if os.environ.get("SIMULATE_ONLY") is not None:
            self.simulate_only = os.environ.get("SIMULATE_ONLY").lower() in ("true", "1", "yes")
        if os.environ.get("BOT_READ_ONLY") is not None:
            self.read_only = os.environ.get("BOT_READ_ONLY").lower() in ("true", "1", "yes")

        # Live trading safety fence
        self.live_trading_allowed = os.environ.get("LIVE_TRADING", "false").lower() == "true"
        if not self.simulate_only and not self.live_trading_allowed:
            logger.warning("LIVE_TRADING is false in .env but simulate_only is false in config. FORCING simulate_only=True for safety.")
            self.simulate_only = True

        # API settings — credentials resolve from exchange-driven env names.
        from xauby.runtime.exchange_config import resolve_exchange_credentials
        api_key, api_secret, base_url = resolve_exchange_credentials(self.config)
        # Fingerprint the account (not the secret) for the cross-instance live
        # lock — two live engines on one account would double real risk.
        self._account_fp = self._account_fingerprint(api_key)
        self._account_lock_fp = None

        # Initialize clients via DI or defaults
        from xauby.database.db import resolve_db_path
        from xauby.api import create_exchange_client, create_exchange_websocket
        config_db_path = self.config.get("database", {}).get("path")
        self.db = db or create_database(config_db_path or resolve_db_path())
        self.client = client or create_exchange_client(self.config, api_key, api_secret, base_url)
        self._exchange_fee_pct_by_symbol: Dict[str, float] = {}
        self._exchange_maker_fee_pct_by_symbol: Dict[str, float] = {}
        
        # Simulated Portfolio settings
        self.initial_balance = float(self.config.get("portfolio", {}).get("initial_balance", 42.0))
        config_name = os.path.splitext(os.path.basename(self.config_path))[0]
        from xauby.runtime.paths import runtime_path
        self.sim_balance_file = self.config.get("portfolio", {}).get("sim_balance_file") or runtime_path(f"simulated_balance_{config_name}.json")
        
        # Legacy single-pair view (focus symbol) for Telegram / tests
        self._tick_lock = threading.Lock()
        self.indicators_cache = {}
        self.last_signal_meta: Dict[str, Any] = {}

        # ---- Strategy plugins (isolated per symbol; no shared strategy state) ----
        sandbox_timeout = float(
            self.config.get("strategy", {}).get("sandbox_timeout", 5.0)
        )
        self._strategy_sandbox_timeout = sandbox_timeout
        from xauby.runtime.architecture_config import (
            strategy_sandbox_strict,
            strategy_validate_strict,
        )
        self._strategy_sandbox_strict = strategy_sandbox_strict(self.config)
        self._strategy_validate_strict = strategy_validate_strict(self.config)
        self._strategies_by_symbol: Dict[str, Any] = {}
        self._runners_by_symbol: Dict[str, StrategyRunner] = {}
        self._strategy_names_by_symbol: Dict[str, str] = {}
        self._started_strategy_symbols: set[str] = set()
        self.strategy = None
        self.runner = None
        self._pending_live_gate_blocks: list[tuple[str, str]] = []

        self._init_pair_runtime()
        self._validate_derivatives_runtime()
        self._load_live_exchange_fees()
        if self.focus_symbol in self.contexts:
            self.strategy = self._get_strategy_for_symbol(self.focus_symbol)
            self.runner = self._get_runner_for_symbol(self.focus_symbol)
        self._start_loaded_strategies()

        sl_confirm_ticks = int(self.config.get("trading", {}).get("sl_confirm_ticks", 3))
        self._sl_breach_threshold = max(1, sl_confirm_ticks)

        # Async sentiment guard
        self._guard_score = 0.0
        self._guard_lock = threading.Lock()
        self._guard_spawn_lock = threading.Lock()
        self._guard_last_run = 0.0
        self._guard_thread: Optional[threading.Thread] = None
        guard_interval_hours = float(
            self.config.get("macro_sentiment_guard", {}).get("check_interval_hours", 4)
        )
        self._guard_interval = max(600.0, guard_interval_hours * 3600)

        # WebSocket disconnect tracking
        self._ws_disconnected_at = 0.0
        self._ws_disconnect_reason = "unknown"
        self._ws_disconnect_alerted_for = 0.0
        self._ws_disconnect_alert_pending_for = 0.0
        self._ws_disconnect_alert_timer: Optional[threading.Timer] = None
        self._ws_status_lock = threading.Lock()
        self._ws_disconnect_alert_after_seconds = float(
            (self.config.get("websocket", {}) or {}).get("disconnect_alert_after_seconds", 45.0)
        )
        self._ws_buy_block_seconds = float(
            self.config.get("trading", {}).get("ws_reconnect_buy_block_seconds", 120.0)
        )
        self._protection_alert_last: Dict[tuple[str, str], float] = {}

        # Balance cache
        self._balance_cache: Dict[str, Any] = {}
        self._balance_lock = threading.Lock()
        self._balance_last_update = 0.0
        # Incremented whenever order flow invalidates the cache.  Background
        # refreshes capture the generation before their REST call and may only
        # publish the result if it is still current; this prevents an in-flight
        # pre-close snapshot from overwriting a post-close invalidation.
        self._balance_cache_generation = 0
        self._balance_cache_ttl = float(
            self.config.get("trading", {}).get("balance_cache_ttl_seconds", 30.0)
        )
        self._balance_refresh_thread: Optional[threading.Thread] = None
        self._last_equity: Optional[float] = None
        self._sim_balance_lock = threading.Lock()
        self._symbol_filter_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._symbol_filter_lock = threading.RLock()

        from xauby.engine.brokers import LiveBroker, SimBroker

        self._sim_broker = SimBroker(
            balance_file=self.sim_balance_file,
            initial_balance=self.initial_balance,
            fee_resolver=self._symbol_fee_pct,
            funding_rate_8h=float((self.config.get("derivatives") or {}).get("sim_funding_rate_8h", 0.0001)),
        )
        exec_cfg = self.config.get("execution", {}) or {}
        self._live_broker = LiveBroker(
            client=self.client,
            place_sl_fn=lambda qty, stop, symbol="": (
                self._place_sl_with_retry(qty, stop, symbol=symbol)
                if hasattr(self, "_place_sl_with_retry")
                else None
            ),
            quote_asset=self._quote_asset(),
            order_timeout_seconds=float(exec_cfg.get("order_timeout_seconds", 10.0) or 10.0),
            order_poll_initial_seconds=float(exec_cfg.get("order_poll_initial_seconds", 0.5) or 0.5),
            order_poll_max_seconds=float(exec_cfg.get("order_poll_max_seconds", 2.0) or 2.0),
        )
        self._regime_router: Optional[RegimeRouter] = None
        try:
            self._regime_router = RegimeRouter(
                self.config,
                history_count_fn=lambda: self.db.count_regime_history(),
            )
        except Exception:
            self._regime_router = RegimeRouter(self.config)

        # Engine single-instance lock
        self._lock_fp = None

        # Config sanity checks. Portfolio owns sizing; trading mirrors values
        # only for older call sites that have not migrated yet.
        trading_cfg = self.config.setdefault("trading", {})
        portfolio_cfg = self.config.setdefault("portfolio", {})
        sizing_cfg = portfolio_cfg.setdefault("position_sizing", {})
        effective_portfolio = portfolio_config_block(self.config)
        risk_cfg = self.config.get("risk", {}) or {}
        n_pairs = len(self._pair_registry.active_symbols()) or len(
            (self.config.get("data", {}) or {}).get("pairs") or []
        )
        n_pairs = max(1, int(n_pairs))
        max_open = int(
            trading_cfg.get("max_open_positions")
            or risk_cfg.get("max_open_positions")
            or n_pairs
        )
        deploy_slots = max(1, min(max_open, n_pairs))
        fee_reserve = 0.04
        per_slot_cap = (1.0 - fee_reserve) / deploy_slots
        risk_pct = float(effective_portfolio.get("risk_pct", 0.01))
        max_pos_pct = float(effective_portfolio.get("max_position_per_trade_pct", 28.0)) / 100.0
        if risk_pct > per_slot_cap + 1e-6:
            logger.warning(
                "risk_pct %.2f%% exceeds per-slot cap %.2f%% "
                "(pairs=%s, deploy_slots=%s); capping",
                risk_pct * 100,
                per_slot_cap * 100,
                n_pairs,
                deploy_slots,
            )
            sizing_cfg["risk_pct"] = round(per_slot_cap, 4)
            portfolio_cfg["risk_pct"] = round(per_slot_cap, 4)
            trading_cfg["risk_pct"] = round(per_slot_cap, 4)
            risk_pct = per_slot_cap
        if max_pos_pct > per_slot_cap + 1e-6:
            logger.warning(
                "max_position_per_trade_pct %.1f exceeds per-slot cap %.1f%%; capping",
                max_pos_pct * 100,
                per_slot_cap * 100,
            )
            sizing_cfg["max_position_per_trade_pct"] = round(per_slot_cap * 100, 2)
            portfolio_cfg["max_position_per_trade_pct"] = round(per_slot_cap * 100, 2)
            trading_cfg["max_position_per_trade_pct"] = round(per_slot_cap * 100, 2)
            max_pos_pct = per_slot_cap
        if risk_pct > max_pos_pct + 1e-6:
            sizing_cfg["risk_pct"] = max_pos_pct
            portfolio_cfg["risk_pct"] = max_pos_pct
            trading_cfg["risk_pct"] = max_pos_pct
        
        self.ws = create_exchange_websocket(
            self.config,
            symbols=self._pair_registry.active_symbols(),
            on_tick=self.on_ws_tick,
            on_status=self._on_ws_status,
            on_market=self.on_ws_market,
        )
        
        # Telegram Setup
        self.tg_enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true"
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        
        if notification_service is not None:
            self._tg_service = notification_service
            # An explicitly injected service is meant to be used; enable delegation
            # so send_telegram_alert routes to it regardless of the TELEGRAM_ENABLED env.
            self.tg_enabled = True
        else:
            from xauby.notifications.telegram import TelegramNotificationService
            self._tg_service = TelegramNotificationService(
                token=self.tg_token,
                chat_id=self.tg_chat_id,
                enabled=self.tg_enabled,
            )

        self._notif_settings = NotificationSettings.from_config(self.config)
        self.trading_mode = str(self.config.get("trading", {}).get("mode", "full_auto")).lower()
        self._report_scheduler = ReportScheduler(self.config)
        self._semi_auto_lock = threading.Lock()
        self._tg_bot = None
        if (
            self._notif_settings.telegram_command_polling_enabled
            and self.tg_enabled
            and self.tg_token
            and self.tg_chat_id
        ):
            from xauby.notifications.telegram_bot import TelegramCommandPoller
            self._tg_bot = TelegramCommandPoller(
                self.tg_token,
                self.tg_chat_id,
                self,
                enabled=True,
            )
            self._tg_bot.start()
        
        self.last_status_time = 0
        self.last_log_message = "Engine initialized."
        self.engine_started_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._engine_loop_started = False
        self._tick_counter = 0

        # Observability layer
        self.run_id = get_run_id()
        self._event_store = EventStore(db=self.db)
        self._emitter = EventEmitter(
            store=self._event_store,
            symbol=self.focus_symbol,
            mode="paper" if self.simulate_only else "live",
            run_id=self.run_id,
            config=self.config,
        )
        obs_cfg = self.config.get("observability", {}) or {}
        state_indent = obs_cfg.get("state_json_indent", 2)
        self._state_exporter = StateExporter(indent=state_indent)
        self.current_regime = None
        self._routing_recorder = None
        try:
            from xauby.runtime.architecture_config import event_bus_enabled

            if event_bus_enabled(self.config):
                from xauby.observability.routing_subscriber import RoutingEventRecorder

                self._routing_recorder = RoutingEventRecorder().register()
        except Exception:
            self._routing_recorder = None
        self._flush_live_gate_blocks()
        self._sync_focus_aliases()

    def _validate_derivatives_runtime(self) -> None:
        from xauby.runtime.derivatives_config import derivatives_settings

        settings = derivatives_settings(self.config)
        if settings["market_type"] != "swap":
            return
        short_specs = [
            spec for spec in self._pair_registry.active()
            if "short" in spec.allowed_sides
        ]
        for spec in short_specs:
            if not (1.0 <= float(spec.leverage) <= settings["max_leverage"]):
                raise ConfigError(
                    f"{spec.symbol}: leverage must be between 1 and {settings['max_leverage']}"
                )
        if any(spec.execution_mode == "live" and spec.short_live_enabled for spec in short_specs):
            caps = getattr(self.client, "capabilities", {}) or {}
            missing = [name for name in ("swap", "positions", "reduce_only") if not caps.get(name)]
            if missing:
                raise ConfigError(
                    "Live SHORT requested but exchange adapter lacks: " + ", ".join(missing)
                )

    def _symbol_fee_pct(self, symbol: str) -> float:
        """Resolve the taker fee fraction for ``symbol``.

        Per-symbol ``sim_fee_pct`` (whitelist) wins; otherwise the exchange-wide
        fee from config. Shared by the sim broker and the legacy simulate-only
        order paths so fee math matches the backtest resolver.
        """
        sym = str(symbol or "").upper().replace("_", "")
        live_fee = self._exchange_fee_pct_by_symbol.get(sym)
        if live_fee is not None:
            return live_fee
        spec = self._pair_registry.get(sym) if self._pair_registry else None
        if spec and spec.sim_fee_pct is not None:
            return float(spec.sim_fee_pct)
        return resolve_fee_pct(self.config)

    def _load_live_exchange_fees(self) -> None:
        """Cache account-tier fees exposed by the active exchange adapter.

        Live execution uses the authenticated rate. Config remains the
        deterministic fallback for adapters without this capability and for
        offline simulation/backtests.
        """
        if self.simulate_only:
            return
        getter = getattr(self.client, "get_trading_fee", None)
        if not callable(getter):
            logger.warning("Exchange adapter does not expose trading fees; using configured fallback")
            return
        for spec in self._pair_registry.active():
            try:
                rates = getter(spec.symbol) or {}
                taker = float(rates.get("taker") or 0.0)
                maker = float(rates.get("maker") or 0.0)
                if taker < 0:
                    raise ValueError(f"invalid taker fee {taker}")
                self._exchange_fee_pct_by_symbol[spec.symbol] = taker
                self._exchange_maker_fee_pct_by_symbol[spec.symbol] = maker
                logger.info(
                    "[%s] Account trading fees loaded: maker=%.6f taker=%.6f",
                    spec.symbol, maker, taker,
                )
            except Exception as exc:
                logger.warning(
                    "[%s] Unable to load account trading fee; using configured fallback: %s",
                    spec.symbol, exc,
                )

    def _quote_asset(self) -> str:
        """The exchange quote asset (e.g. ``USDT``, ``THB``) for balance reads.

        Resolves from the pair registry (whitelist) first, then config. Used to
        index real-exchange balance dicts so cash reads work on non-USDT quotes.
        """
        registry = getattr(self, "_pair_registry", None)
        quote = getattr(registry, "quote_asset", None) if registry else None
        return str(quote or resolve_quote_asset(self.config)).upper()

    @property
    def symbol(self) -> str:
        return self._active_tick_symbol or self.focus_symbol

    @symbol.setter
    def symbol(self, value: str) -> None:
        self.focus_symbol = str(value).upper().replace("_", "")

    @property
    def timeframe_primary(self) -> str:
        return self._sc().primary_timeframe

    @property
    def timeframe_regime(self) -> Optional[str]:
        return self._sc().timeframe_regime

    @property
    def use_d1_regime_filter(self) -> bool:
        return self._sc().use_d1_regime_filter

    @property
    def latest_tick(self) -> Dict[str, Any]:
        return self._sc().latest_tick

    @latest_tick.setter
    def latest_tick(self, value: Dict[str, Any]) -> None:
        self._sc().set_tick(value)

    def _sym(self) -> str:
        return self._active_tick_symbol or self.focus_symbol

    def _sc(self, symbol: Optional[str] = None) -> SymbolContext:
        sym = (symbol or self._sym()).upper().replace("_", "")
        if sym not in self.contexts:
            spec = self._pair_registry.get(sym)
            if spec:
                self.contexts[sym] = SymbolContext.from_spec(spec)
            else:
                raise KeyError(f"Unknown symbol context: {sym}")
        return self.contexts[sym]

    def _load_strategy_for_symbol(self, symbol: str):
        sym = (symbol or self._sym()).upper().replace("_", "")
        if sym in self._strategies_by_symbol:
            return self._strategies_by_symbol[sym]
        name = self._strategy_name_for_symbol(sym)
        try:
            eff = resolve_trading_config(self.config, name, symbol=sym, for_live=True)
            strategy = load_strategy(name, eff.strategy, strict=self._strategy_validate_strict)
            tags = {str(tag).lower() for tag in getattr(strategy, "tags", [])}
            spec = self._pair_registry.get(sym)
            is_live = bool(spec and spec.execution_mode == "live")
            short_enabled = bool(spec and (
                spec.execution_mode == "sim" or spec.short_live_enabled
            ))
            # Research plugins (incl. the research *_short mirrors) are sim/backtest
            # only — never live, even when the pair has short_live_enabled. Without
            # this, enabling live shorts would let a research SHORT plugin trade real
            # money. (cdc_action_zone is NOT research-tagged, so it is unaffected.)
            if "research" in tags and is_live:
                raise ConfigError(
                    f"Research-only strategy {name!r} cannot run live (sim/backtest only)"
                )
            # A live pair must name a plugin somebody has explicitly signed off
            # as production. Until now the only barrier was a `research` string
            # sitting in `tags` beside `btc` and `4h`, so 16 of 17 plugins —
            # every one still at version 0.1.0, none with a certificate — could
            # be promoted to live by editing a whitelist, and nothing would say
            # a word. Undeclared is NOT treated as production: an unanswered
            # question must not read as a pass.
            maturity = strategy_maturity(strategy)
            if is_live and maturity != "production":
                raise ConfigError(
                    f"Strategy {name!r} has maturity "
                    f"{maturity or 'undeclared'!r} and cannot run on a live "
                    "pair. Declare maturity = \"production\" on the plugin once "
                    "it has a certificate, or run this pair in sim."
                )
            if "research" in tags and "short" not in tags:
                raise ConfigError(
                    f"Research-only strategy {name!r} cannot run in the production engine"
                )
            if "short" in tags and not short_enabled:
                raise ConfigError(
                    f"Short strategy {name!r} requires sim mode or short_live_enabled=true"
                )
        except (KeyError, ConfigError) as e:
            logger.error("Failed to load strategy %r for %s: %s", name, sym, e)
            raise
        runner = StrategyRunner(
            strategy,
            timeout=self._strategy_sandbox_timeout,
            check_imports=True,
            strict=self._strategy_sandbox_strict,
        )
        self._strategies_by_symbol[sym] = strategy
        self._runners_by_symbol[sym] = runner
        logger.info(
            "Loaded strategy plugin for %s: %s v%s",
            sym,
            strategy.name,
            getattr(strategy, "version", "?"),
        )
        self._warn_if_stop_loss_unsupported(sym, name, eff, is_live)
        return strategy

    def _warn_if_stop_loss_unsupported(
        self, sym: str, name: str, eff: Any, is_live: bool
    ) -> None:
        """Surface degraded stop-loss protection BEFORE trading starts.

        A strategy that relies on an exchange-side stop (``disable_stop_loss`` is
        False) running live on a venue that cannot place ``STOP_LOSS_LIMIT`` will
        fall back to local monitoring — which only fires while the engine is up.
        Make that explicit and loud at startup instead of after the first SL
        placement fails post-entry.
        """
        uses_exchange_sl = not bool(eff.strategy.get("disable_stop_loss", False))
        if not (is_live and uses_exchange_sl):
            return
        probe = getattr(self.client, "supports_exchange_stop_loss", None)
        if not callable(probe):
            return  # unknown adapter: assume supported (feature-detection default)
        try:
            supported = bool(probe())
        except Exception:
            return  # never block startup on a capability probe error
        if supported:
            return
        provider = str((self.config.get("exchange") or {}).get("provider") or "exchange")
        msg = (
            f"{sym}/{name}: exchange-side stop-loss NOT supported by '{provider}'. "
            f"Positions will rely on LOCAL stop-loss monitoring only, which fires "
            f"ONLY while the engine is running. Enable "
            f"exchange.capabilities.stop_loss_limit (after verifying the venue) or "
            f"use a venue with STOP_LOSS_LIMIT support."
        )
        logger.warning(msg)
        alert = getattr(self, "send_telegram_alert", None)
        if callable(alert):
            try:
                alert(f"⚠️ *Degraded Stop-Loss*\n{msg}")
            except Exception:
                pass

    def _start_loaded_strategies(self) -> None:
        for sym, strategy in list(self._strategies_by_symbol.items()):
            if sym in self._started_strategy_symbols:
                continue
            try:
                strategy.on_start()
                self._started_strategy_symbols.add(sym)
            except Exception as e:
                logger.error("strategy.on_start failed for %s/%s: %s", sym, strategy.name, e)

    def _strategy_name_for_symbol(self, symbol: Optional[str] = None) -> str:
        sym = (symbol or self._sym()).upper().replace("_", "")
        if sym in self._strategy_names_by_symbol:
            return self._strategy_names_by_symbol[sym]
        return strategy_name_for_symbol(self.config, sym)

    def _get_strategy_for_symbol(self, symbol: Optional[str] = None):
        sym = (symbol or self._sym()).upper().replace("_", "")
        return self._load_strategy_for_symbol(sym)

    def _get_runner_for_symbol(self, symbol: Optional[str] = None) -> StrategyRunner:
        sym = (symbol or self._sym()).upper().replace("_", "")
        self._get_strategy_for_symbol(sym)
        return self._runners_by_symbol[sym]

    def _init_pair_runtime(self) -> None:
        env_sym = os.environ.get("DEFAULT_SYMBOL", "").upper().replace("_", "")
        self._pair_registry.load(self.client)
        if not self._pair_registry.active():
            raise ConfigError("No active trading pairs after pair registry load")
        if env_sym and whitelist_strict(self.config):
            active_syms = {s.symbol for s in self._pair_registry.active()}
            if env_sym not in active_syms:
                raise ConfigError(
                    f"Requested symbol {env_sym} (DEFAULT_SYMBOL/--pair) is not an "
                    f"enabled whitelist pair {sorted(active_syms)} (whitelist_strict)"
                )
        self._strategy_names_by_symbol = {
            spec.symbol: spec.strategy_name or strategy_name_for_symbol(
                self.config,
                spec.symbol,
                project_root=self._project_root,
            )
            for spec in self._pair_registry.all_specs()
        }
        active_symbols = set(self._strategy_names_by_symbol.keys())
        for sym in list(self._strategies_by_symbol.keys()):
            strategy = self._strategies_by_symbol.get(sym)
            expected_name = self._strategy_names_by_symbol.get(sym)
            loaded_name = getattr(strategy, "name", "")
            if sym in active_symbols and loaded_name == expected_name:
                continue
            try:
                if sym in self._started_strategy_symbols and strategy is not None:
                    strategy.on_stop()
            except Exception as e:
                logger.error("strategy.on_stop failed while reloading %s: %s", sym, e)
            self._strategies_by_symbol.pop(sym, None)
            self._runners_by_symbol.pop(sym, None)
            self._started_strategy_symbols.discard(sym)
        for spec in self._pair_registry.all_specs():
            forced_sim = RegimeRouter.live_gate_blocked(spec)
            spec = RegimeRouter.apply_live_confirmation(spec)
            self._pair_registry.update_spec(spec)
            banner = RegimeRouter.live_banner_message(spec)
            if banner:
                logger.warning(banner)
            gate_warn = RegimeRouter.live_gate_warning(spec)
            if gate_warn:
                logger.warning(gate_warn)
            if forced_sim:
                self._log_live_gate_block(spec.symbol, gate_warn or "RegimeRouter live override blocked. Falling back to SIM.")
            strategy = self._load_strategy_for_symbol(spec.symbol)
            self._pair_registry.mark_symbol_strategy_warning(
                spec.symbol,
                list(strategy.get_meta().required_timeframes),
            )
        existing = self.contexts
        self.contexts = {}
        for spec in self._pair_registry.all_specs():
            if spec.symbol in existing:
                ctx = existing[spec.symbol]
                ctx.strategy_name = spec.strategy_name
                ctx.primary_timeframe = spec.primary_timeframe
                ctx.confirm_timeframe = spec.regime_timeframe
                ctx.use_d1_regime_filter = bool(spec.use_d1_regime_filter)
                ctx.degraded = spec.degraded
                ctx.set_degrade_reason(spec.degrade_reason)
                ctx.regime_router_warning = RegimeRouter.live_gate_warning(spec) or ctx.regime_router_warning
                self.contexts[spec.symbol] = ctx
            else:
                ctx = SymbolContext.from_spec(spec)
                ctx.regime_router_warning = RegimeRouter.live_gate_warning(spec)
                self.contexts[spec.symbol] = ctx
        self._start_loaded_strategies()
        self.focus_symbol = self._pair_registry.focus_symbol(env_sym or None)
        if self.focus_symbol not in self.contexts and self._pair_registry.active():
            self.focus_symbol = self._pair_registry.active()[0].symbol

    def _sync_focus_aliases(self) -> None:
        try:
            sc = self._sc(self.focus_symbol)
        except KeyError:
            return
        self.indicators_cache = sc.indicators_cache
        self.last_signal_meta = sc.last_signal_meta
        self.current_regime = sc.current_regime

    def _refresh_websocket_symbols(self) -> None:
        syms = self._pair_registry.active_symbols()
        if not syms:
            syms = [self.focus_symbol]
        prev_syms = getattr(self, "_ws_symbols", set())
        if set(syms) == prev_syms:
            return
        try:
            self.ws.stop()
        except Exception:
            pass
        from xauby.api import create_exchange_websocket

        self.ws = create_exchange_websocket(
            self.config,
            symbols=syms,
            on_tick=self.on_ws_tick,
            on_status=self._on_ws_status,
            on_market=self.on_ws_market,
        )
        if self.ws is not None:
            self.ws.start()
        self._ws_symbols = set(syms)
        try:
            if self.ws is not None:
                self.ws.trim_price_cache(list(syms))
        except Exception:
            pass

    def _execution_mode(self, symbol: Optional[str] = None) -> str:
        from xauby.runtime.architecture_config import per_symbol_execution_mode

        sym = (symbol or self._sym()).upper().replace("_", "")
        if per_symbol_execution_mode(self.config):
            spec = self._pair_registry.get(sym)
            if spec and spec.execution_mode in ("sim", "live"):
                if spec.execution_mode == "live" and not self.live_trading_allowed:
                    return "sim"
                return spec.execution_mode
        return "sim" if self.simulate_only else "live"

    def _use_sim_broker(self, symbol: Optional[str] = None) -> bool:
        from xauby.runtime.architecture_config import sim_broker_enabled

        if sim_broker_enabled(self.config):
            return self._execution_mode(symbol) == "sim"
        return bool(self.simulate_only)

    def _broker_for_symbol(self, symbol: Optional[str] = None):
        if self._execution_mode(symbol) == "sim":
            return self._sim_broker
        return self._live_broker

    def _log_live_gate_block(self, symbol: str, message: str) -> None:
        """Record a RegimeRouter live-gate forced-sim fallback to SQLite events.

        Buffered during startup (before the event emitter exists) and flushed
        once the emitter is ready; emitted directly on hot-reload.
        """
        from xauby.observability.events import EventType

        emitter = getattr(self, "_emitter", None)
        if emitter is None:
            self._pending_live_gate_blocks.append((symbol, message))
            return
        try:
            self._emit_event(
                EventType.REGIME_ROUTER_LIVE_BLOCKED,
                symbol=symbol,
                message=message,
            )
        except Exception:
            self._pending_live_gate_blocks.append((symbol, message))

    def _flush_live_gate_blocks(self) -> None:
        pending = getattr(self, "_pending_live_gate_blocks", [])
        if not pending:
            return
        self._pending_live_gate_blocks = []
        for sym, msg in pending:
            self._log_live_gate_block(sym, msg)

    def _sim_fill_price(self, symbol: Optional[str], fallback: float) -> float:
        """Candle-close fill price for sim orders (falls back to live tick).

        Spec: SimBroker fills at the current candle close, not the WS/REST
        tick. Controlled by ``architecture.sim_fill_on_close`` (default true).
        """
        from xauby.runtime.architecture_config import sim_fill_on_close

        if not self._use_sim_broker(symbol) or not sim_fill_on_close(self.config):
            return fallback
        sym = (symbol or self._sym()).upper().replace("_", "")
        spec = self._pair_registry.get(sym)
        tf = (spec.primary_timeframe if spec else None) or self.config.get(
            "trading", {}
        ).get("timeframe", "4h")
        try:
            candles = self.db.get_candles(sym, tf, limit=1)
        except Exception:
            candles = []
        if candles:
            last = candles[-1]
            close = getattr(last, "close", None)
            if close is None and isinstance(last, dict):
                close = last.get("close")
            try:
                close_f = float(close)
                if close_f > 0:
                    return close_f
            except (TypeError, ValueError):
                pass
        return fallback

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
            logger.info(f"Loaded config from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self.config = {}

    def _reload_hot_pair_config(self) -> bool:
        """Refresh pair-owned YAML after the whitelist hot-reload trigger.

        The control plane writes YAML first and the atomic whitelist last. This
        keeps strategy sizing, pair allocations and max-open-position limits in
        the same hot-reload transaction without restarting the live engine.
        Exchange changes are intentionally rejected because the existing API
        client and credentials cannot be replaced safely in place.
        """
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                candidate = yaml.safe_load(handle) or {}
        except Exception as exc:
            logger.warning("Pair config hot reload rejected: %s", exc)
            return False

        def exchange_signature(config: Dict[str, Any]) -> tuple[str, str, str]:
            exchange = config.get("exchange") or {}
            derivatives = config.get("derivatives") or {}
            return (
                str(exchange.get("ccxt_id") or exchange.get("id") or exchange.get("name") or "").lower(),
                str(exchange.get("market_type") or derivatives.get("market_type") or "").lower(),
                str(exchange.get("quote_asset") or (config.get("portfolio") or {}).get("quote_asset") or "").upper(),
            )

        if exchange_signature(candidate) != exchange_signature(self.config):
            logger.warning("Pair config hot reload rejected: exchange target changed")
            return False
        self.config = candidate
        self._pair_registry.config = candidate
        logger.info("Reloaded pair risk and portfolio config from %s", self.config_path)
        return True

    def _get_strategy_config(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = symbol or self._sym()
        name = self._strategy_name_for_symbol(sym)
        return dict(
            resolve_trading_config(
                self.config,
                name,
                symbol=sym or "",
                for_live=True,
            ).strategy
        )

    def _acquire_engine_lock(self):
        try:
            import fcntl
            from xauby.runtime.paths import ensure_runtime_dir, runtime_path
            ensure_runtime_dir()
            lock_name = self.config.get("lock_file")
            if not lock_name:
                config_name = os.path.splitext(os.path.basename(self.config_path))[0]
                lock_name = runtime_path(f".{config_name}.lock")
            # "a+" instead of "w": opening must not truncate — a losing
            # contender would wipe the holder's PID before flock even fails.
            self._lock_fp = open(lock_name, "a+")
            try:
                fcntl.flock(self._lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._lock_fp.close()
                self._lock_fp = None
                # Read the PID of the already-running instance to give a clear message.
                existing_pid = ""
                try:
                    with open(lock_name, "r") as _lf:
                        existing_pid = _lf.read().strip()
                except Exception:
                    pass
                pid_hint = f" (pid {existing_pid})" if existing_pid else ""
                raise RuntimeError(
                    f"{PRODUCT_NAME} engine is already running{pid_hint} — "
                    f"lock file held: {lock_name}\n"
                    f"If the previous process crashed, remove the lock file and retry:\n"
                    f"  rm {lock_name}"
                ) from None
            self._lock_fp.seek(0)
            self._lock_fp.truncate()
            self._lock_fp.write(str(os.getpid()))
            self._lock_fp.flush()
        except ImportError:
            logger.debug("fcntl unavailable; skipping engine lock")

    def _release_engine_lock(self):
        if not self._lock_fp:
            return
        try:
            import fcntl
            fcntl.flock(self._lock_fp, fcntl.LOCK_UN)
            self._lock_fp.close()
        except Exception:
            pass
        self._lock_fp = None

    @staticmethod
    def _account_fingerprint(api_key: Optional[str]) -> str:
        """Stable short id for the exchange account, derived from the API key.

        Hashed so the secret is never written to the lock file. Empty when no
        key is configured (sim/paper, or misconfigured live — caller decides)."""
        key = (api_key or "").strip()
        if not key:
            return ""
        import hashlib
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def _acquire_account_lock(self):
        """Refuse to start a second LIVE engine on the same exchange account.

        Two live instances sharing one account each size positions off the full
        account equity (``get_equity`` reads the whole balance), so combined
        exposure silently doubles. The lock lives in a shared dir keyed by the
        account fingerprint — separate from the per-instance engine lock — so it
        is visible across instances. Design: one instance per subaccount/API key
        (separate keys → different fingerprints → both allowed)."""
        # Sim/paper never touch the real account, so they never contend.
        if self.simulate_only or not self.live_trading_allowed:
            return
        if not self._account_fp:
            logger.warning(
                "No API key to fingerprint the account; skipping cross-instance "
                "lock. Ensure each live instance uses its own subaccount/API key."
            )
            return
        try:
            import fcntl
        except ImportError:
            logger.warning(
                "fcntl unavailable (e.g. Windows); cross-instance account lock SKIPPED "
                "— ensure each live instance uses its own subaccount/API key."
            )
            return
        from xauby.runtime.paths import account_lock_path

        path = account_lock_path(self._account_fp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # "a+" instead of "w": opening must not truncate — a losing contender
        # would wipe the holder's "pid ... cfg ..." info before flock fails.
        fp = open(path, "a+")
        try:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fp.close()
            existing = ""
            try:
                with open(path, "r") as lf:
                    existing = lf.read().strip()
            except Exception:
                pass
            hint = f" (held by {existing})" if existing else ""
            raise RuntimeError(
                f"Another LIVE {PRODUCT_NAME} instance is already trading this exchange "
                f"account{hint}. Two live instances on one account double-count "
                f"risk — give each its own subaccount/API key, or stop the other "
                f"instance. Account lock: {path}"
            ) from None
        fp.seek(0)
        fp.truncate()
        fp.write(f"pid {os.getpid()} cfg {self.config_path}")
        fp.flush()
        self._account_lock_fp = fp

    def _release_account_lock(self):
        if not getattr(self, "_account_lock_fp", None):
            return
        try:
            import fcntl
            fcntl.flock(self._account_lock_fp, fcntl.LOCK_UN)
            self._account_lock_fp.close()
        except Exception:
            pass
        self._account_lock_fp = None

    @staticmethod
    def _timeframe_ms(timeframe: str) -> int:
        return {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }.get(timeframe, 14_400_000)

    def _get_tick_snapshot(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self._sc(symbol).get_tick_snapshot()

    def _get_base_asset(self, symbol: Optional[str] = None) -> str:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        fallback = sym[:-4].upper() if sym.endswith("USDT") else sym.replace("USDT", "").replace("THB", "").upper()
        try:
            ex_info = self.client.get_exchange_info(sym)
            symbols_entry = ex_info.get("symbols", [])
            entry = find_symbol_entry(symbols_entry, sym) if symbols_entry else None
            base = entry.get("baseAsset") if entry else None
            if base:
                return base.upper()
        except Exception as e:
            logger.debug(f"Failed to get baseAsset from exchangeInfo: {e}")
        
        return fallback

    def _load_price_flush_interval(self) -> float:
        try:
            ui = (self.config.get("cli_ui") or {}) if getattr(self, "config", None) else {}
            return max(
                0.5,
                float(ui.get("price_flush_interval_seconds", ui.get("refresh_interval_seconds", 1.0))),
            )
        except Exception:
            return 1.0

    def _apply_tick_to_snapshots(self, sym: str, tick: Dict[str, Any]) -> None:
        last = float(tick.get("last") or 0.0)
        if last <= 0:
            return
        snap = self._symbol_snapshots.get(sym)
        if not snap:
            return
        pct = float(tick.get("percent_change_24h") or 0.0)
        snap["current_price"] = last
        snap["bid"] = float(tick.get("bid") or 0.0)
        snap["ask"] = float(tick.get("ask") or 0.0)
        snap["percent_change_24h"] = pct
        snap["price_change_24h_pct"] = pct

    def _maybe_flush_live_prices(self) -> None:
        """Push websocket prices to bot state JSON between engine ticks."""
        if not self._symbol_snapshots:
            return
        now = time.monotonic()
        if now - self._last_price_flush_mono < self._price_flush_interval_s:
            return
        self._last_price_flush_mono = now
        try:
            self._write_multi_state_json()
        except Exception as e:
            logger.debug(f"Live price state flush failed: {e}")

    def on_ws_tick(self, tick: Dict[str, Any]):
        sym = str(tick.get("symbol", "")).upper().replace("_", "")
        if sym in self.contexts:
            self.contexts[sym].set_tick(tick)
            self._apply_tick_to_snapshots(sym, tick)
            self._maybe_flush_live_prices()

    def on_ws_market(self, event: Dict[str, Any]) -> None:
        sym = str(event.get("symbol", "")).upper().replace("_", "")
        if sym in self.contexts:
            self.contexts[sym].set_market_data(event)
