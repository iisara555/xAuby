#!/usr/bin/env python3
"""Send sample messages for every Telegram alert type (connectivity test)."""
import os
import sys
import time

script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(script_dir)
sys.path.insert(0, script_dir)

from dotenv import load_dotenv

load_dotenv()

from xauby.notifications.interface import AlertLevel
from xauby.notifications.report_formatter import (
    build_daily_digest_message,
    build_period_report_message,
)
from xauby.notifications.telegram import TelegramNotificationService
from xauby.notifications.telegram_bot import SEMI_AUTO_KEYBOARD

SYMBOL = os.environ.get("DEFAULT_SYMBOL", "XAUTUSDT").upper()


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

    svc = TelegramNotificationService(token=token, chat_id=chat_id, enabled=True)
    regime = {"regime": "RISK-OFF", "gold_score": 65, "trend": "NEUTRAL", "macro_bias": "RISK-OFF", "confidence": 0.65}

    samples = [
        ("lifecycle_started", AlertLevel.TRADE, f"🤖 *Lite Bot Started*\nPair: `{SYMBOL}`\nMode: `LIVE`"),
        ("lifecycle_stopped", AlertLevel.INFO, f"🛑 *Engine Stopped*\nSymbol: `{SYMBOL}`\nMode: `LIVE`"),
        ("ws_disconnected", AlertLevel.TRADE, f"⚠️ *WebSocket Disconnected*\nSymbol: `{SYMBOL}`\nReason: `test`"),
        ("ws_reconnected", AlertLevel.TRADE, f"✅ *WebSocket Reconnected*\nSymbol: `{SYMBOL}`\nDowntime: `45s`"),
        ("reconciliation", AlertLevel.TRADE, f"🔄 *Startup Reconciliation*\nSymbol: `{SYMBOL}`\nStatus: `test sync`"),
        ("protection_block", AlertLevel.TRADE, "⚠️ *Trading Blocked by Protection*\nReason: test daily limit"),
        ("guard_block", AlertLevel.INFO, f"🚫 *Macro Guard Blocked BUY*\nScore: `-0.620` │ Threshold: `-0.5`\nSymbol: `{SYMBOL}`"),
        ("paper_buy", AlertLevel.TRADE, f"🟢 [PAPER BUY] Filled 0.010000 XAUT @ 4514.00 USDT (Risk: 0.98%, SL: 4480.00)"),
        ("live_buy", AlertLevel.TRADE, f"🚀 [LIVE BUY FILLED] 0.010000 XAUT @ 4514.00 USDT. SL set to 4480.00"),
        ("live_sell", AlertLevel.TRADE, f"🎉 [LIVE SELL FILLED] Exit 0.010000 XAUT @ 4530.00 | PnL: +0.16 USDT (+0.35%)"),
        ("exchange_sl_hit", AlertLevel.TRADE, f"🔴 [EXCHANGE STOP LOSS HIT] Exit 0.010000 @ 4480.00 | PnL: -0.34 USDT"),
        ("trailing_stop", AlertLevel.POSITION, f"↗️ *Trailing Stop Updated*\nSymbol: `{SYMBOL}`\nNew SL: `4500.00 USDT`\nPeak: `4535.00 USDT`"),
        ("trailing_failed", AlertLevel.POSITION, "⚠️ *Trailing Stop Failed*: Could not update exchange SL order."),
        ("sl_restore_failed", AlertLevel.POSITION, "⚠️ *Restore Stop Loss Failed*: Position relies on local monitoring until SL is restored."),
        ("sl_critical", AlertLevel.CRITICAL, "🚨 *CRITICAL*: Failed to restore exchange SL. Position relies on local monitoring only."),
        ("api_error", AlertLevel.TRADE, "❌ LIVE BUY API Error: test error (code=-2010)"),
        ("critical_loop", AlertLevel.CRITICAL, f"🚨 *CRITICAL ENGINE EXCEPTION*\nSymbol: `{SYMBOL}`\nError: `test exception`\nLoop will retry in 60s."),
        ("heartbeat", AlertLevel.INFO, (
            f"💚 *xAuby Heartbeat*\n"
            f"• Symbol: `{SYMBOL}`\n"
            f"• Price: `4514.23 USDT`\n"
            f"• Equity: `42.00 USDT`\n"
            f"• Position: `IDLE`\n"
            f"• Status: `Running normally`\n"
            f"• Regime: `RISK-OFF` │ Gold: `65/100`"
        )),
        ("regime_change", AlertLevel.INFO, (
            f"🔄 *Regime Update* — `{SYMBOL}`\n"
            f"`RISK-ON` (72/100) → `RISK-OFF` (65/100)\n"
            f"  ✓ DXY Weak\n  ✓ Inflation Supportive\n  ✗ Trend Neutral"
        )),
        ("weekly_review", AlertLevel.INFO, build_period_report_message([], 7, "Weekly Review (TEST)", regime)),
        ("daily_digest", AlertLevel.INFO, build_daily_digest_message([], SYMBOL, 42.0, "IDLE", regime)),
        ("semi_auto", AlertLevel.TRADE, (
            f"🟡 *Semi-auto BUY Confirmation*\n"
            f"Symbol: `{SYMBOL}` │ Price: `4514.00 USDT`\n"
            f"Confirm within `60s` or the signal will be skipped."
        )),
    ]

    ok = 0
    fail = 0
    print(f"Sending {len(samples)} test notifications to chat {chat_id}...\n")

    for idx, (name, level, text) in enumerate(samples, 1):
        reply_markup = SEMI_AUTO_KEYBOARD if name == "semi_auto" else None
        label = f"[{idx}/{len(samples)}] {name} ({level.value})"
        if svc.send_immediate(text, reply_markup=reply_markup):
            print(f"  ✓ {label}")
            ok += 1
        else:
            print(f"  ✗ {label} — see core/logs/telegram_failures.log")
            fail += 1
        time.sleep(0.4)

    svc.stop()
    print(f"\nDone: {ok} sent, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
