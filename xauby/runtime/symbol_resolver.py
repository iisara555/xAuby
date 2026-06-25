"""Central symbol resolution from PairRegistry (no hardcoded trading symbols)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

from xauby.runtime.pair_registry import PairRegistry


def _load_config(config_path: str = "bot_config.yaml") -> Dict[str, Any]:
    path = config_path
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def pair_registry_from_config(
    config: Optional[Dict[str, Any]] = None,
    *,
    config_path: str = "bot_config.yaml",
    project_root: str = ".",
    client: Any = None,
) -> PairRegistry:
    cfg = config if config is not None else _load_config(config_path)
    reg = PairRegistry(cfg, config_path=config_path, project_root=project_root)
    reg.load(client)
    return reg


def active_symbols_from_config(
    config: Optional[Dict[str, Any]] = None,
    *,
    config_path: str = "bot_config.yaml",
    project_root: str = ".",
    client: Any = None,
) -> List[str]:
    return pair_registry_from_config(
        config,
        config_path=config_path,
        project_root=project_root,
        client=client,
    ).active_symbols()


def focus_symbol_from_config(
    preferred: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    *,
    config_path: str = "bot_config.yaml",
    project_root: str = ".",
    client: Any = None,
) -> str:
    reg = pair_registry_from_config(
        config,
        config_path=config_path,
        project_root=project_root,
        client=client,
    )
    return reg.focus_symbol(preferred)
