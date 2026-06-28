"""Active trading pair list from YAML + coin_whitelist.json with hot reload."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from xauby.runtime.architecture_config import sync_yaml_pairs_from_whitelist, whitelist_strict
from xauby.runtime.config_error import ConfigError
from xauby.runtime.exchange_config import resolve_quote_asset
from xauby.runtime.symbol_utils import normalize_market_symbol, normalize_symbol, normalize_tf

# Backward-compatible aliases used across the codebase.
_normalize_symbol = normalize_symbol
_normalize_tf = normalize_tf

logger = logging.getLogger("xauby.runtime.pair_registry")

DEFAULT_WHITELIST_PATH = "coin_whitelist.json"


def _parse_execution_mode(entry: Dict[str, Any]) -> Optional[str]:
    raw = entry.get("mode")
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in ("sim", "live"):
        return val
    return None


def _parse_bool_field(entry: Dict[str, Any], key: str, default: bool = False) -> bool:
    val = entry.get(key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _parse_sim_fee(entry: Dict[str, Any]) -> Optional[float]:
    raw = entry.get("sim_fee_pct")
    if raw is None:
        return None
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return None


def _pair_fields_from_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    raw_sides = entry.get("allowed_sides", ["long"])
    if isinstance(raw_sides, str):
        raw_sides = [raw_sides]
    allowed_sides = tuple(
        side for side in (str(v).strip().lower() for v in raw_sides or [])
        if side in ("long", "short")
    ) or ("long",)
    try:
        leverage = float(entry.get("leverage", 1.0) or 1.0)
    except (TypeError, ValueError):
        leverage = 1.0
    return {
        "execution_mode": _parse_execution_mode(entry),
        "regime_router_enabled": _parse_bool_field(entry, "regime_router_enabled"),
        "regime_router_live_confirmed": _parse_bool_field(entry, "regime_router_live_confirmed"),
        "sim_fee_pct": _parse_sim_fee(entry),
        "allowed_sides": allowed_sides,
        "leverage": leverage,
        "short_live_enabled": _parse_bool_field(entry, "short_live_enabled"),
    }


@dataclass
class PairSpec:
    symbol: str
    enabled: bool = True
    strategy_name: str = ""
    primary_timeframe: str = "4h"
    confirm_timeframe: str = "1d"
    use_d1_regime_filter: Optional[bool] = None
    degraded: bool = False
    degrade_reason: str = ""
    execution_mode: Optional[str] = None
    regime_router_enabled: bool = False
    regime_router_live_confirmed: bool = False
    sim_fee_pct: Optional[float] = None
    allowed_sides: tuple[str, ...] = ("long",)
    leverage: float = 1.0
    short_live_enabled: bool = False

    @property
    def regime_timeframe(self) -> Optional[str]:
        use_d1 = self.use_d1_regime_filter
        if use_d1 is False:
            return None
        cf = (self.confirm_timeframe or "").strip()
        return _normalize_tf(cf) if cf else None


class PairRegistry:
    """Merge data.pairs with whitelist assets; optional exchange validation."""

    def __init__(
        self,
        config: Dict[str, Any],
        config_path: str = "bot_config.yaml",
        project_root: str = ".",
        whitelist_path: Optional[str] = None,
    ):
        self.config = config
        self.config_path = config_path
        self.project_root = project_root
        data = config.get("data", {}) or {}
        hybrid = data.get("hybrid_dynamic_coin_config", {}) or {}
        self.whitelist_path = whitelist_path or os.path.join(
            project_root, hybrid.get("whitelist_json_path", DEFAULT_WHITELIST_PATH)
        )
        self.quote_asset = resolve_quote_asset(config)
        self._specs: Dict[str, PairSpec] = {}
        self._last_mtime = 0.0
        self._last_reload = 0.0
        self._reload_interval = float(hybrid.get("reload_interval_seconds", 30))
        self._hot_reload = bool(hybrid.get("hot_reload_enabled", True))
        self._require_supported = bool(hybrid.get("require_supported_market", True))
        self._strict = whitelist_strict(config)

    def _defaults(self) -> tuple[str, str, bool]:
        trading = self.config.get("trading", {}) or {}
        primary = _normalize_tf(trading.get("timeframe", "4h"))
        active_mode = self.config.get("strategy_mode", {}).get("active", "trend_only")
        mode_cfg = self.config.get("strategy_mode", {}).get(active_mode, {}) or {}
        confirm = _normalize_tf(mode_cfg.get("confirm_timeframe", "1d"))
        strat = self.config.get("strategies", {}).get(
            self.config.get("strategy", {}).get("active", "cdc_action_zone")
            or "cdc_action_zone",
            {},
        )
        use_d1 = bool(strat.get("use_d1_regime_filter", False))
        return primary, confirm, use_d1

    def _whitelist_abs_path(self) -> str:
        path = self.whitelist_path
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        return path

    def _load_whitelist_raw(self) -> Dict[str, Any]:
        path = self._whitelist_abs_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            logger.warning("Failed to read whitelist %s: %s", path, e)
            return {}

    def _yaml_pairs(self) -> List[str]:
        data = self.config.get("data", {}) or {}
        pairs = data.get("pairs") or []
        out: List[str] = []
        for p in pairs:
            sym = _normalize_symbol(str(p), self.quote_asset)
            if sym:
                out.append(sym)
        return out

    def _resolve_strategy_name(self, sym: str, entry: Optional[Dict[str, Any]] = None) -> str:
        from xauby.runtime.trading_config import strategy_name_for_symbol
        from xauby.runtime.whitelist_validator import strategy_name_from_whitelist_entry

        if entry:
            wl_name = strategy_name_from_whitelist_entry(entry)
            if wl_name:
                if self._strict:
                    return wl_name
                return wl_name
        return strategy_name_for_symbol(
            self.config,
            sym,
            project_root=self.project_root,
            whitelist_path=self.whitelist_path,
            strict=self._strict,
        )

    def _exchange_supported(self, client: Any) -> Optional[Set[str]]:
        if not client or not self._require_supported or not hasattr(client, "get_exchange_info"):
            return None
        try:
            info = client.get_exchange_info()
            symbols_entry = info.get("symbols", [])
            supported: Set[str] = set()
            for entry in symbols_entry:
                if isinstance(entry, dict):
                    supported.add(str(entry.get("symbol", "")).upper())
            return supported
        except Exception as e:
            if self._strict:
                raise ConfigError(f"exchangeInfo validation failed: {e}") from e
            logger.warning("exchangeInfo validation skipped: %s", e)
            return None

    def _apply_exchange_filter(self, merged: Dict[str, PairSpec], supported: Optional[Set[str]]) -> None:
        if supported is None:
            return
        if self._strict:
            active_syms = [s.symbol for s in merged.values() if s.enabled]
            from xauby.runtime.whitelist_validator import validate_exchange_support

            validate_exchange_support(
                active_syms,
                supported,
                require_supported=self._require_supported,
            )
            return
        for sym in list(merged.keys()):
            if sym not in supported:
                logger.warning("Dropping unsupported pair %s", sym)
                del merged[sym]

    def _load_strict(self, wl: Dict[str, Any], client: Any = None) -> Dict[str, PairSpec]:
        from xauby.runtime.whitelist_validator import validate_whitelist

        primary_def, confirm_def, use_d1_def = self._defaults()
        supported = self._exchange_supported(client)
        validate_whitelist(
            wl,
            whitelist_path=self._whitelist_abs_path(),
            supported=supported,
            require_supported_market=self._require_supported,
            strict=True,
        )
        merged: Dict[str, PairSpec] = {}
        for entry in wl.get("assets") or []:
            if not isinstance(entry, dict):
                continue
            base = str(entry.get("symbol", "")).strip()
            sym = normalize_market_symbol(base, self.quote_asset)
            if not sym:
                continue
            enabled = bool(entry.get("enabled", True))
            ptf = _normalize_tf(entry.get("primary_timeframe") or primary_def)
            ctf = _normalize_tf(entry.get("confirm_timeframe") or confirm_def)
            u1 = entry.get("use_d1_regime_filter")
            if u1 is None:
                u1 = use_d1_def
            strat = self._resolve_strategy_name(sym, entry)
            extra = _pair_fields_from_entry(entry)
            merged[sym] = PairSpec(
                symbol=sym,
                enabled=enabled,
                strategy_name=strat,
                primary_timeframe=ptf,
                confirm_timeframe=ctf,
                use_d1_regime_filter=bool(u1),
                **extra,
            )
        self._apply_exchange_filter(merged, supported)
        if not any(s.enabled for s in merged.values()):
            raise ConfigError("No enabled pairs after strict whitelist validation")
        return merged

    def _load_legacy(self, wl: Dict[str, Any], client: Any = None) -> Dict[str, PairSpec]:
        from xauby.runtime.trading_config import strategy_name_for_symbol
        from xauby.runtime.whitelist_validator import strategy_name_from_whitelist_entry

        primary_def, confirm_def, use_d1_def = self._defaults()
        merged: Dict[str, PairSpec] = {}
        assets = wl.get("assets") or []
        if assets:
            for entry in assets:
                if not isinstance(entry, dict):
                    continue
                base = str(entry.get("symbol", "")).strip()
                sym = normalize_market_symbol(base, self.quote_asset)
                if not sym:
                    continue
                enabled = bool(entry.get("enabled", True))
                ptf = _normalize_tf(entry.get("primary_timeframe") or primary_def)
                ctf = _normalize_tf(entry.get("confirm_timeframe") or confirm_def)
                u1 = entry.get("use_d1_regime_filter")
                if u1 is None:
                    u1 = use_d1_def
                wl_strat = strategy_name_from_whitelist_entry(entry)
                strat = wl_strat or strategy_name_for_symbol(
                    self.config,
                    sym,
                    project_root=self.project_root,
                    whitelist_path=self.whitelist_path,
                )
                merged[sym] = PairSpec(
                    symbol=sym,
                    enabled=enabled,
                    strategy_name=strat,
                    primary_timeframe=ptf,
                    confirm_timeframe=ctf,
                    use_d1_regime_filter=bool(u1),
                    **_pair_fields_from_entry(entry),
                )
        else:
            for sym in self._yaml_pairs():
                merged[sym] = PairSpec(
                    symbol=sym,
                    enabled=True,
                    strategy_name=strategy_name_for_symbol(
                        self.config,
                        sym,
                        project_root=self.project_root,
                        whitelist_path=self.whitelist_path,
                    ),
                    primary_timeframe=primary_def,
                    confirm_timeframe=confirm_def,
                    use_d1_regime_filter=use_d1_def,
                )

        for sym in self._yaml_pairs():
            if sym not in merged:
                merged[sym] = PairSpec(
                    symbol=sym,
                    enabled=True,
                    strategy_name=strategy_name_for_symbol(
                        self.config,
                        sym,
                        project_root=self.project_root,
                        whitelist_path=self.whitelist_path,
                    ),
                    primary_timeframe=primary_def,
                    confirm_timeframe=confirm_def,
                    use_d1_regime_filter=use_d1_def,
                )
            # else: pair already present from the whitelist — keep its enabled
            # flag as-is. (Previously `enabled or True`, which always forced
            # True and could re-enable a disabled whitelist pair.)

        env_sym = os.environ.get("DEFAULT_SYMBOL", "").upper().replace("_", "")
        if env_sym and env_sym not in merged:
            merged[env_sym] = PairSpec(
                symbol=env_sym,
                enabled=True,
                strategy_name=strategy_name_for_symbol(
                    self.config,
                    env_sym,
                    project_root=self.project_root,
                    whitelist_path=self.whitelist_path,
                ),
                primary_timeframe=primary_def,
                confirm_timeframe=confirm_def,
                use_d1_regime_filter=use_d1_def,
            )

        supported = self._exchange_supported(client)
        self._apply_exchange_filter(merged, supported)
        return merged

    def _maybe_sync_yaml_pairs(self) -> None:
        if not sync_yaml_pairs_from_whitelist(self.config):
            return
        from xauby.runtime.pair_config import sync_data_pairs_yaml

        enabled = [s.symbol for s in self.active()]
        if enabled:
            sync_data_pairs_yaml(enabled, self.project_root, self.config_path)

    def load(self, client: Any = None) -> List[PairSpec]:
        """Build specs from whitelist + YAML; filter by exchange if client given."""
        wl = self._load_whitelist_raw()
        self.quote_asset = str(
            wl.get("quote_asset") or resolve_quote_asset(self.config)
        ).upper()

        if self._strict:
            self._specs = self._load_strict(wl, client)
        else:
            self._specs = self._load_legacy(wl, client)

        path = self._whitelist_abs_path()
        if os.path.exists(path):
            self._last_mtime = os.path.getmtime(path)
        self._last_reload = time.time()
        self._maybe_sync_yaml_pairs()
        return self.active()

    def maybe_reload(self, client: Any = None) -> bool:
        """Reload if hot_reload enabled and whitelist file changed."""
        if not self._hot_reload:
            return False
        if time.time() - self._last_reload < self._reload_interval:
            return False
        path = self._whitelist_abs_path()
        if not os.path.exists(path):
            return False
        mtime = os.path.getmtime(path)
        if mtime <= self._last_mtime:
            self._last_reload = time.time()
            return False
        logger.info("Whitelist changed; reloading pair registry")
        try:
            self.load(client)
        except ConfigError as exc:
            self._last_reload = time.time()
            logger.warning(
                "Whitelist reload rejected; keeping previous active registry: %s",
                exc,
            )
            return False
        # A same-symbol edit can still change timeframe, strategy, execution
        # mode, or regime settings. The engine must rebuild pair runtime for
        # those changes too, so report every accepted file reload.
        return True

    def all_specs(self) -> List[PairSpec]:
        return list(self._specs.values())

    def active(self) -> List[PairSpec]:
        return [s for s in self._specs.values() if s.enabled]

    def get(self, symbol: str) -> Optional[PairSpec]:
        return self._specs.get(symbol.upper().replace("_", ""))

    def update_spec(self, spec: PairSpec) -> None:
        """Replace a pair spec in the registry (e.g. live gate forcing sim mode)."""
        sym = spec.symbol.upper().replace("_", "")
        self._specs[sym] = spec

    def is_whitelisted(self, symbol: str) -> bool:
        spec = self.get(symbol)
        return spec is not None and spec.enabled

    def focus_symbol(self, preferred: Optional[str] = None) -> str:
        pref = (preferred or os.environ.get("XAUBY_FOCUS_SYMBOL", "")).upper().replace("_", "")
        active = self.active()
        if pref:
            for s in active:
                if s.symbol == pref:
                    return pref
        if active:
            return active[0].symbol
        if self._strict:
            raise ConfigError("No active trading pairs in whitelist")
        env = os.environ.get("DEFAULT_SYMBOL", "").upper().replace("_", "")
        if env:
            return env
        yaml_pairs = self._yaml_pairs()
        if yaml_pairs:
            return yaml_pairs[0]
        return ""

    def active_symbols(self) -> List[str]:
        return [s.symbol for s in self.active()]

    def mark_strategy_warnings(self, required_timeframes: List[str]) -> None:
        req = {_normalize_tf(t) for t in required_timeframes}
        for spec in self._specs.values():
            if not spec.enabled:
                continue
            self.mark_symbol_strategy_warning(spec.symbol, list(req))

    def mark_symbol_strategy_warning(self, symbol: str, required_timeframes: List[str]) -> None:
        spec = self.get(symbol)
        if not spec or not spec.enabled:
            return
        req = {_normalize_tf(t) for t in required_timeframes}
        missing = req - {_normalize_tf(spec.primary_timeframe), _normalize_tf(spec.confirm_timeframe)}
        if spec.regime_timeframe:
            missing.discard(_normalize_tf(spec.regime_timeframe))
        if missing:
            spec.degraded = True
            spec.degrade_reason = f"missing TF vs strategy: {sorted(missing)}"
        else:
            spec.degraded = False
            spec.degrade_reason = ""
