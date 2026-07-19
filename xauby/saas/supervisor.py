from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from xauby.saas.catalog import (
    exchange_profile,
    preset_by_id,
    safe_risk,
    target_by_id,
    validate_profile,
)
from xauby.saas.security import validate_tenant_slug
from xauby.saas.settings import SaaSSettings
from xauby.utils.atomic_io import atomic_json_write
from xauby.runtime.manual_orders import write_manual_order_request


class TenantSupervisor:
    """Narrow adapter around tenant files and the templated systemd unit."""

    LIVE_CERTIFIED = {
        ("okx", "swap"), ("binanceusdm", "swap"), ("binance_th", "spot")
    }

    def __init__(self, settings: SaaSSettings):
        self.settings = settings
        self._credential_loader: Callable[[str], tuple[str, dict[str, str]] | None] | None = None

    def set_credential_loader(
        self, loader: Callable[[str], tuple[str, dict[str, str]] | None]
    ) -> None:
        self._credential_loader = loader

    def credential_path(self, slug: str) -> Path:
        root = self.settings.credential_runtime_root or (self.settings.data_root / "credentials")
        return root / f"{validate_tenant_slug(slug)}.env"

    def config_dir(self, slug: str) -> Path:
        return self.settings.tenant_config_root / validate_tenant_slug(slug)

    def runtime_dir(self, slug: str) -> Path:
        return self.settings.tenant_runtime_root / validate_tenant_slug(slug)

    def manual_order_path(self, slug: str) -> Path:
        return self.runtime_dir(slug) / "manual_order_request.json"

    def queue_manual_order(
        self,
        slug: str,
        *,
        symbol: str,
        intent: str,
        request_id: str,
        management_mode: str = "strategy_handoff",
    ) -> dict[str, Any]:
        return write_manual_order_request(
            symbol,
            intent,
            management_mode=management_mode,
            source="saas_control",
            request_id=request_id,
            request_path=str(self.manual_order_path(slug)),
        )

    @staticmethod
    def _restore_control_acl(path: Path, permissions: str) -> None:
        try:
            subprocess.run(
                ["setfacl", "-m", f"u:xauby-control:{permissions}", str(path)],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    @classmethod
    def _set_config_permissions(cls, config_dir: Path) -> None:
        for name in ("bot_config.yaml", "coin_whitelist.json"):
            path = config_dir / name
            try:
                os.chmod(path, 0o640)
            except OSError:
                pass
            # Tenant files are owned by the isolated engine user.  chmod(640)
            # tightens the ACL mask as well, which used to turn the control
            # plane's provisioned rw ACL into read-only and made Live activation
            # fail with EACCES.  Restore only the control-plane file ACL; the
            # engine still receives read access through its owning user/group.
            cls._restore_control_acl(path, "rw")

    @staticmethod
    def _environment_line(key: str, value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'

    def provision(self, slug: str) -> None:
        tenant = validate_tenant_slug(slug)
        if self.settings.provision_helper:
            result = subprocess.run(
                ["sudo", "-n", self.settings.provision_helper, tenant], capture_output=True,
                text=True, timeout=20, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "tenant provision helper failed").strip())
        config_dir, runtime_dir = self.config_dir(tenant), self.runtime_dir(tenant)
        config_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        source_yaml = self.settings.project_root / "bot_config.yaml"
        source_whitelist = self.settings.project_root / "coin_whitelist.json"
        target_yaml = config_dir / "bot_config.yaml"
        target_whitelist = config_dir / "coin_whitelist.json"
        if not target_yaml.exists():
            cfg = yaml.safe_load(source_yaml.read_text(encoding="utf-8")) or {}
            cfg["simulate_only"] = True
            cfg["read_only"] = False
            cfg.setdefault("trading", {}).update({
                "risk_pct": 0.01, "max_position_per_trade_pct": 10.0,
                "max_daily_loss_pct": 3.0, "max_open_positions": 1,
            })
            cfg.setdefault("risk", {}).update({
                "risk_pct": 0.01, "max_position_per_trade_pct": 10.0,
                "max_daily_loss_pct": 3.0, "stop_loss_pct": 2.0,
            })
            cfg.setdefault("derivatives", {}).update({"default_leverage": 1, "max_leverage": 2})
            cfg.setdefault("data", {}).setdefault(
                "dashboard_timeframes", ["1h", "4h", "1d"]
            )
            target_yaml.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        if not target_whitelist.exists():
            whitelist = json.loads(source_whitelist.read_text(encoding="utf-8"))
            for asset in whitelist.get("assets") or []:
                asset["mode"] = "sim"
                asset["short_live_enabled"] = False
                asset["leverage"] = 1.0
                params = asset.setdefault("strategy_params", {})
                params["disable_stop_loss"] = False
                params["position_pct"] = min(float(params.get("position_pct", 0.1) or 0.1), 0.1)
            atomic_json_write(str(target_whitelist), whitelist, indent=2)
        for path, mode in ((config_dir, 0o700), (runtime_dir, 0o700)):
            try:
                os.chmod(path, mode)
            except OSError:
                pass
            self._restore_control_acl(path, "rwx")
        self._set_config_permissions(config_dir)

    def _systemctl(self, action: str, slug: str) -> dict[str, Any]:
        tenant = validate_tenant_slug(slug)
        if action not in {"start", "stop", "restart"}:
            raise ValueError("unsupported engine lifecycle action")
        unit = self.settings.engine_unit_template.format(tenant=tenant)
        if self.settings.systemctl_bin.lower() in {"mock", "none"}:
            return {"ok": True, "unit": unit, "action": action, "mock": True}
        command = (
            ["sudo", "-n", self.settings.service_helper, action, tenant]
            if self.settings.service_helper
            else [self.settings.systemctl_bin, action, unit]
        )
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "systemctl failed").strip())
        return {"ok": True, "unit": unit, "action": action}

    def start(self, slug: str) -> dict[str, Any]:
        self.provision(slug)
        self.materialize_credentials(slug)
        return self._systemctl("start", slug)

    def stop(self, slug: str) -> dict[str, Any]:
        result = self._systemctl("stop", slug)
        self.clear_credentials(slug)
        return result

    def restart(self, slug: str) -> dict[str, Any]:
        self.materialize_credentials(slug)
        return self._systemctl("restart", slug)

    def status(self, slug: str) -> str:
        tenant = validate_tenant_slug(slug)
        if self.settings.systemctl_bin.lower() in {"mock", "none"}:
            return "unknown"
        unit = self.settings.engine_unit_template.format(tenant=tenant)
        command = (
            ["sudo", "-n", self.settings.service_helper, "is-active", tenant]
            if self.settings.service_helper
            else [self.settings.systemctl_bin, "is-active", unit]
        )
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=10, check=False,
        )
        return (result.stdout or "inactive").strip()

    def read_state(self, slug: str) -> dict[str, Any]:
        path = self.runtime_dir(slug) / "logs" / "xauby_bot_state.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def read_curated_config(self, slug: str) -> dict[str, Any]:
        self.provision(slug)
        cfg = yaml.safe_load((self.config_dir(slug) / "bot_config.yaml").read_text(encoding="utf-8")) or {}
        whitelist = json.loads((self.config_dir(slug) / "coin_whitelist.json").read_text(encoding="utf-8"))
        trading = cfg.get("trading") or {}
        portfolio = cfg.get("portfolio") or {}
        sizing = portfolio.get("position_sizing") or {}
        symbol = str(trading.get("trading_pair") or "").upper().replace("_", "")
        symbol_cfg = (portfolio.get("symbols") or {}).get(symbol) or {}
        symbol_sizing = symbol_cfg.get("position_sizing") or {}
        max_position = (
            symbol_sizing.get("max_position_per_trade_pct")
            if symbol_sizing.get("max_position_per_trade_pct") is not None
            else sizing.get("max_position_per_trade_pct")
            if sizing.get("max_position_per_trade_pct") is not None
            else trading.get("max_position_per_trade_pct", 10.0)
        )
        return {
            "simulate_only": bool(cfg.get("simulate_only", True)),
            "max_open_positions": int(trading.get("max_open_positions", 1)),
            "risk_pct": float(trading.get("risk_pct", 0.01)),
            "max_position_per_trade_pct": float(max_position),
            "max_daily_loss_pct": float(trading.get("max_daily_loss_pct", 3.0)),
            "assets": whitelist.get("assets") or [],
        }

    def update_curated_config(self, slug: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.provision(slug)
        allowed = {
            "risk_pct": (0.001, 0.01),
            "max_position_per_trade_pct": (1.0, 95.0),
            "max_daily_loss_pct": (1.0, 3.0),
        }
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        trading = cfg.setdefault("trading", {})
        risk = cfg.setdefault("risk", {})
        portfolio = cfg.setdefault("portfolio", {})
        sizing = portfolio.setdefault("position_sizing", {})
        symbol = str(trading.get("trading_pair") or "").upper().replace("_", "")
        symbol_cfg = (portfolio.setdefault("symbols", {})).setdefault(symbol, {}) if symbol else {}
        symbol_sizing = symbol_cfg.setdefault("position_sizing", {}) if symbol else {}
        for key, (minimum, maximum) in allowed.items():
            if key not in changes:
                continue
            value = float(changes[key])
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
            trading[key] = value
            if key in {"max_position_per_trade_pct", "max_daily_loss_pct"}:
                risk[key] = value
            if key == "max_position_per_trade_pct":
                sizing[key] = value
                if symbol:
                    symbol_sizing[key] = value
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        self._set_config_permissions(self.config_dir(slug))
        return self.read_curated_config(slug)

    def apply_profile(
        self,
        slug: str,
        preset_ids: list[str],
        active_preset_id: str,
        risk_values: dict[str, Any],
        *,
        preserve_live: bool = False,
    ) -> dict[str, Any]:
        """Compile catalog choices into bounded files; raw strategy config is never accepted."""
        profile = validate_profile(preset_ids, active_preset_id, risk_values)
        self.provision(slug)
        bounded = safe_risk(profile["risk"])
        bounded["stop_loss_required"] = bool(profile["risk"].get("stop_loss_required", True))
        pair_count = len(profile["preset_ids"])
        active_symbol = str(
            next(
                preset["symbol"]
                for preset in profile["presets"]
                if preset["id"] == profile["active_preset_id"]
            )
        )
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cfg["simulate_only"] = True
        exchange_recipe = self._apply_exchange_profile(cfg, profile["target_id"])
        cfg.setdefault("trading", {}).update({
            "risk_pct": bounded["risk_pct"],
            "max_position_per_trade_pct": bounded["max_position_per_trade_pct"],
            "max_daily_loss_pct": bounded["max_daily_loss_pct"],
            "max_open_positions": pair_count,
            "trading_pair": active_symbol,
        })
        cfg.setdefault("risk", {}).update({
            "risk_pct": bounded["risk_pct"],
            "max_position_per_trade_pct": bounded["max_position_per_trade_pct"],
            "max_daily_loss_pct": bounded["max_daily_loss_pct"],
            "stop_loss_pct": 2.0,
            "stop_loss_required": bounded["stop_loss_required"],
        })
        portfolio = cfg.setdefault("portfolio", {})
        portfolio["quote_asset"] = exchange_recipe["exchange"]["quote_asset"]
        sizing = portfolio.setdefault("position_sizing", {})
        sizing["max_position_per_trade_pct"] = bounded["max_position_per_trade_pct"]
        for preset in profile["presets"]:
            symbol_cfg = portfolio.setdefault("symbols", {}).setdefault(preset["symbol"], {})
            if preset.get("allocation_pct") is not None:
                symbol_cfg["allocation_pct"] = float(preset["allocation_pct"])
            symbol_sizing = symbol_cfg.setdefault("position_sizing", {})
            symbol_sizing["risk_pct"] = float(
                preset.get("risk_pct", bounded["risk_pct"])
            )
            symbol_sizing["max_position_per_trade_pct"] = float(
                preset.get(
                    "max_position_per_trade_pct",
                    bounded["max_position_per_trade_pct"],
                )
            )
        cfg.setdefault("derivatives", {}).update({
            "default_leverage": bounded["max_leverage"],
            "max_leverage": bounded["max_leverage"],
        })
        wl_recipe = exchange_recipe["whitelist"]
        market_type = exchange_recipe["exchange"].get("market_type", "spot")
        assets = []
        for preset_id in profile["preset_ids"]:
            preset = preset_by_id(preset_id)
            execution_profile = dict(preset.get("execution_profile") or {})
            cdc_pure = bool(preset.get("cdc_pure_certified"))
            params: dict[str, Any] = {
                "disable_stop_loss": bool(execution_profile.get("disable_stop_loss", False)),
                "position_pct": float(execution_profile.get("position_pct", 0.1) or 0.1),
            }
            if preset["strategy"] == "xauby_actionzone":
                params.update({
                    "backtest_data_proxy": "PAXGUSDT", "enable_short": False,
                    "ap_smoothing": 2, "sl_atr_mult": 2.0, "trailing_atr_mult": 1.8,
                })
            params.update(execution_profile)
            # Sim previews the certified sides; spot can never carry a short.
            sides = [s for s in (preset.get("allowed_sides") or ["long"]) if s in {"long", "short"}]
            if market_type != "swap":
                sides = ["long"]
            if "long" not in sides:
                sides = ["long", *sides]
            assets.append({
                "symbol": preset["asset"], "enabled": True,
                "preset_id": preset_id,
                "primary_timeframe": preset["primary_timeframe"],
                "confirm_timeframe": preset["confirm_timeframe"],
                "strategy": preset["strategy"], "strategy_params": params,
                "cdc_pure_certified": cdc_pure,
                "live_certified": bool(preset.get("live_certified")),
                "mode": "sim", "allowed_sides": sides, "leverage": 1.0,
                "manual_allowed_sides": ["long", "short"]
                if market_type == "swap" else ["long"],
                "manual_short_live_enabled": False,
                "short_live_enabled": False, "regime_router_enabled": False,
                "regime_router_live_confirmed": False,
                "sim_fee_pct": wl_recipe["sim_fee_pct"],
            })
        whitelist = {
            "version": 1,
            "quote_asset": wl_recipe["quote_asset"],
            "min_quote_balance_usdt": wl_recipe["min_quote_balance"],
            "min_quote_balance_for_pairs": 0,
            "require_supported_market": True,
            "include_assets_with_balance": False,
            "assets": assets,
        }
        if preserve_live:
            self._enable_live_payload(cfg, whitelist, profile["target_id"])

        # The engine watches the whitelist for hot reload. Write YAML first so
        # an accepted whitelist mtime always points at its matching risk and
        # portfolio configuration.
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        atomic_json_write(
            str(self.config_dir(slug) / "coin_whitelist.json"), whitelist, indent=2
        )
        self._set_config_permissions(self.config_dir(slug))
        return profile

    @staticmethod
    def _apply_exchange_profile(cfg: dict[str, Any], target_id: str) -> dict[str, Any]:
        """Write the full per-target exchange recipe into a tenant config.

        The exchange block is replaced wholesale so keys from a previous
        target (params/base_url/settle_asset) can never leak across a switch.
        Returns the applied profile.
        """
        profile = exchange_profile(target_id)
        cfg["exchange"] = dict(profile["exchange"])
        derivatives = cfg.setdefault("derivatives", {})
        derivatives["market_type"] = profile["derivatives"]["market_type"]
        return profile

    def configure_exchange(self, slug: str, target_id: str) -> None:
        self.provision(slug)
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        self._apply_exchange_profile(cfg, target_id)
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        self._set_config_permissions(self.config_dir(slug))

    @staticmethod
    def _credential_env_names(cfg: dict[str, Any]) -> tuple[str, str, str]:
        """Env-var names the engine will read, straight from the tenant YAML.

        Single contract shared with xauby.runtime.exchange_config: explicit
        api_key_env/api_secret_env win; the passphrase env swaps the _API_KEY
        suffix (OKX_API_KEY -> OKX_API_PASSPHRASE, matching the ccxt adapter's
        prefix-derived lookup).
        """
        exchange_cfg = cfg.get("exchange") or {}
        raw = str(
            exchange_cfg.get("ccxt_id")
            or exchange_cfg.get("name")
            or exchange_cfg.get("provider")
            or "binance"
        )
        prefix = "".join(ch if ch.isalnum() else "_" for ch in raw).upper()
        key_env = str(exchange_cfg.get("api_key_env") or f"{prefix}_API_KEY")
        secret_env = str(exchange_cfg.get("api_secret_env") or f"{prefix}_API_SECRET")
        if key_env.endswith("_API_KEY"):
            passphrase_env = key_env[: -len("_API_KEY")] + "_API_PASSPHRASE"
        else:
            passphrase_env = f"{prefix}_API_PASSPHRASE"
        return key_env, secret_env, passphrase_env

    def materialize_credentials(self, slug: str) -> Path | None:
        if self._credential_loader is None:
            return None
        loaded = self._credential_loader(validate_tenant_slug(slug))
        if loaded is None:
            return None
        _target_id, credentials = loaded
        if any("\n" in value or "\r" in value for value in credentials.values()):
            raise ValueError("exchange credentials may not contain line breaks")
        cfg = yaml.safe_load(
            (self.config_dir(slug) / "bot_config.yaml").read_text(encoding="utf-8")
        ) or {}
        key_env, secret_env, passphrase_env = self._credential_env_names(cfg)
        lines = [
            self._environment_line(key_env, credentials.get("api_key", "")),
            self._environment_line(secret_env, credentials.get("api_secret", "")),
        ]
        if credentials.get("passphrase"):
            lines.append(
                self._environment_line(passphrase_env, credentials["passphrase"])
            )
        live = not bool(cfg.get("simulate_only", True))
        lines.extend([
            f"LIVE_TRADING={'true' if live else 'false'}",
            f"SIMULATE_ONLY={'false' if live else 'true'}",
        ])
        path = self.credential_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def clear_credentials(self, slug: str) -> None:
        try:
            self.credential_path(slug).unlink()
        except FileNotFoundError:
            pass

    def probe_exchange(self, slug: str, credentials: dict[str, str]) -> dict[str, Any]:
        """Run the credential/capability probe in a one-shot child process."""
        self.provision(slug)
        env = dict(os.environ)
        cfg = yaml.safe_load(
            (self.config_dir(slug) / "bot_config.yaml").read_text(encoding="utf-8")
        ) or {}
        key_env, secret_env, passphrase_env = self._credential_env_names(cfg)
        env[key_env] = credentials.get("api_key", "")
        env[secret_env] = credentials.get("api_secret", "")
        if credentials.get("passphrase"):
            env[passphrase_env] = credentials["passphrase"]
        result = subprocess.run(
            [sys.executable, "-m", "xauby.saas.probe", "--config-dir", str(self.config_dir(slug))],
            cwd=str(self.settings.project_root), env=env, capture_output=True,
            text=True, timeout=45, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "exchange probe failed").strip())
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise RuntimeError("exchange probe returned invalid output") from exc

    @classmethod
    def _enable_live_payload(
        cls,
        cfg: dict[str, Any],
        whitelist: dict[str, Any],
        target_id: str,
    ) -> None:
        target = target_by_id(target_id)
        exchange = str(target["exchange_id"]).lower()
        market = str(target["market_type"]).lower()
        if (exchange, market) not in cls.LIVE_CERTIFIED:
            raise ValueError(f"{exchange}/{market} is not live certified")
        if not target.get("live_certified"):
            raise ValueError(f"{target_id} is not live certified")
        cfg["simulate_only"] = False
        cfg["read_only"] = False
        cls._apply_exchange_profile(cfg, target_id)
        cfg.setdefault("derivatives", {})["default_leverage"] = 1
        cfg["derivatives"]["max_leverage"] = 1
        any_cdc_live = False
        enabled_count = 0
        live_count = 0
        for asset in whitelist.get("assets") or []:
            params = asset.setdefault("strategy_params", {})
            cdc_pure = bool(asset.get("cdc_pure_certified"))
            if bool(params.get("disable_stop_loss")) and not cdc_pure:
                raise ValueError("live activation requires a stop loss")
            if cdc_pure:
                # CDC ActionZone stop-and-reverse: long + short, D1 filter off
                # (July 2026 PAXGUSDT study: full-cycle +105% vs +78% long-only,
                # max DD 8.4% at 1x, net of fee/slippage/funding).
                params["disable_stop_loss"] = True
                params["position_pct"] = 0.95
                params["enable_short"] = True
                params["use_d1_regime_filter"] = False
                if market == "swap":
                    asset["allowed_sides"] = ["long", "short"]
            else:
                params["disable_stop_loss"] = False
                params["position_pct"] = min(float(params.get("position_pct", 0.1) or 0.1), 0.1)
            enabled = bool(asset.get("enabled", True))
            # Legacy whitelists predate the per-asset flag; only catalog code
            # writes these files and every legacy asset already passed the
            # stop-loss/CDC check above, so missing defaults to certified.
            live_ok = enabled and bool(asset.get("live_certified", True))
            asset["mode"] = "live" if live_ok else "sim"
            asset["leverage"] = 1.0
            asset["short_live_enabled"] = live_ok and "short" in (asset.get("allowed_sides") or [])
            asset["manual_short_live_enabled"] = bool(
                live_ok
                and target.get("manual_short_live_certified")
                and "short" in (asset.get("manual_allowed_sides") or [])
            )
            enabled_count += 1 if enabled else 0
            live_count += 1 if live_ok else 0
            any_cdc_live = any_cdc_live or (live_ok and cdc_pure)
        if live_count == 0:
            raise ValueError("no live-certified pair is selected")
        cfg.setdefault("trading", {})["max_open_positions"] = max(1, enabled_count)
        cfg.setdefault("risk", {})["stop_loss_required"] = not any_cdc_live
        cfg.setdefault("execution", {})["live_strategy_mode"] = (
            "cdc_pure" if any_cdc_live else "stop_loss"
        )

    def set_live_mode(self, slug: str, target_id: str) -> None:
        self.provision(slug)
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        wl_path = self.config_dir(slug) / "coin_whitelist.json"
        whitelist = json.loads(wl_path.read_text(encoding="utf-8"))
        self._enable_live_payload(cfg, whitelist, target_id)
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        atomic_json_write(str(wl_path), whitelist, indent=2)
        self._set_config_permissions(self.config_dir(slug))

    def set_sim_mode(self, slug: str) -> None:
        """Restore all hosted safety gates after a failed live activation."""
        self.provision(slug)
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cfg["simulate_only"] = True
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        wl_path = self.config_dir(slug) / "coin_whitelist.json"
        whitelist = json.loads(wl_path.read_text(encoding="utf-8"))
        for asset in whitelist.get("assets") or []:
            asset["mode"] = "sim"
            asset["short_live_enabled"] = False
        atomic_json_write(str(wl_path), whitelist, indent=2)
        self._set_config_permissions(self.config_dir(slug))
