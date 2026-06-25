#!/usr/bin/env python3
"""Send multi-pair Telegram message samples (connectivity + layout test)."""
import os
import sys
import time

script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(script_dir)
sys.path.insert(0, script_dir)

from dotenv import load_dotenv

load_dotenv()

from xauby.notifications.multi_pair_format import (
    build_multi_daily_digest,
    format_multi_heartbeat,
    format_multi_last_trades,
    format_multi_pnl,
    format_multi_regime,
)
from xauby.notifications.telegram import TelegramNotificationService

SYMBOLS = ["XAUTUSDT", "BTCUSDT"]


def main() -> int:
    enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not enabled:
        print("[ERR] TELEGRAM_ENABLED is not true in .env")
        return 1
    if not token or not chat_id:
        print("[ERR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        return 1

    regime_xaut = {
        "regime": "RISK-OFF",
        "gold_score": 65,
        "trend": "NEUTRAL",
        "volatility": "NORMAL",
        "macro_bias": "RISK-OFF",
        "reasons": [{"supportive": True, "label": "DXY Weak"}],
    }
    regime_btc = {
        "regime": "TRENDING BEARISH",
        "gold_score": 40,
        "trend": "BEARISH",
        "volatility": "NORMAL",
        "macro_bias": "RISK-OFF",
        "reasons": [{"supportive": False, "label": "Trend Bearish"}],
    }
    regimes = {"XAUTUSDT": regime_xaut, "BTCUSDT": regime_btc}

    samples = [
        ("multi_regime", format_multi_regime(SYMBOLS, lambda s: regimes.get(s))),
        (
            "multi_status_style",
            "📡 *xAuby Status* — `2` active pair(s)\n"
            "Equity: `85.00 USDT` │ Mode: `LIVE`\n"
            "• `XAUTUSDT` 4h │ `4496.84` │ IDLE │ HOLD\n"
            "• `BTCUSDT` 4h │ `72650.16` │ IDLE │ HOLD",
        ),
        ("multi_pnl", format_multi_pnl(SYMBOLS, lambda _s, _n: [])),
        (
            "multi_last",
            format_multi_last_trades(
                SYMBOLS,
                lambda sym, n: (
                    [
                        {
                            "closed_at": "2026-06-01T10:00:00",
                            "trigger": "trailing",
                            "net_pnl": 1.2,
                        }
                    ]
                    if sym == "XAUTUSDT"
                    else []
                ),
            ),
        ),
        (
            "multi_heartbeat",
            format_multi_heartbeat(
                SYMBOLS,
                85.0,
                lambda s: 4496.84 if s == "XAUTUSDT" else 72650.16,
                lambda _s: "IDLE",
                lambda s: regimes.get(s),
            ),
        ),
        (
            "multi_daily_digest",
            build_multi_daily_digest(
                SYMBOLS,
                85.0,
                lambda _s, _n: [],
                lambda _s: "IDLE",
                lambda s: regimes.get(s),
            ),
        ),
        (
            "lifecycle_started_multi",
            "🤖 *Lite Bot Started* (TEST)\n"
            "Pairs: `XAUTUSDT, BTCUSDT`\n"
            "Focus: `XAUTUSDT`\n"
            "Mode: `LIVE`",
        ),
    ]

    svc = TelegramNotificationService(token=token, chat_id=chat_id, enabled=True)
    ok = fail = 0
    print(f"Sending {len(samples)} multi-pair test messages to chat {chat_id}...\n")

    for idx, (name, text) in enumerate(samples, 1):
        label = f"[{idx}/{len(samples)}] {name}"
        if svc.send_immediate(text):
            print(f"  ✓ {label}")
            ok += 1
        else:
            print(f"  ✗ {label}")
            fail += 1
        time.sleep(0.5)

    svc.stop()
    print(f"\nDone: {ok} sent, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
