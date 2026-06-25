import unittest
from unittest.mock import patch

from xauby.ui.dashboard import (
    LayoutProfile,
    get_layout_profile,
    _fit_checklist_line,
    _compose_pipe_line,
    _chart_dimensions,
    _chart_header_compact,
    _checklist_budget_lines,
    _multi_phone_focus_line,
    _render_strategy_checklist,
    render_blocker_box,
    _shorten_hold_reason,
    _metric_tier,
    format_decision_gates_compact,
    format_engine_status_badges,
    show_verbose_checklist,
    CHECKLIST_LINE_BUDGET,
)
from xauby.ui.copy import (
    MONITORING_LABEL,
    is_monitoring_empty_state,
    format_monitoring_empty,
    format_tradelog_empty,
)
from xauby.meta import PRODUCT_TITLE, product_title_for_width
from xauby.ui.state_view import (
    format_static_portfolio_lines,
    format_all_positions_lines,
    format_pairs_table_lines,
)
from xauby.utils.common import visible_len
import xauby.ui.dashboard as dashboard
from xauby.ui.widgets import (
    format_regime_compact_lines,
    format_regime_detail_lines,
    format_regime_inline_lines,
    sparkline,
    DEFAULT_REGIME,
    render_track_record_lines,
    render_advanced_analytics_lines,
    get_panel_column_widths,
)


class TestProductMeta(unittest.TestCase):
    def test_product_title(self):
        self.assertEqual(
            PRODUCT_TITLE,
            "xAuby : Alternative Store of Value Trading System",
        )

    def test_title_fits_or_shortens(self):
        full = product_title_for_width(200)
        self.assertIn("Alternative Store of Value", full)
        short = product_title_for_width(30)
        self.assertLessEqual(visible_len(short), 30)


class TestLayoutProfile(unittest.TestCase):
    def test_tablet_80(self):
        p = get_layout_profile(80)
        self.assertEqual(p.tier, "tablet")


class TestChartDimensions(unittest.TestCase):
    def test_phone_default_height_13(self):
        profile = get_layout_profile(60)
        self.assertTrue(profile.is_phone)
        _, h, show_vol = _chart_dimensions(60, profile, content_width=56)
        self.assertEqual(h, 16)
        self.assertFalse(show_vol)

    def test_phone_chart_header_compact(self):
        hdr = _chart_header_compact("4h", "BTCUSDT", 24)
        self.assertIn("4H", hdr)
        self.assertIn("24c", hdr)
        self.assertNotIn("ActionZone", hdr)

    def test_tablet_80col_fills_width(self):
        profile = get_layout_profile(80)
        content_w = 76  # W - 4 border padding
        w, h, show_vol = _chart_dimensions(80, profile, content_width=content_w)
        cols = 1
        try:
            from xauby.ui.chart import get_chart_candle_cols
            cols = get_chart_candle_cols()
        except Exception:
            pass
        self.assertGreaterEqual(w, 30)
        self.assertLessEqual(w, 56)
        est_line = 2 + w * cols + 10
        self.assertLessEqual(est_line, content_w + 2)

    def test_wide_uses_column_width(self):
        profile = get_layout_profile(120)
        _, right_w = get_panel_column_widths(120)
        w, h, _ = _chart_dimensions(120, profile, content_width=right_w)
        cols = 1
        try:
            from xauby.ui.chart import get_chart_candle_cols
            cols = get_chart_candle_cols()
        except Exception:
            pass
        self.assertGreaterEqual(w, 20)
        self.assertLessEqual(2 + w * cols + 10, right_w + 2)


class TestChecklistRich(unittest.TestCase):
    def test_compose_pipe_line_fits_60(self):
        mark = "[✗]"
        line = _compose_pipe_line(
            mark, "Fresh", "EXPIRED (10/3)",
            ["Cross: 10b ago"], 76, label_w=12,
        )
        self.assertLessEqual(visible_len(line), CHECKLIST_LINE_BUDGET)

    def test_fresh_entry_expired_compact(self):
        items = [{
            "label": "Fresh",
            "value": "EXPIRED (10/3)",
            "ok": False,
            "columns": ["Cross: 10b ago"],
        }]
        lines = _render_strategy_checklist(items, get_layout_profile(80), 60)
        self.assertEqual(len(lines), 1)
        self.assertLessEqual(visible_len(lines[0]), CHECKLIST_LINE_BUDGET)
        self.assertIn("EXPIRED (10/3)", lines[0])
        self.assertIn("Cross: 10b ago", lines[0])

    def test_ema_cross_no_cross_clear(self):
        items = [{
            "label": "EMA Cross",
            "value": "No cross",
            "ok": False,
            "columns": ["Need fresh cross"],
        }]
        lines = _render_strategy_checklist(items, get_layout_profile(80), 60)
        self.assertIn("No cross", lines[0])
        self.assertIn("Need fresh cross", lines[0])

    def test_zone_stale_compact(self):
        items = [{
            "label": "4H Zone",
            "value": "GREEN",
            "ok": True,
            "columns": ["Stale (10/3)"],
        }]
        lines = _render_strategy_checklist(items, get_layout_profile(80), 60)
        self.assertIn("Stale (10/3)", lines[0])
        self.assertLessEqual(visible_len(lines[0]), CHECKLIST_LINE_BUDGET)

    def test_blocker_stale_zone_reason(self):
        reason = _shorten_hold_reason(
            "4H zone GREEN but not fresh (green for 10 bars > window 3)"
        )
        self.assertIn("stale", reason.lower())
        self.assertIn("10/3", reason)
        self.assertLessEqual(len(reason), 75)

    def test_blocker_box_fits(self):
        state = {
            "signal_meta": {
                "action": "HOLD",
                "reason": "4H zone GREEN but not fresh (green for 10 bars > window 3)",
            },
            "position": {"state": "idle"},
        }
        items = [
            {"label": "Fresh", "ok": False},
            {"label": "EMA Cross", "ok": False},
        ]
        lines = render_blocker_box(state, items, 76)
        joined = " ".join(lines)
        self.assertIn("stale", joined.lower())
        self.assertNotIn("...", joined)


class TestSparkline(unittest.TestCase):
    def test_output_width(self):
        s = sparkline([1, 2, 3, 4, 5, 6, 7, 8], width=8)
        self.assertEqual(len(s), 8)


class TestRegimeInline(unittest.TestCase):
    def test_two_lines(self):
        reg = dict(DEFAULT_REGIME)
        reg.update({"regime": "RISK-OFF", "confidence": 0.65, "gold_score": 65})
        lines = format_regime_inline_lines(reg, guard_text="G:OK", max_width=76)
        self.assertEqual(len(lines), 2)

    def test_inline_close_risk_suffix(self):
        reg = dict(DEFAULT_REGIME)
        lines = format_regime_inline_lines(
            reg,
            guard_text="OK",
            max_width=76,
            close_text="Cls:01h",
            risk_text="Rsk:1.0%",
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("Cls:01h", lines[1])
        self.assertIn("Rsk:1.0%", lines[1])

    def test_regime_detail_lines_include_strategy_bias(self):
        reg = dict(DEFAULT_REGIME)
        reg.update(
            {
                "phase": "ACCUMULATION",
                "risk_state": "QUIET_RISK",
                "trend_strength": "CHOPPY",
                "volatility_state": "QUIET",
                "transition_risk": "LOW",
                "strategy_bias": {
                    "family": "range_or_accumulation",
                    "posture": "selective",
                    "preferred": ["mean_reversion", "pullback"],
                    "allowed_actions": ["BUY", "HOLD", "SELL"],
                },
            }
        )
        joined = "\n".join(format_regime_detail_lines(reg))
        self.assertIn("Strategy Bias", joined)
        self.assertIn("range or accumulation", joined)
        self.assertIn("BUY/HOLD/SELL", joined)


class TestPhoneMultiShell(unittest.TestCase):
    def _envelope(self):
        return {
            "aggregate": {"total_equity_usdt": 1000, "open_positions": 0},
            "by_symbol": {
                "BTCUSDT": {
                    "current_price": 100.0,
                    "primary_timeframe": "4h",
                    "position": {"state": "idle"},
                    "signal_meta": {"action": "HOLD"},
                    "macro_guard": {"enabled": False},
                    "equity_breakdown": {"usdt_balance_usdt": 500},
                    "portfolio": {"USDT": 500},
                },
                "ETHUSDT": {
                    "current_price": 50.0,
                    "primary_timeframe": "4h",
                    "position": {"state": "idle"},
                    "signal_meta": {"action": "HOLD"},
                    "macro_guard": {"enabled": False},
                },
            },
        }

    def test_phone_portfolio_one_line(self):
        lines = format_static_portfolio_lines(self._envelope(), 70, is_phone=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("Port", lines[0])
        self.assertRegex(lines[0], r"\d,\d{3}B")
        self.assertNotIn("k", lines[0])

    def test_phone_positions_hidden_when_all_idle(self):
        by = self._envelope()["by_symbol"]
        lines = format_all_positions_lines(by, 70, is_phone=True)
        self.assertEqual(lines, [])

    def test_wide_positions_monitoring_when_all_idle(self):
        by = self._envelope()["by_symbol"]
        lines = format_all_positions_lines(by, 100, is_phone=False)
        joined = "\n".join(lines)
        self.assertIn(MONITORING_LABEL, joined)

    def test_phone_positions_shown_when_long(self):
        env = self._envelope()
        env["by_symbol"]["BTCUSDT"]["position"] = {
            "state": "bought",
            "quantity": 1.0,
            "entry_price": 99.0,
            "unrealized_pnl": 1.0,
        }
        lines = format_all_positions_lines(env["by_symbol"], 70, is_phone=True)
        self.assertGreaterEqual(len(lines), 2)

    def test_wide_positions_show_live_mark_sl_and_peak(self):
        env = self._envelope()
        env["by_symbol"]["BTCUSDT"].update(
            {
                "current_price": 101.5,
                "bid": 101.4,
                "ask": 101.6,
                "position": {
                    "state": "bought",
                    "quantity": 1.0,
                    "entry_price": 99.0,
                    "stop_loss": 95.0,
                    "highest_price_seen": 103.0,
                    "unrealized_pnl": 2.5,
                    "unrealized_pnl_pct": 2.53,
                },
            }
        )

        text = "\n".join(format_all_positions_lines(env["by_symbol"], 100, is_phone=False))

        self.assertIn("Mark", text)
        self.assertIn("SL", text)
        self.assertIn("Peak", text)
        self.assertIn("uPnL", text)
        self.assertIn("2.53%", text)

    def test_short_position_uses_short_labels_and_inverse_sl_math(self):
        env = self._envelope()
        env["by_symbol"]["BTCUSDT"].update({
            "current_price": 90.0,
            "position": {
                "state": "bought", "position_side": "SHORT", "leverage": 2,
                "market_type": "SWAP", "quantity": 1.0, "entry_price": 100.0,
                "stop_loss": 105.0, "highest_price_seen": 88.0,
                "unrealized_pnl": 10.0, "unrealized_pnl_pct": 10.0,
            },
        })
        text = "\n".join(format_all_positions_lines(env["by_symbol"], 100, is_phone=False))
        self.assertIn("SHORT 2x SWAP", text)
        self.assertIn("Low", text)
        self.assertNotIn(" LONG ", text)

    def test_phone_pairs_omit_exp_on_non_focus(self):
        lines = format_pairs_table_lines(self._envelope(), "BTCUSDT", 70, is_phone=True)
        eth_line = [ln for ln in lines if "ETHUSDT" in ln][0]
        btc_line = [ln for ln in lines if "BTCUSDT" in ln][0]
        self.assertNotIn("exp:", eth_line)
        self.assertIn("exp:", btc_line)


class TestPhoneBlockerAndBudget(unittest.TestCase):
    def test_blocker_one_line_on_phone(self):
        state = {
            "signal_meta": {"action": "HOLD", "reason": "4H zone not GREEN (current: RED)"},
            "position": {"state": "idle"},
        }
        items = [{"label": "4H Zone", "ok": False}]
        lines = render_blocker_box(state, items, 60, is_phone=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("BLOCKED", lines[0])
        self.assertIn("NEXT", lines[0])

    def test_checklist_budget_phone_blocker_smaller(self):
        state = {
            "signal_meta": {
                "action": "HOLD",
                "reason": "x",
                "checklist": [{"label": f"C{i}", "ok": False} for i in range(6)],
            },
            "position": {"state": "idle"},
        }
        wide = _checklist_budget_lines(state, is_phone=False)
        phone = _checklist_budget_lines(state, is_phone=True)
        self.assertEqual(wide - phone, 3)

    def test_multi_phone_focus_line_fits(self):
        line = _multi_phone_focus_line("FOCUS: BTC · 4H", 100.0, 1.5, "\033[32m", 60)
        self.assertIn("FOCUS", line)
        self.assertLessEqual(visible_len(line), 60)


class TestDecisionGatesCompact(unittest.TestCase):
    def test_short_actions_use_semantic_labels(self):
        base = {"position": {"state": "idle"}, "signal_meta": {"checklist": []}}
        for action, intent, expected in (
            ("SELL", "OPEN", "OPEN SHORT"),
            ("BUY", "CLOSE", "CLOSE SHORT"),
        ):
            state = {
                **base,
                "signal_meta": {
                    "action": action,
                    "intent": intent,
                    "position_side": "SHORT",
                    "checklist": [{"label": "4H Zone", "ok": True}],
                },
            }
            lines = format_decision_gates_compact(state, get_layout_profile(120), 100)
            self.assertIn(expected, "\n".join(lines))

    def test_compact_gate_chips(self):
        state = {
            "symbol": "XAUTUSDT",
            "position": {"state": "idle"},
            "signal_meta": {
                "action": "HOLD",
                "reason": "Conditions not met",
                "checklist": [
                    {"label": "EMA Bullish", "ok": True, "value": "ok"},
                    {"label": "EMA Cross", "ok": False, "value": "no"},
                    {"label": "4H Trend Zone", "ok": True, "value": "GREEN"},
                ],
            },
        }
        lines = format_decision_gates_compact(state, get_layout_profile(120), 100)
        joined = "\n".join(lines)
        self.assertIn("HOLD", joined)
        self.assertIn("2/3", joined)
        self.assertIn("✓", joined)
        self.assertIn("✗", joined)
        self.assertIn("EMA", joined)

    def test_monitoring_when_idle_no_reason(self):
        state = {
            "symbol": "XAUTUSDT",
            "position": {"state": "idle"},
            "signal_meta": {"action": "HOLD", "checklist": []},
        }
        lines = format_decision_gates_compact(state, get_layout_profile(80), 76)
        self.assertIn(MONITORING_LABEL, "\n".join(lines))


class TestEngineStatusBadges(unittest.TestCase):
    def test_metric_tiers(self):
        self.assertEqual(_metric_tier(10, 50, 80), "ok")
        self.assertEqual(_metric_tier(60, 50, 80), "warn")
        self.assertEqual(_metric_tier(90, 50, 80), "crit")

    def test_badge_fits_phone_width(self):
        s = format_engine_status_badges(42.0, 58.0, 24.1, compact=True)
        self.assertLessEqual(visible_len(s), 48)
        self.assertIn("C", s)
        self.assertIn("R", s)
        self.assertIn("D", s)

    def test_wide_badges_include_labels(self):
        s = format_engine_status_badges(12.0, 58.0, 24.1, compact=False)
        self.assertIn("CPU", s)
        self.assertIn("RAM", s)
        self.assertIn("DB", s)


class TestMonitoringCopy(unittest.TestCase):
    def test_is_monitoring_empty(self):
        state = {"symbol": "X", "position": {"state": "idle"}}
        self.assertTrue(is_monitoring_empty_state(state))
        state["position"] = {"state": "bought"}
        self.assertFalse(is_monitoring_empty_state(state))

    def test_monitoring_copy_not_negative(self):
        text = format_monitoring_empty()
        self.assertIn(MONITORING_LABEL, text)
        self.assertNotIn("No active", text)
        self.assertNotIn("no trading", text.lower())

    def test_tradelog_empty_tone(self):
        msg = format_tradelog_empty()
        self.assertIn(MONITORING_LABEL, msg)
        self.assertNotIn("No trades", msg)

    def test_show_verbose_checklist_width(self):
        self.assertFalse(show_verbose_checklist(70))
        self.assertTrue(show_verbose_checklist(120))


class TestNewTUIFeatures(unittest.TestCase):
    def test_get_panel_column_widths_wide(self):
        # W = 150: right_w = 80, left_w = 150 - 7 - 80 = 63
        left_w, right_w = get_panel_column_widths(150)
        self.assertEqual(right_w, 80)
        self.assertEqual(left_w, 63)

        # W = 120: W - 7 = 113. right_w = 80, left_w = 33 < 45 => left_w = 45, right_w = 68
        left_w, right_w = get_panel_column_widths(120)
        self.assertEqual(left_w, 45)
        self.assertEqual(right_w, 68)

    def test_print_row_with_bg_style(self):
        import sys
        from io import StringIO
        from xauby.ui.widgets import print_row

        saved_stdout = sys.stdout
        try:
            out = StringIO()
            sys.stdout = out
            print_row("hello", 20, border_color="|", bg_style="[BG]")
            output = out.getvalue()
            # 20 width - 4 border/padding = 16 content width
            # "hello" has 5 chars, so 11 spaces padding.
            self.assertIn("[BG]hello", output)
            self.assertIn("|│\033[0m", output)
        finally:
            sys.stdout = saved_stdout


if __name__ == "__main__":
    unittest.main()
