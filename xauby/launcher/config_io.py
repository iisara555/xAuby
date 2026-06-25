"""Config + .env read/write helpers and per-symbol config accessors."""
import os
import sys
import re
import time
import json
import subprocess
from datetime import datetime

import yaml

from xauby.utils import (
    C_RESET, C_PRIMARY,
    C_MUTED, RESET, BOLD, GREEN, RED, YELLOW,
    WHITE, BB_AMBER, BB_BG_AMBER, BB_CYAN,
    get_terminal_width, center_text,
)
from xauby.ui.menu import (
    print_line, print_menu_row, check_engine_status,
)
from xauby.database.db import resolve_db_path

__all__ = [
    "default_runtime_symbol",
    "_yaml_defines_key",
    "_replace_yaml_key",
    "get_active_strategy_name",
    "list_installed_strategies",
    "_load_bot_yaml",
    "_edit_bot_yaml",
    "_set_yaml_path",
    "_normalize_symbol",
    "_active_symbols_from_config",
    "_quick_config_symbols",
    "_strategy_name_for_symbol_from_cfg",
    "_strategy_cfg_for_symbol",
    "_portfolio_cfg_for_symbol",
    "_select_symbol_interactive",
    "update_yaml_config",
    "_set_pair_strategy_values",
    "update_env_variable",
]


def default_runtime_symbol() -> str:
    try:
        from xauby.ui.state_view import default_symbol_from_whitelist
        return default_symbol_from_whitelist()
    except Exception:
        return os.environ.get("XAUBY_DEFAULT_SYMBOL", "XAUTUSDT")

# ── Tmux helpers for Textual TUI ──
import re

def _yaml_defines_key(line: str, key: str) -> bool:
    """True when line defines a YAML key (not a comment mentioning the key)."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return False
    return re.match(rf"^{re.escape(key)}\s*:", stripped) is not None

def _replace_yaml_key(line: str, key: str, value: str) -> str:
    comment = ""
    if "#" in line:
        comment = " " + line[line.index("#"):]
    indent = line[: len(line) - len(line.lstrip())]
    return f"{indent}{key}: {value}{comment}\n"

def get_active_strategy_name(cfg: dict) -> str:
    """Resolve the active strategy plugin id from bot_config.yaml."""
    strategy_block = cfg.get("strategy") or {}
    return (
        strategy_block.get("active")
        or cfg.get("active_strategy")
        or "cdc_action_zone"
    )


def list_installed_strategies() -> list:
    """Return strategy plugin ids discovered under xauby/strategies/."""
    try:
        from xauby.strategies import available_strategies
        return available_strategies()
    except Exception:
        return ["cdc_action_zone"]


def _load_bot_yaml() -> dict:
    if not os.path.exists("bot_config.yaml"):
        return {}
    try:
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _edit_bot_yaml(mutate_fn) -> bool:
    """Round-trip edit bot_config.yaml preserving comments + structure.

    Loads the file with ruamel.yaml (preserve_quotes), applies ``mutate_fn(doc)``
    to the ruamel document IN PLACE, then writes it back. Mutating the loaded doc
    (rather than dumping a fresh dict) is what keeps the file's comments intact.
    Returns True on success.
    """
    from ruamel.yaml import YAML

    path = "bot_config.yaml"
    if not os.path.exists(path):
        print(f"{RED}[ERR] bot_config.yaml not found{RESET}")
        return False
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml_rt.load(f)
        if doc is None:
            return False
        mutate_fn(doc)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml_rt.dump(doc, f)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        print(f"{RED}[ERR] Failed to save bot_config.yaml: {e}{RESET}")
        return False


def _set_yaml_path(dotted_path: str, value) -> bool:
    """Set bot_config.yaml[a][b][c] = value for 'a.b.c', creating maps as needed.

    Existing intermediate maps are reused (so their comments survive); only
    genuinely missing levels are created.
    """
    keys = [k for k in dotted_path.split(".") if k]

    def _mutate(doc) -> None:
        node = doc
        for k in keys[:-1]:
            nxt = node.get(k) if hasattr(node, "get") else None
            if not isinstance(nxt, dict):
                nxt = {}
                node[k] = nxt
            node = nxt
        node[keys[-1]] = value

    return _edit_bot_yaml(_mutate)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("_", "")


def _active_symbols_from_config(cfg: dict) -> list[str]:
    try:
        from xauby.runtime.pair_registry import PairRegistry

        reg = PairRegistry(cfg)
        reg.load(None)
        syms = reg.active_symbols()
        if syms:
            return syms
    except Exception:
        pass
    symbols = list(((cfg.get("strategy") or {}).get("symbols") or {}).keys())
    data_pairs = list((cfg.get("data") or {}).get("pairs") or [])
    merged = [_normalize_symbol(s) for s in (symbols or data_pairs)]
    if merged:
        return merged
    try:
        from xauby.runtime.symbol_resolver import focus_symbol_from_config

        focus = focus_symbol_from_config(config=cfg)
        return [focus] if focus else []
    except Exception:
        return []


def _quick_config_symbols(cfg: dict) -> list[str]:
    """Return a non-empty, normalized symbol list for interactive editors."""
    symbols = []
    seen = set()
    for raw in _active_symbols_from_config(cfg):
        symbol = _normalize_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    if not symbols:
        fallback = _normalize_symbol(default_runtime_symbol())
        if fallback:
            symbols.append(fallback)
    return symbols


def _strategy_name_for_symbol_from_cfg(cfg: dict, symbol: str) -> str:
    try:
        from xauby.runtime.trading_config import strategy_name_for_symbol

        return strategy_name_for_symbol(cfg, symbol)
    except Exception:
        sym = _normalize_symbol(symbol)
        block = ((cfg.get("strategy") or {}).get("symbols") or {}).get(sym) or {}
        return str(block.get("strategy") or (cfg.get("strategy") or {}).get("active") or "cdc_action_zone")


def _strategy_cfg_for_symbol(cfg: dict, symbol: str) -> dict:
    try:
        from xauby.runtime.trading_config import resolve_trading_config

        return dict(resolve_trading_config(cfg, symbol=symbol, for_live=True).strategy)
    except Exception:
        name = _strategy_name_for_symbol_from_cfg(cfg, symbol)
        base = dict(((cfg.get("strategy") or {}).get("config") or {}).get(name) or {})
        override = dict(((cfg.get("strategy") or {}).get("symbols") or {}).get(_normalize_symbol(symbol)) or {})
        override.pop("strategy", None)
        override.pop("strategy_name", None)
        return {**base, **override}


def _portfolio_cfg_for_symbol(cfg: dict, symbol: str) -> dict:
    try:
        from xauby.runtime.trading_config import resolve_trading_config

        return dict(resolve_trading_config(cfg, symbol=symbol, for_live=True).portfolio)
    except Exception:
        return dict((cfg.get("portfolio") or {}).get("position_sizing") or {})


def _select_symbol_interactive(cfg: dict, prompt: str = "Symbol") -> str:
    syms = _quick_config_symbols(cfg)
    print(f"\n{BOLD}{prompt}{RESET}")
    for idx, sym in enumerate(syms, 1):
        strat = _strategy_name_for_symbol_from_cfg(cfg, sym)
        print(f"  [{idx}] {sym} ({strat})")
    raw = input(f" Select symbol [1-{len(syms)}] or enter symbol: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(syms):
        return syms[int(raw) - 1]
    return _normalize_symbol(raw) if raw else syms[0]


def update_yaml_config(sim_only=None, max_risk=None, sl_atr=None, rsi_min=None, rsi_max=None, vol_min_ratio=None,
                       be_enabled=None, be_activation=None, be_buffer=None, guard_enabled=None,
                       use_fred=None, use_news=None, news_provider=None, news_model=None, news_base_url=None,
                       use_d1_regime_filter=None, active_strategy=None, symbol=None):
    """Structured bot_config.yaml updater aligned with 3-level config."""
    sym = _normalize_symbol(symbol) if symbol else ""

    def _mutate(cfg) -> None:
      if sim_only is not None:
        cfg["simulate_only"] = bool(sim_only)

      trading = cfg.setdefault("trading", {})
      risk = cfg.setdefault("risk", {})
      strategy_root = cfg.setdefault("strategy", {})
      strategy_config = strategy_root.setdefault("config", {})
      symbol_overrides = strategy_root.setdefault("symbols", {})
      portfolio = cfg.setdefault("portfolio", {})
      sizing = portfolio.setdefault("position_sizing", {})

      if max_risk is not None:
        # risk_pct is a FRACTION (0.03 = 3%) per the project convention; it is
        # written verbatim to trading/portfolio sizing keys. The legacy
        # risk.max_risk_per_trade_pct is stored in PERCENT (3.0), so convert.
        risk_val = float(max_risk)
        trading["risk_pct"] = risk_val
        risk["max_risk_per_trade_pct"] = risk_val * 100.0
        sizing["risk_pct"] = risk_val
        if sym:
            ps = portfolio.setdefault("symbols", {}).setdefault(sym, {}).setdefault("position_sizing", {})
            ps["risk_pct"] = risk_val

      target_strategy = active_strategy or (strategy_root.get("active") or "cdc_action_zone")
      if active_strategy is not None:
        if sym:
            symbol_overrides.setdefault(sym, {})["strategy"] = active_strategy
        else:
            strategy_root["active"] = active_strategy
      if sym:
        target_strategy = _strategy_name_for_symbol_from_cfg(cfg, sym)
        strat_target = symbol_overrides.setdefault(sym, {})
      else:
        strat_target = strategy_config.setdefault(str(target_strategy), {})

      for key, val in (
        ("sl_atr_mult", sl_atr),
        ("rsi_min", rsi_min),
        ("rsi_max", rsi_max),
        ("vol_min_ratio", vol_min_ratio),
        ("breakeven_sl_enabled", be_enabled),
        ("breakeven_activation_atr_mult", be_activation),
        ("breakeven_buffer_atr_mult", be_buffer),
        ("use_d1_regime_filter", use_d1_regime_filter),
      ):
        if val is not None:
            strat_target[key] = val

      guard = cfg.setdefault("macro_sentiment_guard", {})
      for key, val in (
        ("enabled", guard_enabled),
        ("use_fred", use_fred),
        ("use_news", use_news),
        ("news_provider", news_provider.lower() if isinstance(news_provider, str) else news_provider),
        ("news_model", news_model),
        ("news_base_url", news_base_url),
      ):
        if val is not None:
            guard[key] = val

    _edit_bot_yaml(_mutate)


def _set_pair_strategy_values(
    cfg: dict,
    symbol: str,
    *,
    strategy: str | None = None,
    params: dict | None = None,
) -> bool:
    """Write per-pair strategy values to the active source of truth."""
    sym = _normalize_symbol(symbol)
    try:
        from xauby.runtime.architecture_config import whitelist_strict

        if whitelist_strict(cfg):
            from xauby.runtime.pair_config import set_pair_strategy_config

            set_pair_strategy_config(sym, strategy=strategy, params=params or {}, project_root=".")
            return True
        if strategy is not None:
            update_yaml_config(active_strategy=strategy, symbol=sym)
        for key, value in (params or {}).items():
            if not _set_yaml_path(f"strategy.symbols.{sym}.{key}", value):
                return False
        return True
    except (KeyError, OSError, ValueError) as e:
        print(f"{RED}[ERR] Failed to update {sym}: {e}{RESET}")
        return False

def _harden_env_perms(path: str) -> None:
    """Restrict the .env file to owner read/write (0600).

    Secrets live here; keep them off other local users' eyes. No-op on
    platforms that ignore POSIX modes (e.g. Windows).
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def update_env_variable(key: str, val: str):
    from xauby.runtime.paths import env_file

    path = env_file()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{key}={val}\n")
        _harden_env_perms(path)
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={val}\n"
            updated = True
            break

    if not updated:
        lines.append(f"{key}={val}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    _harden_env_perms(path)

