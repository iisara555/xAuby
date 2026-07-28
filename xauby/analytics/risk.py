"""One definition of Sharpe, Sortino and drawdown, for live and backtest alike.

Roadmap P1.5. Until now the dashboard and the backtest each computed a number
called "Sharpe" and they were not the same number:

===================  ==================================  =========================
                     live (analytics/calculator.py)      backtest (backtest/metrics.py)
===================  ==================================  =========================
sampling unit        one observation per closed trade    one per bar, mark-to-market
annualised           no                                  yes, x sqrt(periods/year)
return basis         ``net_pnl_pct`` — return on the     equity-relative
                     trade's own notional
downside deviation   squares every return, clipping      standard deviation of the
                     positives to zero, and divides      negative returns only
                     by the count of ALL returns
drawdown             equity at trade closes only         per-bar, so intra-trade
                                                         excursion is included
===================  ==================================  =========================

Every row is a different estimator. A dashboard Sharpe of 0.4 and a backtest
Sharpe of 0.4 said nothing about each other, and no test asserted they should.

The return-basis row is the one that silently distorts: ``net_pnl_pct`` is the
return on the position, not on the account. Compounding a list of them treats
every trade as if it had been the whole balance. Measured on the BTC preset
while wiring up P1.3, that turned a real 9.8% max drawdown into 35.4%.

The drawdown row cannot be fully repaired from closed trades — the excursion
between entry and exit is simply not in the data — so instead of quietly
reporting the smaller number under the same name, every result carries a
:class:`MetricBasis` saying what was measured. That matters most for pairs
running ``disable_stop_loss: true``, where the bot is the stop and the
intra-trade excursion is precisely the risk.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

#: Trading periods per year for common bar sizes, used to annualise.
BARS_PER_YEAR = {
    "1m": 525_600.0, "5m": 105_120.0, "15m": 35_040.0, "30m": 17_520.0,
    "1h": 8_760.0, "2h": 4_380.0, "4h": 2_190.0, "6h": 1_460.0,
    "12h": 730.0, "1d": 365.0, "1w": 52.0,
}


@dataclass(frozen=True)
class MetricBasis:
    """What a risk number was actually computed from.

    Carried alongside the number so two of them are never compared without the
    reader seeing that one is per-trade and the other per-bar.
    """

    sampling: str          # "per-trade" | "per-bar"
    annualised: bool
    return_basis: str      # "equity-relative" | "trade-notional"
    drawdown_source: str   # "trade-close" | "mark-to-market"
    observations: int = 0
    periods_per_year: float = 0.0

    def describe(self) -> str:
        unit = "annualised" if self.annualised else "not annualised"
        return (f"{self.sampling}, {unit}, {self.return_basis} returns; "
                f"drawdown from {self.drawdown_source} "
                f"({self.observations} observations)")

    def comparable_to(self, other: "MetricBasis") -> bool:
        """Whether two results measure the same thing well enough to compare."""
        return (self.sampling == other.sampling
                and self.annualised == other.annualised
                and self.return_basis == other.return_basis)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sampling": self.sampling,
            "annualised": self.annualised,
            "return_basis": self.return_basis,
            "drawdown_source": self.drawdown_source,
            "observations": self.observations,
            "periods_per_year": self.periods_per_year,
            "description": self.describe(),
        }


def equity_relative_returns(
    pnls: Sequence[float], initial_balance: float
) -> List[float]:
    """Each PnL as a percent of the equity that existed before it.

    The series every other function here expects. Feeding trade-notional
    returns instead is not a rounding difference — it rescales the whole
    distribution by the inverse of position sizing.
    """
    balance = float(initial_balance)
    if balance <= 0:
        return []
    out: List[float] = []
    for pnl in pnls:
        if balance <= 0:
            break
        value = float(pnl)
        out.append(value / balance * 100.0)
        balance += value
    return out


def sharpe(
    returns: Sequence[float],
    *,
    periods_per_year: float = 0.0,
    risk_free_per_period: float = 0.0,
) -> float:
    """Mean over standard deviation, annualised when the periodicity is known.

    ``periods_per_year`` of 0 means "unknown", and the result is then a raw
    per-period ratio. It is not silently annualised with a guess: an assumed
    periodicity is a multiplier of up to 90x on a number people size positions
    against.
    """
    values = [float(v) for v in returns]
    if len(values) < 2:
        return 0.0
    excess = [v - float(risk_free_per_period) for v in values]
    sd = statistics.pstdev(excess)
    if sd == 0:
        return 0.0
    ratio = statistics.fmean(excess) / sd
    if periods_per_year > 0:
        ratio *= math.sqrt(periods_per_year)
    return ratio


def sortino(
    returns: Sequence[float],
    *,
    periods_per_year: float = 0.0,
    target: float = 0.0,
) -> float:
    """Mean excess over downside deviation.

    Downside deviation is the standard deviation of the *shortfalls below
    target*, taken over the observations that fall short — not over all of them
    with the positives clipped to zero. The clipped version divides by the full
    count, so adding profitable trades shrinks the denominator's average and
    inflates the ratio; that was the live implementation.
    """
    values = [float(v) for v in returns]
    if len(values) < 2:
        return 0.0
    shortfalls = [float(target) - v for v in values if v < target]
    if not shortfalls:
        return 0.0
    downside = math.sqrt(sum(s * s for s in shortfalls) / len(shortfalls))
    if downside == 0:
        return 0.0
    ratio = (statistics.fmean(values) - float(target)) / downside
    if periods_per_year > 0:
        ratio *= math.sqrt(periods_per_year)
    return ratio


def max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    """Worst peak-to-trough decline of an equity curve, in percent."""
    peak = 0.0
    worst = 0.0
    for value in equity_curve:
        equity = float(value)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return worst


def equity_curve_from_pnls(
    pnls: Sequence[float], initial_balance: float
) -> List[float]:
    """Balance after each PnL, starting at ``initial_balance``."""
    balance = float(initial_balance)
    curve = [balance]
    for pnl in pnls:
        balance += float(pnl)
        curve.append(balance)
    return curve


def periods_per_year_from_span(observations: int, years: float) -> float:
    """Observations per year, for annualising a per-trade series.

    Returns 0.0 when the span is unusable, so :func:`sharpe` falls back to the
    raw ratio rather than annualising by a number derived from nothing.
    """
    if observations < 2 or years <= 0:
        return 0.0
    return observations / years


def bars_per_year(timeframe: str) -> float:
    """Periods per year for a timeframe string, 0.0 when unrecognised."""
    return BARS_PER_YEAR.get(str(timeframe).lower(), 0.0)


@dataclass
class RiskResult:
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    basis: MetricBasis

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "basis": self.basis.to_dict(),
        }


def risk_from_trade_pnls(
    pnls: Sequence[float],
    initial_balance: float,
    *,
    years: Optional[float] = None,
) -> RiskResult:
    """Risk metrics from closed trades — the live/dashboard path.

    Annualised only when ``years`` is supplied, because the number of trades
    per year is the only honest way to convert a per-trade ratio and it is not
    recoverable from the PnLs alone. The basis records ``drawdown_source =
    "trade-close"``: whatever happened between entry and exit is not in this
    data, and on a pair running without an exchange stop that gap is the risk.
    """
    values = [float(p) for p in pnls]
    returns = equity_relative_returns(values, initial_balance)
    ppy = periods_per_year_from_span(len(returns), years or 0.0)
    return RiskResult(
        sharpe=sharpe(returns, periods_per_year=ppy),
        sortino=sortino(returns, periods_per_year=ppy),
        max_drawdown_pct=max_drawdown_pct(
            equity_curve_from_pnls(values, initial_balance)),
        basis=MetricBasis(
            sampling="per-trade",
            annualised=ppy > 0,
            return_basis="equity-relative",
            drawdown_source="trade-close",
            observations=len(returns),
            periods_per_year=ppy,
        ),
    )


def risk_from_equity_curve(
    equity_curve: Sequence[float],
    *,
    periods_per_year: float = 0.0,
) -> RiskResult:
    """Risk metrics from a per-bar equity curve — the backtest path.

    Same estimators as :func:`risk_from_trade_pnls`, different sampling. The
    basis is what tells the two apart.
    """
    curve = [float(e) for e in equity_curve if e is not None]
    returns = [
        (cur - prev) / prev * 100.0
        for prev, cur in zip(curve, curve[1:]) if prev > 0
    ]
    return RiskResult(
        sharpe=sharpe(returns, periods_per_year=periods_per_year),
        sortino=sortino(returns, periods_per_year=periods_per_year),
        max_drawdown_pct=max_drawdown_pct(curve),
        basis=MetricBasis(
            sampling="per-bar",
            annualised=periods_per_year > 0,
            return_basis="equity-relative",
            drawdown_source="mark-to-market",
            observations=len(returns),
            periods_per_year=periods_per_year,
        ),
    )
