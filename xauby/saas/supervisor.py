from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from xauby.saas.catalog import preset_by_id, safe_risk, validate_profile
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

    def apply_profile(self, slug: str, preset_ids: list[str], active_preset_id: str,
                      risk_values: dict[str, Any]) -> dict[str, Any]:
        """Compile catalog choices into bounded files; raw strategy config is never accepted."""
        profile = validate_profile(preset_ids, active_preset_id, risk_values)
        self.provision(slug)
        bounded = safe_risk(profile["risk"])
        bounded["stop_loss_required"] = bool(profile["risk"].get("stop_loss_required", True))
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cfg["simulate_only"] = True
        cfg.setdefault("trading", {}).update({
            "risk_pct": bounded["risk_pct"],
            "max_position_per_trade_pct": bounded["max_position_per_trade_pct"],
            "max_daily_loss_pct": bounded["max_daily_loss_pct"],
            "max_open_positions": 1,
        })
        cfg.setdefault("risk", {}).update({
            "risk_pct": bounded["risk_pct"],
            "max_position_per_trade_pct": bounded["max_position_per_trade_pct"],
            "max_daily_loss_pct": bounded["max_daily_loss_pct"],
            "stop_loss_pct": 2.0,
            "stop_loss_required": bounded["stop_loss_required"],
        })
        portfolio = cfg.setdefault("portfolio", {})
        sizing = portfolio.setdefault("position_sizing", {})
        sizing["max_position_per_trade_pct"] = bounded["max_position_per_trade_pct"]
        symbol = str((cfg.get("trading") or {}).get("trading_pair") or "").upper().replace("_", "")
        if symbol:
            symbol_cfg = portfolio.setdefault("symbols", {}).setdefault(symbol, {})
            symbol_cfg.setdefault("position_sizing", {})[
                "max_position_per_trade_pct"
            ] = bounded["max_position_per_trade_pct"]
        cfg.setdefault("derivatives", {}).update({
            "default_leverage": bounded["max_leverage"],
            "max_leverage": bounded["max_leverage"],
        })
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

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
            assets.append({
                "symbol": preset["asset"], "enabled": preset_id == active_preset_id,
                "primary_timeframe": preset["primary_timeframe"],
                "confirm_timeframe": preset["confirm_timeframe"],
                "strategy": preset["strategy"], "strategy_params": params,
                "cdc_pure_certified": cdc_pure,
                "mode": "sim", "allowed_sides": ["long"], "leverage": 1.0,
                "manual_allowed_sides": ["long", "short"]
                if preset["market_type"] == "swap" else ["long"],
                "manual_short_live_enabled": False,
                "short_live_enabled": False, "regime_router_enabled": False,
                "regime_router_live_confirmed": False, "sim_fee_pct": 0.05,
            })
        atomic_json_write(
            str(self.config_dir(slug) / "coin_whitelist.json"),
            {"version": 1, "quote_asset": "USDT", "min_quote_balance_usdt": 10.0,
             "min_quote_balance_for_pairs": 0, "require_supported_market": True,
             "include_assets_with_balance": False, "assets": assets},
            indent=2,
        )
        self.set_sim_mode(slug)
        self._set_config_permissions(self.config_dir(slug))
        return profile

    def configure_exchange(self, slug: str, exchange_id: str) -> None:
        self.provision(slug)
        exchange = str(exchange_id or "").strip().lower()
        prefix = "".join(ch if ch.isalnum() else "_" for ch in exchange).upper()
        if not prefix:
            raise ValueError("exchange is required")
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        exchange_cfg = cfg.setdefault("exchange", {})
        exchange_cfg.update({
            "provider": "ccxt", "ccxt_id": exchange,
            "api_key_env": f"{prefix}_API_KEY", "api_secret_env": f"{prefix}_API_SECRET",
        })
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        self._set_config_permissions(self.config_dir(slug))

    def materialize_credentials(self, slug: str) -> Path | None:
        if self._credential_loader is None:
            return None
        loaded = self._credential_loader(validate_tenant_slug(slug))
        if loaded is None:
            return None
        exchange, credentials = loaded
        if any("\n" in value or "\r" in value for value in credentials.values()):
            raise ValueError("exchange credentials may not contain line breaks")
        prefix = "".join(ch if ch.isalnum() else "_" for ch in exchange).upper()
        cfg = yaml.safe_load(
            (self.config_dir(slug) / "bot_config.yaml").read_text(encoding="utf-8")
        ) or {}
        lines = [
            self._environment_line(f"{prefix}_API_KEY", credentials.get("api_key", "")),
            self._environment_line(f"{prefix}_API_SECRET", credentials.get("api_secret", "")),
        ]
        if credentials.get("passphrase"):
            lines.append(
                self._environment_line(
                    f"{prefix}_API_PASSPHRASE", credentials["passphrase"]
                )
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
        exchange = str((cfg.get("exchange") or {}).get("ccxt_id") or "")
        prefix = "".join(ch if ch.isalnum() else "_" for ch in exchange).upper()
        env[f"{prefix}_API_KEY"] = credentials.get("api_key", "")
        env[f"{prefix}_API_SECRET"] = credentials.get("api_secret", "")
        if credentials.get("passphrase"):
            env[f"{prefix}_API_PASSPHRASE"] = credentials["passphrase"]
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

    def set_live_mode(self, slug: str, exchange_id: str, market_type: str) -> None:
        exchange = str(exchange_id).lower()
        market = str(market_type).lower()
        if (exchange, market) not in self.LIVE_CERTIFIED:
            raise ValueError(f"{exchange}/{market} is not live certified")
        self.provision(slug)
        cfg_path = self.config_dir(slug) / "bot_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cfg["simulate_only"] = False
        cfg["read_only"] = False
        cfg.setdefault("derivatives", {})["default_leverage"] = 1
        cfg["derivatives"]["max_leverage"] = 1
        cfg.setdefault("trading", {})["max_open_positions"] = 1
        cdc_pure = False
        wl_path = self.config_dir(slug) / "coin_whitelist.json"
        whitelist = json.loads(wl_path.read_text(encoding="utf-8"))
        for asset in whitelist.get("assets") or []:
            params = asset.setdefault("strategy_params", {})
            cdc_pure = bool(
                asset.get("cdc_pure_certified")
                and asset.get("symbol") == "XAU"
                and asset.get("strategy") == "xauby_actionzone"
            )
            if bool(params.get("disable_stop_loss")) and not cdc_pure:
                raise ValueError("live activation requires a stop loss")
            if cdc_pure:
                params["disable_stop_loss"] = True
                params["position_pct"] = 0.95
                params["enable_short"] = False
                asset["allowed_sides"] = ["long"]
            else:
                params["disable_stop_loss"] = False
                params["position_pct"] = min(float(params.get("position_pct", 0.1) or 0.1), 0.1)
            asset["mode"] = "live" if asset.get("enabled", True) else "sim"
            asset["leverage"] = 1.0
            asset["short_live_enabled"] = False if cdc_pure else "short" in (asset.get("allowed_sides") or [])
        cfg.setdefault("risk", {})["stop_loss_required"] = not cdc_pure
        cfg.setdefault("execution", {})["live_strategy_mode"] = "cdc_pure" if cdc_pure else "stop_loss"
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
