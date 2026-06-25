"""HealthMonitor — reusable system health checks."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import sqlite3
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class HealthMonitor:
    """Aggregate health checks for CLI, cron, or in-process heartbeat."""

    def __init__(
        self,
        project_root: str = ".",
        state_file: Optional[str] = None,
        log_path: Optional[str] = None,
        db_path: Optional[str] = None,
        events_dir: Optional[str] = None,
    ):
        from xauby.runtime.paths import (
            bot_state_path,
            db_path as _db_path,
            events_dir as _events_dir,
            log_path as _log_path,
        )
        self.project_root = project_root
        self.state_file = os.path.join(project_root, state_file or bot_state_path())
        self.log_path = os.path.join(project_root, log_path or _log_path("xauby_bot.log"))
        self.db_path = os.path.join(project_root, db_path or _db_path())
        self.events_dir = os.path.join(project_root, events_dir or _events_dir())

    def get_server_resources(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        try:
            total, used, free = shutil.disk_usage(self.project_root)
            res["disk"] = {
                "total_gb": round(total / (2**30), 2),
                "used_gb": round(used / (2**30), 2),
                "free_gb": round(free / (2**30), 2),
                "pct_used": round((used / total) * 100, 2),
            }
        except Exception as e:
            res["disk"] = {"error": str(e)}

        if HAS_PSUTIL:
            try:
                mem = psutil.virtual_memory()
                res["memory"] = {
                    "total_mb": round(mem.total / (2**20), 2),
                    "available_mb": round(mem.available / (2**20), 2),
                    "used_mb": round(mem.used / (2**20), 2),
                    "pct_used": mem.percent,
                }
                res["cpu"] = {
                    "pct_used": psutil.cpu_percent(interval=0.5),
                    "cores": psutil.cpu_count(),
                }
            except Exception as e:
                res["psutil_metrics"] = {"error": str(e)}
        else:
            res["psutil_metrics"] = {"status": "psutil not installed"}
        return res

    def check_api_connectivity(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"connected": False}
        client = None
        try:
            import yaml
            from xauby.api import create_exchange_client
            from xauby.runtime.exchange_config import resolve_exchange_credentials
            from xauby.runtime.pair_registry import PairRegistry

            config_path = os.path.join(self.project_root, "bot_config.yaml")
            with open(config_path, "r", encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
            key, secret, base_url = resolve_exchange_credentials(cfg)
            client = create_exchange_client(cfg, key, secret, base_url)
            registry = PairRegistry(cfg, project_root=self.project_root)
            symbols = registry.load(None)
            symbol = symbols[0].symbol if symbols else ""
            t0 = time.time()
            ticker = client.get_ticker(symbol) if symbol else {}
            latency = (time.time() - t0) * 1000
            exchange_cfg = cfg.get("exchange") or {}
            res["exchange"] = str(exchange_cfg.get("ccxt_id") or exchange_cfg.get("name") or exchange_cfg.get("provider") or "unknown")
            res["connected"] = float(ticker.get("last") or 0.0) > 0
            res["exchange_latency_ms"] = round(latency, 2)
            res["clock_offset_sec"] = float(getattr(getattr(client, "_clock", None), "offset", 0.0))
        except Exception as e:
            res["exchange_error"] = str(e)
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass

        tg_enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true"
        res["telegram_enabled"] = tg_enabled
        if tg_enabled:
            try:
                t0 = time.time()
                requests.get("https://api.telegram.org", timeout=5)
                res["telegram_latency_ms"] = round((time.time() - t0) * 1000, 2)
                res["telegram_connected"] = True
            except Exception as e:
                res["telegram_connected"] = False
                res["telegram_error"] = str(e)
        return res

    def check_process_status(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"status": "OFFLINE", "pid": None}
        if not os.path.exists(self.state_file):
            return res
        try:
            mtime = os.path.getmtime(self.state_file)
            age = time.time() - mtime
            res["state_file_age_sec"] = round(age, 1)

            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            res["pid"] = data.get("pid")
            res["run_id"] = data.get("run_id")
            res["engine_mode"] = "SIMULATION" if data.get("simulate_only", True) else "LIVE"
            res["symbol"] = data.get("symbol")
            res["last_price"] = data.get("current_price")
            res["last_engine_timestamp"] = data.get("timestamp")
            res["ws_stale"] = age > 120

            is_active = False
            pid = res["pid"]
            if pid:
                if HAS_PSUTIL:
                    try:
                        if psutil.Process(pid).is_running():
                            is_active = True
                    except Exception:
                        pass
                elif sys.platform != "win32":
                    try:
                        os.kill(pid, 0)
                        is_active = True
                    except OSError:
                        pass

            if is_active and age < 120:
                res["status"] = "RUNNING"
            elif age < 120:
                res["status"] = "ACTIVE (Stale PID or checking fallback)"
            else:
                res["status"] = "OFFLINE"
        except Exception as e:
            res["error"] = str(e)
        return res

    def check_event_store(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"jsonl_dir": self.events_dir}
        try:
            if os.path.isdir(self.events_dir):
                files = sorted(
                    f for f in os.listdir(self.events_dir) if f.endswith(".jsonl")
                )
                res["jsonl_files"] = len(files)
                if files:
                    latest = os.path.join(self.events_dir, files[-1])
                    res["latest_jsonl"] = files[-1]
                    res["latest_jsonl_age_sec"] = round(
                        time.time() - os.path.getmtime(latest), 1
                    )
            if HAS_SQLITE and os.path.exists(self.db_path):
                conn = sqlite3.connect(self.db_path)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM events")
                    res["sqlite_event_count"] = cur.fetchone()[0]
                    cur.execute("SELECT ts FROM events ORDER BY ts DESC LIMIT 1")
                    row = cur.fetchone()
                    res["latest_event_ts"] = row[0] if row else None
                finally:
                    conn.close()
        except Exception as e:
            res["error"] = str(e)
        return res

    def scan_recent_logs(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"errors_found": [], "warnings_found": []}
        if not os.path.exists(self.log_path):
            res["status"] = "xauby_bot.log not found"
            return res
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            recent = lines[-200:]
            res["lines_scanned"] = len(recent)
            for line in recent:
                s = line.strip()
                if "ERROR" in s or "EXCEPTION" in s or "CRITICAL" in s:
                    res["errors_found"].append(s)
                elif "WARNING" in s or "WARN" in s:
                    res["warnings_found"].append(s)
            res["errors_count"] = len(res["errors_found"])
            res["warnings_count"] = len(res["warnings_found"])
            res["errors_found"] = res["errors_found"][-10:]
            res["warnings_found"] = res["warnings_found"][-10:]
        except Exception as e:
            res["error"] = str(e)
        return res

    def verify_balances_and_positions(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        sim_mode = True
        cfg_path = os.path.join(self.project_root, "bot_config.yaml")
        if HAS_YAML and os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                sim_mode = cfg.get("simulate_only", True)
            except Exception:
                pass
        res["simulate_only"] = sim_mode

        env_path = os.path.join(self.project_root, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line and not line.strip().startswith("#"):
                            k, v = line.strip().split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip())
            except Exception:
                pass

        from xauby.runtime.exchange_config import resolve_exchange_credentials
        exchange_cfg = {}
        if HAS_YAML and os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    exchange_cfg = yaml.safe_load(f) or {}
            except Exception:
                exchange_cfg = {}
        api_key, api_secret, base_url = resolve_exchange_credentials(exchange_cfg)
        if api_key and api_secret and HAS_REQUESTS:
            try:
                from xauby.api import create_exchange_client
                client = create_exchange_client(exchange_cfg, api_key, api_secret, base_url)
                balances = client.get_balances()
                res["exchange_connectivity"] = "VALIDATED"
                exchange_bals = {}
                for asset, bal in (balances or {}).items():
                    free = float(bal.get("available", bal.get("free", 0.0)) or 0.0)
                    locked = float(bal.get("reserved", bal.get("locked", 0.0)) or 0.0)
                    if free > 0 or locked > 0:
                        exchange_bals[str(asset).upper()] = {"free": free, "locked": locked}
                res["exchange_balances"] = exchange_bals
                res["can_trade"] = True
            except Exception as e:
                res["exchange_connectivity"] = f"FAILED ({e})"
            finally:
                try:
                    if 'client' in locals() and client is not None:
                        client.close()
                except Exception:
                    pass
        else:
            res["exchange_connectivity"] = "SKIPPED (API Keys missing)"

        from xauby.runtime.paths import sim_balance_path
        sim_balance_file = sim_balance_path()
        if HAS_YAML and os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                sim_balance_file = cfg.get("portfolio", {}).get("sim_balance_file")
                if not sim_balance_file:
                    config_name = os.path.splitext(os.path.basename(cfg_path))[0]
                    sim_balance_file = sim_balance_path(config_name)
            except Exception:
                pass

        sim_bal = os.path.join(self.project_root, sim_balance_file)
        if os.path.exists(sim_bal):
            try:
                with open(sim_bal, "r") as f:
                    res["simulated_balance"] = json.load(f)
            except Exception as e:
                res["simulated_balance_error"] = str(e)

        if os.path.exists(self.db_path) and HAS_SQLITE:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM trade_states")
                res["db_positions"] = [dict(r) for r in cur.fetchall()]
                cur.execute("SELECT COUNT(*) FROM closed_trades")
                res["closed_trades_count"] = cur.fetchone()[0]
            except Exception as e:
                res["db_positions_error"] = str(e)
            finally:
                if conn is not None:
                    conn.close()
        return res

    def run_full_check(self) -> Dict[str, Any]:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resources": self.get_server_resources(),
            "api_connectivity": self.check_api_connectivity(),
            "wallet_and_positions": self.verify_balances_and_positions(),
            "process_status": self.check_process_status(),
            "event_store": self.check_event_store(),
            "log_scan": self.scan_recent_logs(),
        }
        anomalies: List[str] = []

        disk = report["resources"].get("disk", {})
        if disk.get("pct_used", 0) > 90:
            anomalies.append(f"Disk space warning: {disk.get('pct_used')}% used.")

        mem = report["resources"].get("memory", {})
        if mem.get("pct_used", 0) > 90:
            anomalies.append(f"RAM warning: {mem.get('pct_used')}% used.")

        api = report["api_connectivity"]
        if not api.get("connected"):
            anomalies.append(
                f"API Connection failure: {api.get('exchange_error', 'Unknown error')}"
            )
        elif abs(api.get("clock_offset_sec", 0)) > 5.0:
            anomalies.append(
                f"System clock offset too high: {api.get('clock_offset_sec')} seconds."
            )

        proc = report["process_status"]
        if proc.get("status") == "OFFLINE":
            anomalies.append("Background Bot Engine is OFFLINE.")
        elif proc.get("ws_stale"):
            anomalies.append(
                f"Engine state file stale ({proc.get('state_file_age_sec')}s)."
            )

        logs = report["log_scan"]
        if logs.get("errors_count", 0) > 0:
            anomalies.append(f"Found {logs.get('errors_count')} errors in recent logs.")

        report["anomalies"] = anomalies
        report["status"] = (
            "ALL SYSTEMS OPERATIONAL" if not anomalies else "WARNINGS/ANOMALIES DETECTED"
        )
        return report
