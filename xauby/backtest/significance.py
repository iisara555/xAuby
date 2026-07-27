"""Statistical significance for backtest results. Roadmap P1.3.

The repo searches 17 strategies across parameter grids — the actionzone sweep
alone scored 144 configurations — and then reports the winner's profit factor as
if it were the outcome of a single experiment. It is not. The best of 144 draws
from a distribution of noise has a high profit factor *by construction*, and
nothing here corrected for that.

This module supplies the four tests that separate a result from an artefact of
searching:

* :func:`bootstrap_returns` — resample the return series to put an interval
  around the compounded result. Already in use for XAU; centralised here so it
  is one implementation with one seed convention.
* :func:`shuffle_pvalue` — permute the *order* of trades. Path statistics like
  max drawdown depend on sequence; if the observed drawdown is unremarkable
  among random orderings of the same trades, the equity curve's shape was luck.
* :func:`probabilistic_sharpe_ratio` / :func:`deflated_sharpe_ratio` — Bailey &
  López de Prado. PSR asks whether a Sharpe beats a benchmark given the sample's
  own skew and fat tails; DSR asks it again after subtracting the Sharpe you
  would expect from the *best* of N trials. DSR is the one that speaks to
  selection bias, and it needs to know how many configurations were tried.
* :func:`benjamini_hochberg` / :func:`bonferroni` — when several presets or
  variants are tested at once, control the family-wise or false-discovery rate
  instead of reading each p-value alone.

Two deliberate refusals, because a silently wrong answer here is worse than no
answer:

* :func:`shuffle_pvalue` rejects an order-invariant statistic. Compounded return
  and total PnL do not change when you reorder trades — multiplication and
  addition commute — so a shuffle test on them always returns p ≈ 1 and reads
  as "not significant" when in fact nothing was tested.
* :func:`deflated_sharpe_ratio` requires the trial count and the spread of
  Sharpes across trials. Passing ``n_trials=1`` for a result that was picked out
  of a sweep does not deflate anything; the function cannot detect that, so the
  caller must supply it and certificates record it.

Standard library plus math only — no scipy — so this stays importable anywhere
the engine is.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence

#: Euler–Mascheroni constant, used by the expected maximum of N Sharpe draws.
_EULER_MASCHERONI = 0.5772156649015329

DEFAULT_SAMPLES = 10000
DEFAULT_SEED = 20260727


class OrderInvariantStatistic(ValueError):
    """A shuffle test was asked for on a statistic that reordering cannot move."""


def normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * math.erfc(-float(x) / math.sqrt(2.0))


def normal_ppf(p: float) -> float:
    """Standard normal quantile (Acklam's approximation, refined by Halley).

    Accurate to roughly 1e-15 after the refinement step, which matters because
    :func:`expected_max_sharpe` evaluates it at 1 - 1/N for large N, where the
    raw approximation's tail error would be the dominant term.
    """
    p = float(p)
    if not 0.0 < p < 1.0:
        raise ValueError("normal_ppf requires 0 < p < 1")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
             ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    # One Halley step against the true CDF.
    err = normal_cdf(x) - p
    pdf = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    if pdf > 0:
        u = err / pdf
        x -= u / (1.0 + 0.5 * x * u)
    return x


# --------------------------------------------------------------------------- #
# Path statistics                                                             #
# --------------------------------------------------------------------------- #
def compound(returns: Sequence[float]) -> float:
    """Compounded return in percent from a sequence of percent returns."""
    total = 1.0
    for value in returns:
        total *= 1.0 + float(value) / 100.0
    return (total - 1.0) * 100.0


def max_drawdown(returns: Sequence[float]) -> float:
    """Worst peak-to-trough decline of the compounded path, in percent.

    Order dependent by construction — which is exactly why it is the statistic
    a shuffle test can say something about.
    """
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + float(value) / 100.0
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return worst


def longest_losing_streak(returns: Sequence[float]) -> int:
    streak = worst = 0
    for value in returns:
        if float(value) < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


# --------------------------------------------------------------------------- #
# Bootstrap                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class BootstrapResult:
    samples: int
    observations: int
    median_pct: float
    p05_pct: float
    p95_pct: float
    prob_profitable: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "samples": self.samples,
            "observations": self.observations,
            "median_pct": self.median_pct,
            "p05_pct": self.p05_pct,
            "p95_pct": self.p95_pct,
            "prob_profitable": self.prob_profitable,
            "note": self.note,
        }


def bootstrap_returns(
    returns: Sequence[float],
    *,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    block: int = 1,
    min_observations: int = 5,
) -> BootstrapResult:
    """Resample the return series; interval around the compounded result.

    ``block`` > 1 draws contiguous runs instead of single observations, which
    keeps some autocorrelation intact. The i.i.d. default deliberately does not:
    read it as sampling variability *within this regime mix*, never as a
    probability of future safety. A four-year gold bull resampled i.i.d. yields
    another four-year gold bull every time.
    """
    values = [float(value) for value in returns]
    if len(values) < min_observations:
        return BootstrapResult(0, len(values), 0.0, 0.0, 0.0, 0.0,
                               note=f"fewer than {min_observations} observations")
    if block < 1:
        raise ValueError("block must be >= 1")
    rng = random.Random(seed)
    n = len(values)
    totals: List[float] = []
    for _ in range(samples):
        drawn: List[float] = []
        while len(drawn) < n:
            start = rng.randrange(n)
            drawn.extend(values[start:start + block] if block > 1 else [values[start]])
        totals.append(compound(drawn[:n]))
    totals.sort()
    return BootstrapResult(
        samples=samples,
        observations=n,
        median_pct=round(statistics.median(totals), 2),
        p05_pct=round(totals[int(0.05 * samples)], 2),
        p95_pct=round(totals[int(0.95 * samples)], 2),
        prob_profitable=round(sum(1 for t in totals if t > 0) / samples, 4),
        note="i.i.d." if block == 1 else f"block bootstrap, block={block}",
    )


# --------------------------------------------------------------------------- #
# Shuffle / permutation                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class ShuffleResult:
    samples: int
    observed: float
    p_value: float
    mean_shuffled: float
    better_or_equal: int
    lower_is_better: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "samples": self.samples,
            "observed": round(self.observed, 4),
            "p_value": round(self.p_value, 4),
            "mean_shuffled": round(self.mean_shuffled, 4),
            "better_or_equal": self.better_or_equal,
            "lower_is_better": self.lower_is_better,
        }


def shuffle_pvalue(
    returns: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = max_drawdown,
    *,
    samples: int = 2000,
    seed: int = DEFAULT_SEED,
    lower_is_better: bool = True,
) -> ShuffleResult:
    """How unusual is the observed path among random reorderings of the same trades?

    The trades are held fixed and only their sequence is permuted, so the test
    isolates path shape. A small p-value on ``max_drawdown`` means the realised
    drawdown was genuinely mild for this set of trades; a large one means the
    curve's smoothness was an accident of ordering and should not be sold as
    risk control.

    Raises :class:`OrderInvariantStatistic` when ``statistic`` cannot move under
    permutation — compounded return and total PnL are the common traps, and a
    test that always answers "not significant" is worse than no test.
    """
    values = [float(value) for value in returns]
    if len(values) < 3:
        raise ValueError("need at least 3 observations to permute")
    rng = random.Random(seed)
    observed = float(statistic(values))

    drawn: List[float] = []
    better_or_equal = 0
    working = list(values)
    for _ in range(samples):
        rng.shuffle(working)
        result = float(statistic(working))
        drawn.append(result)
        if (result <= observed) if lower_is_better else (result >= observed):
            better_or_equal += 1

    # Checked against the whole run rather than a short probe: a probe can miss
    # variation by chance on a small sample and raise on a perfectly good test.
    if all(math.isclose(value, observed, rel_tol=1e-12, abs_tol=1e-12)
           for value in drawn):
        raise OrderInvariantStatistic(
            f"{getattr(statistic, '__name__', statistic)!r} took the same value "
            f"in all {samples} permutations, so reordering tested nothing and "
            "the p-value would be a meaningless 1.0. Either the statistic is "
            "order-invariant (compounded return and total PnL are — "
            "multiplication and addition commute), or this particular sample "
            "cannot move it: a series with a single losing trade has the same "
            "max drawdown in every order."
        )
    # +1 in numerator and denominator: the observed ordering is itself one of
    # the permutations, so a p-value of exactly zero is not attainable.
    p_value = (better_or_equal + 1) / (samples + 1)
    return ShuffleResult(
        samples=samples,
        observed=observed,
        p_value=p_value,
        mean_shuffled=statistics.fmean(drawn),
        better_or_equal=better_or_equal,
        lower_is_better=lower_is_better,
    )


# --------------------------------------------------------------------------- #
# Sharpe under selection                                                      #
# --------------------------------------------------------------------------- #
def sample_skew(returns: Sequence[float]) -> float:
    values = [float(v) for v in returns]
    n = len(values)
    if n < 3:
        return 0.0
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return 0.0
    return sum(((v - mean) / sd) ** 3 for v in values) / n


def sample_kurtosis(returns: Sequence[float]) -> float:
    """Non-excess kurtosis (normal = 3.0), which is what the PSR formula wants."""
    values = [float(v) for v in returns]
    n = len(values)
    if n < 4:
        return 3.0
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return 3.0
    return sum(((v - mean) / sd) ** 4 for v in values) / n


def probabilistic_sharpe_ratio(
    sharpe: float,
    observations: int,
    *,
    benchmark: float = 0.0,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """P(true Sharpe > ``benchmark``), adjusting for skew and fat tails.

    ``sharpe`` and ``benchmark`` must share the periodicity of ``observations``
    — feed a per-period Sharpe with the number of periods, not an annualised
    Sharpe with a count of years. Mixing them is the usual way this number comes
    out flattering.
    """
    n = int(observations)
    if n < 2:
        return 0.0
    denominator = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if denominator <= 0:
        # Extreme skew/kurtosis relative to the Sharpe: the Gaussian expansion
        # underlying PSR has broken down, and returning a confident number here
        # would be the least honest option.
        return float("nan")
    return normal_cdf((sharpe - benchmark) * math.sqrt(n - 1) / math.sqrt(denominator))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """E[max Sharpe] over ``n_trials`` independent strategies with zero true skill.

    This is the bar a searched result has to clear before it means anything: try
    enough configurations and the best one looks good with no skill involved.
    """
    trials = int(n_trials)
    if trials < 1:
        raise ValueError("n_trials must be >= 1")
    if trials == 1:
        return 0.0
    if sharpe_variance < 0:
        raise ValueError("sharpe_variance must be >= 0")
    sd = math.sqrt(sharpe_variance)
    gamma = _EULER_MASCHERONI
    return sd * ((1.0 - gamma) * normal_ppf(1.0 - 1.0 / trials)
                 + gamma * normal_ppf(1.0 - 1.0 / (trials * math.e)))


@dataclass
class DeflatedSharpe:
    sharpe: float
    observations: int
    n_trials: int
    expected_max_sharpe: float
    probabilistic_sharpe: float
    deflated_sharpe: float
    skew: float
    kurtosis: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sharpe": round(self.sharpe, 4),
            "observations": self.observations,
            "n_trials": self.n_trials,
            "expected_max_sharpe": round(self.expected_max_sharpe, 4),
            "probabilistic_sharpe": round(self.probabilistic_sharpe, 4),
            "deflated_sharpe": round(self.deflated_sharpe, 4),
            "skew": round(self.skew, 4),
            "kurtosis": round(self.kurtosis, 4),
            "note": self.note,
        }


def deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    n_trials: int,
    sharpe_variance: float,
    benchmark: float = 0.0,
) -> DeflatedSharpe:
    """PSR against the Sharpe expected from the best of ``n_trials`` searches.

    ``n_trials`` is every configuration that was evaluated before this one was
    chosen — the whole grid, not the shortlist. ``sharpe_variance`` is the
    variance of the Sharpe ratios across those trials. Neither can be recovered
    from a single run, which is why both are required arguments and why
    certificates record what was passed: an under-reported trial count deflates
    nothing and the result would look rigorous anyway.
    """
    values = [float(v) for v in returns]
    if len(values) < 2:
        return DeflatedSharpe(0.0, len(values), n_trials, 0.0, 0.0, 0.0, 0.0, 3.0,
                              note="too few observations")
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return DeflatedSharpe(0.0, len(values), n_trials, 0.0, 0.0, 0.0, 0.0, 3.0,
                              note="zero variance")
    sharpe = mean / sd
    skew = sample_skew(values)
    kurtosis = sample_kurtosis(values)
    threshold = expected_max_sharpe(n_trials, sharpe_variance) + benchmark
    psr = probabilistic_sharpe_ratio(sharpe, len(values), benchmark=benchmark,
                                     skew=skew, kurtosis=kurtosis)
    dsr = probabilistic_sharpe_ratio(sharpe, len(values), benchmark=threshold,
                                     skew=skew, kurtosis=kurtosis)
    note = ""
    if n_trials <= 1:
        note = ("n_trials=1: nothing was deflated. Pass the real number of "
                "configurations evaluated, or read this as PSR only.")
    return DeflatedSharpe(
        sharpe=sharpe,
        observations=len(values),
        n_trials=int(n_trials),
        expected_max_sharpe=threshold,
        probabilistic_sharpe=psr,
        deflated_sharpe=dsr,
        skew=skew,
        kurtosis=kurtosis,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Multiple testing                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class MultipleTestResult:
    method: str
    alpha: float
    p_values: List[float] = field(default_factory=list)
    rejected: List[bool] = field(default_factory=list)
    threshold: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "alpha": self.alpha,
            "p_values": [round(p, 5) for p in self.p_values],
            "rejected": list(self.rejected),
            "threshold": round(self.threshold, 5),
            "survivors": sum(1 for flag in self.rejected if flag),
        }


def bonferroni(p_values: Sequence[float], alpha: float = 0.05) -> MultipleTestResult:
    """Family-wise error control. Conservative, and the right default when any
    single false positive would put money at risk."""
    values = [float(p) for p in p_values]
    if not values:
        return MultipleTestResult("bonferroni", alpha, [], [], 0.0)
    threshold = alpha / len(values)
    return MultipleTestResult("bonferroni", alpha, values,
                              [p <= threshold for p in values], threshold)


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> MultipleTestResult:
    """False-discovery-rate control: expected share of accepted results that are
    false. Less conservative than Bonferroni and the usual choice when the point
    is to shortlist candidates for further work rather than to approve one."""
    values = [float(p) for p in p_values]
    n = len(values)
    if n == 0:
        return MultipleTestResult("benjamini-hochberg", alpha, [], [], 0.0)
    order = sorted(range(n), key=lambda i: values[i])
    threshold = 0.0
    cutoff_rank = 0
    for rank, index in enumerate(order, start=1):
        if values[index] <= alpha * rank / n:
            cutoff_rank = rank
            threshold = alpha * rank / n
    rejected = [False] * n
    for rank, index in enumerate(order, start=1):
        if rank <= cutoff_rank:
            rejected[index] = True
    return MultipleTestResult("benjamini-hochberg", alpha, values, rejected, threshold)
