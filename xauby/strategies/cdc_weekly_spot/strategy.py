"""CDC V3 basic green/red rules, separate from the production 4H profile.

Reuses the existing AP=EMA(close, 2), EMA12/26 zone calculations. This is the
author's basic colour method, not a reproduction of every TradingView alert,
momentum overlay, or first-entry label. Paper maturity is intentional.
"""

from dataclasses import replace

from xauby.strategies.cdc_action_zone.strategy import CDCActionZoneStrategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, hold


@register("cdc_weekly_spot")
class CDCWeeklySpotStrategy(CDCActionZoneStrategy):
    display_name = "CDC V3 Weekly Spot (paper)"
    description = "Weekly closed GREEN entry / RED exit; spot long-only."
    maturity = "paper"
    tags = ["trend", "swing", "1w", "spot", "paper-test"]
    required_timeframes = ["1w"]

    @classmethod
    def default_config(cls) -> dict:
        return {
            **super().default_config(),
            "primary_timeframe": "1w",
            "confirm_timeframe": "",
            "require_fresh_zone": False,
            # Wider than the mathematical RSI range so floating-point output
            # such as 100.00000000000001 cannot turn this disabled filter on.
            "rsi_min": -1.0,
            "rsi_max": 101.0,
            "vol_min_ratio": 0.0,
            "disable_stop_loss": True,
            "sl_atr_mult": 0.0,
            "trailing_atr_mult": 0.0,
            "fixed_tp_pct": 0.0,
        }

    def analyze(self, ctx: MarketContext) -> Signal:
        if ctx.timeframe_primary != "1w":
            return hold("Weekly CDC requires 1w candles", strategy_name=self.name)
        # The engine/report must trim by timestamp before handing data to us.
        # Refuse ambiguous input instead of trading a still-forming weekly bar.
        if ctx.extras.get("last_bar_is_forming") is not False:
            return hold("Weekly CDC requires confirmed closed candles", strategy_name=self.name)
        if ctx.df_primary is None or len(ctx.df_primary) < self.min_bars:
            return hold("Weekly CDC needs 100 closed weekly candles", strategy_name=self.name)
        if str(ctx.position_side or "LONG").upper() != "LONG":
            return hold("Weekly CDC supports spot LONG positions only", strategy_name=self.name)
        # Keep this profile's colour-only rules stable even if an old tenant
        # config carries filters or SHORT settings over from a 4H strategy.
        rules = {
            **self.default_config(),
            "use_d1_regime_filter_long": False,
            "use_d1_regime_filter_short": False,
        }
        signal = super().analyze(replace(ctx, config=rules))
        signal.reason = signal.reason.replace("4H", "1W")
        signal.status_summary = (
            (signal.status_summary or "")
            .replace("D1: OFF | ", "")
            .replace("4H", "1W")
        )
        signal.timeframe = "1w"
        for item in signal.checklist or []:
            item["label"] = item.get("label", "").replace("4H", "1W")
        return signal
