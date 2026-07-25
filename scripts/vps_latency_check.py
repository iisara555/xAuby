#!/usr/bin/env python3
"""Read-only VPS latency and host health checks for xAuby.

The script is intentionally dependency-free and safe to run on production VPSes.
It reports rough network timings to the active OKX endpoint plus local load,
disk, and clock-sync hints that commonly explain bot lag.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, List


DEFAULT_HOSTS = ("api.okx.com",)


def _run(cmd: List[str], timeout: float = 10.0) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "stdout": proc.stdout.strip()[-2000:],
            "stderr": proc.stderr.strip()[-2000:],
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": str(exc),
        }


def _tcp_connect(host: str, port: int = 443, timeout: float = 5.0) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": str(exc),
        }


def _curl_timing(url: str) -> Dict[str, Any]:
    fmt = (
        "dns=%{time_namelookup} "
        "connect=%{time_connect} "
        "tls=%{time_appconnect} "
        "ttfb=%{time_starttransfer} "
        "total=%{time_total} "
        "code=%{http_code}"
    )
    return _run(
        ["curl", "-sS", "-o", "/dev/null", "-w", fmt, "--max-time", "10", url],
        timeout=12.0,
    )


def _host_health(path: str) -> Dict[str, Any]:
    loadavg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    total, used, free = shutil.disk_usage(path)
    return {
        "loadavg": [round(x, 2) for x in loadavg],
        "disk": {
            "path": path,
            "used_pct": round((used / total) * 100.0, 1),
            "free_gb": round(free / (1024**3), 2),
        },
    }


def collect(hosts: List[str], project_root: str) -> Dict[str, Any]:
    checks: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host_health": _host_health(project_root),
        "clock": _run(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "SystemClockSynchronized"], timeout=5.0),
        "targets": {},
    }
    for host in hosts:
        checks["targets"][host] = {
            "dns": _run(["getent", "hosts", host], timeout=5.0),
            "ping": _run(["ping", "-c", "5", "-W", "2", host], timeout=15.0),
            "tcp_443": _tcp_connect(host),
            "curl_https": _curl_timing(f"https://{host}"),
        }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Check VPS latency to OKX.")
    parser.add_argument("--host", action="append", dest="hosts", help="Extra host to test")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--project-root", default=".", help="Path for disk usage check")
    args = parser.parse_args()

    hosts = list(DEFAULT_HOSTS)
    for h in args.hosts or []:
        if h not in hosts:
            hosts.append(h)

    data = collect(hosts, args.project_root)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    print("xAuby VPS latency check")
    print(f"timestamp: {data['ts']}")
    health = data["host_health"]
    print(f"loadavg: {health['loadavg']}  disk_used: {health['disk']['used_pct']}%  free: {health['disk']['free_gb']} GB")
    clock = data["clock"]
    print(f"clock: ok={clock.get('ok')} {clock.get('stdout') or clock.get('error') or clock.get('stderr')}")
    for host, res in data["targets"].items():
        print(f"\n[{host}]")
        print(f"  tcp443: {res['tcp_443'].get('elapsed_ms')}ms ok={res['tcp_443'].get('ok')}")
        print(f"  ping: ok={res['ping'].get('ok')} elapsed={res['ping'].get('elapsed_ms')}ms")
        if res["ping"].get("stdout"):
            print("  " + res["ping"]["stdout"].splitlines()[-1])
        print(f"  curl: ok={res['curl_https'].get('ok')} {res['curl_https'].get('stdout') or res['curl_https'].get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
