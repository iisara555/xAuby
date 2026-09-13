"""GPL-3.0 research derivatives of pinned freqtrade/freqtrade-strategies.

The hosted harness explicitly patches BOTH upstream resampling intervals to
60 minutes before these subclasses load. Never import into xAuby live code.
"""

from datetime import datetime, timedelta

import pandas as pd
from FReinforcedStrategy import FReinforcedStrategy
from VolatilitySystem import VolatilitySystem


def in_research_session(clock: pd.Series) -> pd.Series:
    """Known calendar filter at intended entry time; DST is not a price feature."""
    london = clock.dt.tz_convert("Europe/London")
    new_york = clock.dt.tz_convert("America/New_York")
    return ((london.dt.dayofweek < 5) & london.dt.hour.between(8, 11)) | (
        (new_york.dt.dayofweek < 5) & new_york.dt.hour.between(8, 11)
    )


class ResearchRiskMixin:
    stoploss = -0.02
    startup_candle_count = 999
    position_adjustment_enable = False

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag=None, side="long", **kwargs):
        return 1.0

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        amount = min(proposed_stake, max_stake, self.wallets.get_total_stake_amount() * 0.25)
        return 0.0 if min_stake is not None and amount < min_stake else amount

    def custom_exit(self, pair, trade, current_time: datetime, current_rate,
                    current_profit, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=48):
            return "research_timeout_48h"
        return None


class Community15mVolatilityRisk(ResearchRiskMixin, VolatilitySystem):
    """15m execution, explicitly patched 1h ATR/close-change context."""

    timeframe = "15m"
    startup_candle_count = 499


class Community15mReinforcedRisk(ResearchRiskMixin, FReinforcedStrategy):
    """15m EMAs/ADX and fixed 1h SMA50; native ROI minute schedule retained."""

    timeframe = "15m"


class Community15mVolatilitySession(Community15mVolatilityRisk):
    """Same XAU rules with a frozen calendar-only entry restriction."""

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        allowed = in_research_session(dataframe["date"] + pd.Timedelta(minutes=15))
        dataframe.loc[~allowed, ["enter_long", "enter_short"]] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        # Upstream derives exits from entries: restore raw entries ONLY on a
        # copy so the calendar never suppresses an exit or re-enables entries.
        raw = dataframe.copy()
        raw[["enter_long", "enter_short", "exit_long", "exit_short"]] = 0
        raw = VolatilitySystem.populate_entry_trend(self, raw, metadata)
        exits = VolatilitySystem.populate_exit_trend(self, raw, metadata)
        dataframe[["exit_long", "exit_short"]] = exits[["exit_long", "exit_short"]]
        return dataframe
