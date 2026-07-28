import json
from datetime import datetime, timezone
from types import SimpleNamespace

from xauby.notifications.scheduler import ReportScheduler


TRADE = {
    "symbol": "BTCUSDT",
    "amount": 1.0,
    "entry_price": 100.0,
    "exit_price": 110.0,
    "entry_cost": 100.0,
    "total_fees": 0.105,
    "funding_fee": 0.0,
    "net_pnl": 8.95,
    "net_pnl_pct": 8.95,
    "execution_mode": "live",
    "opened_at": "2026-07-20T00:00:00+00:00",
    "closed_at": "2026-07-25T00:00:00+00:00",
}

EVENTS = [
    {
        "seq": 1,
        "ts": "2026-07-20T00:00:00+00:00",
        "event_type": "position_opened",
        "symbol": "BTCUSDT",
        "payload": {"slippage_bps": 1.0},
    },
    {
        "seq": 2,
        "ts": "2026-07-25T00:00:00+00:00",
        "event_type": "position_closed",
        "symbol": "BTCUSDT",
        "payload": {"slippage_bps": 1.0},
    },
]


class _DB:
    def get_closed_trades(self, symbol, limit=5000):
        return [dict(TRADE)]

    def query_events(self, *, event_type, **kwargs):
        return [dict(event) for event in EVENTS if event["event_type"] == event_type]


class _Registry:
    def get(self, symbol):
        return SimpleNamespace(sim_fee_pct=0.0005)


class _Engine:
    initial_balance = 200.0
    db = _DB()
    _pair_registry = _Registry()

    def __init__(self):
        self.alerts = []

    def _active_pair_symbols(self):
        return ["BTCUSDT"]

    def send_telegram_alert(self, message, level=None):
        self.alerts.append((message, level))


def _config():
    return {
        "exchange": {"fee_pct": 0.0005},
        "backtest": {"slippage_bps": 2.0},
        "monthly_report": {
            "enabled": True,
            "day_of_month": 1,
            "hour_utc": 12,
            "send_telegram": True,
            "save_to_file": True,
            "file_path": "monthly/report_{month}.json",
            "parity_tolerance_bps": 10.0,
        },
    }


def test_current_month_self_test_writes_runtime_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUBY_HOME", str(tmp_path))
    monkeypatch.delenv("XAUBY_INSTANCE_ID", raising=False)
    engine = _Engine()
    scheduler = ReportScheduler(_config())

    bundle = scheduler.run_monthly_now(
        engine,
        now=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        current_month=True,
        send=False,
    )

    assert bundle["month"] == "2026-07"
    assert bundle["track_record"]["total_trades"] == 1
    assert bundle["track_record"]["max_drawdown_pct"] == 0.0
    assert bundle["realised_parity"]["status"] == "pass"
    assert bundle["initial_balance"] == 200.0
    path = tmp_path / "monthly" / "report_2026-07.json"
    assert path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["realised_parity"]["slippage_coverage_pct"] == 100.0


def test_scheduled_report_runs_once_for_previous_calendar_month(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUBY_HOME", str(tmp_path))
    monkeypatch.delenv("XAUBY_INSTANCE_ID", raising=False)
    engine = _Engine()
    scheduler = ReportScheduler(_config())
    slot = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)

    scheduler.check_and_run(engine, slot)
    scheduler.check_and_run(engine, slot)

    assert len(engine.alerts) == 1
    assert "2026-07" in engine.alerts[0][0]
    assert "Realised parity: pass" in engine.alerts[0][0]
    assert (tmp_path / "monthly" / "report_2026-07.json").is_file()
