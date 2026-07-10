"""Marketplace-facing strategy manifest helpers."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Type

from xauby.strategies.base import Strategy

MANIFEST_VERSION = 1
MANIFEST_FILENAMES = ("strategy.yaml", "strategy.yml", "manifest.yaml")
DEFAULT_PERMISSIONS = {
    "network": False,
    "database": False,
    "orders": False,
}


def _manifest_path_for_class(cls: Type[Strategy]) -> str:
    mod = sys.modules.get(cls.__module__)
    mod_file = getattr(mod, "__file__", "") if mod is not None else ""
    if not mod_file:
        return ""
    root = os.path.dirname(os.path.abspath(mod_file))
    for name in MANIFEST_FILENAMES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return ""


def _load_manifest_file(path: str) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def strategy_manifest_from_class(cls: Type[Strategy]) -> Dict[str, Any]:
    """Return a marketplace manifest for a strategy class.

    Built-in strategies can optionally ship a YAML manifest. Missing fields
    fall back to class metadata so existing in-repo plugins keep working.
    """
    file_manifest = _load_manifest_file(_manifest_path_for_class(cls))
    default_config = cls.default_config()
    base = {
        "manifest_version": MANIFEST_VERSION,
        "id": cls.name,
        "name": getattr(cls, "display_name", cls.name),
        "version": getattr(cls, "version", "0.0.0"),
        "author": getattr(cls, "author", "unknown"),
        "description": getattr(cls, "description", ""),
        "tags": list(getattr(cls, "tags", []) or []),
        "required_timeframes": list(getattr(cls, "required_timeframes", []) or []),
        "min_bars": int(getattr(cls, "min_bars", 0) or 0),
        "default_config": dict(default_config or {}),
        "config_schema": cls({})._config_schema(),
        "permissions": dict(DEFAULT_PERMISSIONS),
        "source": "builtin",
        "entrypoint": f"{cls.__module__}:{cls.__name__}",
    }
    base.update({k: v for k, v in file_manifest.items() if v is not None})
    base["id"] = str(base.get("id") or cls.name)
    base["permissions"] = {
        **DEFAULT_PERMISSIONS,
        **dict(base.get("permissions") or {}),
    }
    return base
