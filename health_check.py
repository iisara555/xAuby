#!/usr/bin/env python3
"""CLI wrapper for HealthMonitor (backward-compatible with original health_check.py)."""
import json
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.environ.setdefault("BINANCE_BASE_URL", "https://api.binance.th")

from xauby.observability.health import HealthMonitor


def run_health_check():
    monitor = HealthMonitor(project_root=project_root)
    report = monitor.run_full_check()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run_health_check()
