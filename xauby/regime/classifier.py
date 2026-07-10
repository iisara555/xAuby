import math
from typing import List, Dict, Any, Optional


from xauby.regime.models import GoldRegime


# Macro score combination weights.
#
# Convention: the macro inputs (``dxy_score``, ``fred_score``, ``news_score``)
# are produced by ``xauby.macro.sentiment_guard`` already oriented to the
# *traded asset* — positive means bullish, negative means bearish. The DXY/rate
# inversions ("strong USD is bad for gold", "rising rates are bad for gold") are
# baked into the producer, so the classifier must NOT invert them again. Hence
# the defaults are all ``+1.0``: ``combined_macro`` simply sums the
# already-oriented scores, positive => RISK-ON, negative => RISK-OFF.
#
# Weights are configurable per asset class via
# ``macro_sentiment_guard.macro_weights``. An asset whose correlation to the
# dollar/rates is the opposite of gold's would flip the relevant sign to -1.0.
DEFAULT_MACRO_WEIGHTS: Dict[str, float] = {"news": 1.0, "dxy": 1.0, "fred": 1.0}

# The sentiment guard contract (xauby.macro.sentiment_guard) is that every macro
# input is oriented to the traded asset and bounded to [-1, 1]. dxy_score and
# fred_score are hard-clamped at the producer, but news_score comes straight from
# an LLM response and is NOT clamped there — a misbehaving model returning e.g.
# 5.0 would silently dominate the combined bias. Clamping every input to this
# range here is the "normalise before combining" step: it puts all three on one
# commensurable scale so none can outweigh the others purely by magnitude.
MACRO_INPUT_CLIP: float = 1.0

# |combined_macro| above this flips the macro bias off NEUTRAL. Overridable via
# ``macro_sentiment_guard.regime_bias_threshold`` so operators can tune macro
# sensitivity per deployment without editing code.
DEFAULT_MACRO_BIAS_THRESHOLD: float = 0.3

# Minimum closed bars required for a fallback-computed EMA200 to be trustworthy.
# compute_ema() returns a value for any length (seeded at closes[0]), so with far
# fewer than 200 bars the "EMA200" is really a slow EMA over whatever exists,
# heavily weighted to the oldest price. Below one full period we refuse to assert
# a BULLISH/BEARISH trend off it. When the indicator registry supplies ema_200
# (computed over full history upstream) this floor does not apply.
EMA200_MIN_BARS: int = 200

# ── Regime classification thresholds ──────────────────────────────────────────
# Every hand-tuned cutoff used to categorise trend / volatility / liquidity and
# to synthesise the detailed regime lives here, so the bands stay consistent
# across _trend_strength, _trend_quality_score, _volatility_state and
# _synthesise_detailed_regime — which previously repeated raw literals inline
# (e.g. the trend-quality "strong" cutoff appeared three times). Values are
# unchanged from those inline versions: this is a naming pass, not a retune.

# EMA-spread / EMA200-distance / 20-bar-momentum bands qualifying a trend as
# STRONG vs merely WEAK vs FLAT (all fractions of price).
TREND_STRONG_EMA_SPREAD_PCT = 0.004
TREND_STRONG_EMA200_DIST_PCT = 0.015
TREND_STRONG_MOMENTUM_PCT = 0.02
TREND_FLAT_EMA200_DIST_PCT = 0.006
TREND_FLAT_MOMENTUM_PCT = 0.01

# Trend-quality score: per-feature normalisers (a feature at its normaliser
# scores ~1.0) and the blend weights (sum to 1.0).
TQ_SPREAD_NORM = 0.006
TQ_EMA200_DIST_NORM = 0.025
TQ_MOMENTUM_NORM = 0.04
TQ_W_SPREAD = 0.30
TQ_W_DIST = 0.25
TQ_W_MOMENTUM = 0.25
TQ_W_CONSISTENCY = 0.20

# |combined_macro| normaliser for the macro-conviction score.
MACRO_CONVICTION_NORM = 1.2

# Absolute ATR/price ratio vs its rolling average -> coarse HIGH/LOW volatility.
VOL_HIGH_RATIO = 1.3
VOL_LOW_RATIO = 0.7

# Detailed volatility state: relative ATR (vs rolling avg) OR ATR percentile.
VOL_EXTREME_REL = 1.8
VOL_EXTREME_PCTL = 0.95
VOL_EXPANDING_REL = 1.3
VOL_EXPANDING_PCTL = 0.80
VOL_COMPRESSION_REL = 0.55
VOL_COMPRESSION_PCTL = 0.10
VOL_QUIET_REL = 0.75
VOL_QUIET_PCTL = 0.25

# Volume-ratio bands for the discrete liquidity state.
LIQ_SURGE_RATIO = 1.8
LIQ_CONFIRMING_RATIO = 1.2
LIQ_THIN_RATIO = 0.65

# Volume-ratio bands for the continuous liquidity-confirmation score.
LIQ_CONF_STRONG_RATIO = 1.5
LIQ_CONF_OK_RATIO = 1.0
LIQ_CONF_WEAK_RATIO = 0.65

# Detailed-regime synthesis cutoffs — the ones _trend_strength and
# _synthesise_detailed_regime effectively shared.
TREND_QUALITY_STRONG = 0.55       # trend_quality above this = high-conviction trend
PANIC_MOMENTUM_PCT = -0.025       # 20-bar momentum flushing into panic
PANIC_VOL_PERCENTILE = 0.90       # ATR percentile flagging a panic vol spike
NEAR_EMA200_DIST_PCT = 0.008      # |price-EMA200| this small = pinned to the mean
WIDE_RANGE_PCT = 0.035            # 20-bar range this wide = volatility expansion


def resolve_macro_weights(config: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Resolve macro combination weights from config, falling back to defaults."""
    guard = ((config or {}).get("macro_sentiment_guard") or {})
    raw = guard.get("macro_weights") or {}
    weights = dict(DEFAULT_MACRO_WEIGHTS)
    for key in DEFAULT_MACRO_WEIGHTS:
        if key in raw:
            try:
                weights[key] = float(raw[key])
            except (TypeError, ValueError):
                continue
    return weights


def resolve_macro_bias_threshold(config: Optional[Dict[str, Any]]) -> float:
    """Resolve the RISK-ON/RISK-OFF cutoff from config, falling back to default.

    Reads ``macro_sentiment_guard.regime_bias_threshold``. Non-numeric or
    non-positive values fall back to :data:`DEFAULT_MACRO_BIAS_THRESHOLD`.
    """
    guard = ((config or {}).get("macro_sentiment_guard") or {})
    raw = guard.get("regime_bias_threshold")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MACRO_BIAS_THRESHOLD
    return value if value > 0 else DEFAULT_MACRO_BIAS_THRESHOLD


def _clip_macro(value: Any) -> float:
    """Coerce a macro input to float and clamp it to the [-clip, clip] contract."""
    try:
        val = float(value)
        if not math.isfinite(val):
            return 0.0
    except (TypeError, ValueError):
        return 0.0
    return max(-MACRO_INPUT_CLIP, min(MACRO_INPUT_CLIP, val))

def compute_ema(values: List[float], span: int) -> float:
    if not values:
        return 0.0
    if len(values) < 2:
        return values[0]
    alpha = 2.0 / (span + 1.0)
    ema = values[0]
    for val in values[1:]:
        ema = val * alpha + ema * (1.0 - alpha)
    return ema

def compute_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    tr_list = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)
    
    if not tr_list:
        return 0.0
    
    # Wilder's smoothing or simple average for ATR
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list)
    
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _pct_change(values: List[float], lookback: int) -> float:
    if len(values) <= lookback:
        return 0.0
    base = values[-lookback - 1]
    if base <= 0:
        return 0.0
    return (values[-1] - base) / base


def _range_pct(highs: List[float], lows: List[float], close: float, lookback: int = 20) -> float:
    if close <= 0 or not highs or not lows:
        return 0.0
    hh = max(highs[-lookback:])
    ll = min(lows[-lookback:])
    return (hh - ll) / close if close > 0 else 0.0


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        if not math.isfinite(value):
            return lo
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return lo


def _safe_round(value: float, places: int = 4) -> float:
    try:
        if math.isfinite(value):
            return round(float(value), places)
    except (TypeError, ValueError):
        pass
    return 0.0


def _pct_returns(values: List[float], lookback: int = 60) -> List[float]:
    vals = values[-(lookback + 1):] if lookback > 0 else values
    out: List[float] = []
    for i in range(1, len(vals)):
        prev = vals[i - 1]
        if prev > 0:
            out.append((vals[i] - prev) / prev)
    return out


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    var = sum((v - avg) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(max(0.0, var))


def _percentile_rank(value: float, sample: List[float]) -> float:
    clean = [float(v) for v in sample if v is not None and math.isfinite(float(v))]
    if not clean:
        return 0.5
    below = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    return _clamp((below + 0.5 * equal) / len(clean))


def _historical_atr_ratios(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    *,
    period: int = 14,
    lookback: int = 120,
) -> List[float]:
    ratios: List[float] = []
    if len(closes) < period + 2:
        return ratios
    start = max(period + 1, len(closes) - lookback)
    for end in range(start, len(closes) + 1):
        close = closes[end - 1]
        if close <= 0:
            continue
        atr = compute_atr(highs[:end], lows[:end], closes[:end], period)
        if atr > 0:
            ratios.append(atr / close)
    return ratios


def _directional_consistency(closes: List[float], lookback: int = 20) -> float:
    returns = _pct_returns(closes, lookback)
    if not returns:
        return 0.0
    up = sum(1 for r in returns if r > 0)
    down = sum(1 for r in returns if r < 0)
    total = max(1, up + down)
    return (up - down) / total


def _trend_quality_score(
    *,
    trend: str,
    ema_spread_pct: float,
    ema200_distance_pct: float,
    momentum_20: float,
    directional_consistency: float,
) -> float:
    if trend == "NEUTRAL":
        return 0.0
    direction = 1.0 if trend == "BULLISH" else -1.0
    spread_score = _clamp(abs(ema_spread_pct) / TQ_SPREAD_NORM)
    distance_score = _clamp(abs(ema200_distance_pct) / TQ_EMA200_DIST_NORM)
    momentum_score = _clamp(abs(momentum_20) / TQ_MOMENTUM_NORM)
    consistency_score = _clamp(direction * directional_consistency)
    return _clamp(
        TQ_W_SPREAD * spread_score
        + TQ_W_DIST * distance_score
        + TQ_W_MOMENTUM * momentum_score
        + TQ_W_CONSISTENCY * consistency_score
    )


def _macro_conviction_score(combined_macro: float) -> float:
    return _clamp(abs(combined_macro) / MACRO_CONVICTION_NORM)


def _liquidity_confirmation_score(volume_ratio: float) -> float:
    if volume_ratio <= 0:
        return 0.35
    if volume_ratio >= LIQ_CONF_STRONG_RATIO:
        return 1.0
    if volume_ratio >= LIQ_CONF_OK_RATIO:
        return 0.75
    if volume_ratio >= LIQ_CONF_WEAK_RATIO:
        return 0.55
    return 0.25


def _trend_strength(
    *,
    trend: str,
    ema_spread_pct: float,
    ema200_distance_pct: float,
    momentum_20: float,
) -> str:
    if trend == "BULLISH":
        if (
            ema_spread_pct >= TREND_STRONG_EMA_SPREAD_PCT
            and ema200_distance_pct >= TREND_STRONG_EMA200_DIST_PCT
            and momentum_20 > TREND_STRONG_MOMENTUM_PCT
        ):
            return "STRONG_UP"
        if ema_spread_pct > 0 and ema200_distance_pct > 0:
            return "WEAK_UP"
    if trend == "BEARISH":
        if (
            ema_spread_pct <= -TREND_STRONG_EMA_SPREAD_PCT
            and ema200_distance_pct <= -TREND_STRONG_EMA200_DIST_PCT
            and momentum_20 < -TREND_STRONG_MOMENTUM_PCT
        ):
            return "STRONG_DOWN"
        if ema_spread_pct < 0 and ema200_distance_pct < 0:
            return "WEAK_DOWN"
    if abs(ema200_distance_pct) <= TREND_FLAT_EMA200_DIST_PCT and abs(momentum_20) <= TREND_FLAT_MOMENTUM_PCT:
        return "FLAT"
    return "CHOPPY"


def _volatility_state(
    volatility: str,
    vol_ratio: float,
    avg_vol_ratio: float,
    vol_percentile: float = 0.5,
) -> str:
    if avg_vol_ratio <= 0:
        return volatility
    rel = vol_ratio / avg_vol_ratio
    if rel >= VOL_EXTREME_REL or vol_percentile >= VOL_EXTREME_PCTL:
        return "EXTREME_EXPANSION"
    if rel >= VOL_EXPANDING_REL or vol_percentile >= VOL_EXPANDING_PCTL:
        return "EXPANDING"
    if rel <= VOL_COMPRESSION_REL or vol_percentile <= VOL_COMPRESSION_PCTL:
        return "COMPRESSION"
    if rel <= VOL_QUIET_REL or vol_percentile <= VOL_QUIET_PCTL:
        return "QUIET"
    return "NORMAL"


def _liquidity_state(volume_ratio: float) -> str:
    if volume_ratio >= LIQ_SURGE_RATIO:
        return "SURGE"
    if volume_ratio >= LIQ_CONFIRMING_RATIO:
        return "CONFIRMING"
    if 0.0 < volume_ratio < LIQ_THIN_RATIO:
        return "THIN"
    return "NORMAL"


def _macro_alignment(trend: str, macro_bias: str) -> str:
    if trend == "BULLISH" and macro_bias == "RISK-ON":
        return "ALIGNED"
    if trend == "BEARISH" and macro_bias == "RISK-OFF":
        return "ALIGNED"
    if macro_bias == "NEUTRAL" or trend == "NEUTRAL":
        return "MIXED"
    return "CONFLICT"


def _synthesise_detailed_regime(
    *,
    trend: str,
    volatility: str,
    volatility_state: str,
    liquidity_state: str,
    macro_bias: str,
    trend_strength: str,
    momentum_20: float,
    range_pct_20: float,
    ema200_distance_pct: float,
    trend_quality: float,
    vol_percentile: float,
) -> Dict[str, Any]:
    macro_alignment = _macro_alignment(trend, macro_bias)
    panic_like = (
        trend == "BEARISH"
        and volatility_state in {"EXPANDING", "EXTREME_EXPANSION"}
        and (macro_bias == "RISK-OFF" or momentum_20 <= PANIC_MOMENTUM_PCT)
        and (trend_quality >= TREND_QUALITY_STRONG or vol_percentile >= PANIC_VOL_PERCENTILE)
    )
    bull_breakout = (
        trend == "BULLISH"
        and trend_strength in {"WEAK_UP", "STRONG_UP"}
        and volatility_state in {"EXPANDING", "EXTREME_EXPANSION"}
        and liquidity_state in {"CONFIRMING", "SURGE"}
        and trend_quality >= TREND_QUALITY_STRONG
    )
    bear_breakdown = (
        trend == "BEARISH"
        and trend_strength in {"WEAK_DOWN", "STRONG_DOWN"}
        and volatility_state in {"EXPANDING", "EXTREME_EXPANSION"}
        and trend_quality >= TREND_QUALITY_STRONG
    )
    low_vol = volatility_state in {"COMPRESSION", "QUIET"} or volatility == "LOW"
    near_ema200 = abs(ema200_distance_pct) <= NEAR_EMA200_DIST_PCT
    wide_range = range_pct_20 >= WIDE_RANGE_PCT

    if panic_like:
        regime = "PANIC_SELL"
        phase = "CAPITULATION"
        risk_state = "PANIC_RISK"
    elif low_vol and near_ema200 and trend_strength in {"FLAT", "CHOPPY", "WEAK_UP", "WEAK_DOWN"}:
        regime = "LOW_VOL_ACCUMULATION"
        phase = "ACCUMULATION"
        risk_state = "QUIET_RISK"
    elif bull_breakout:
        regime = "BULL_BREAKOUT"
        phase = "MARKUP"
        risk_state = "RISK_ON_TREND" if macro_alignment != "CONFLICT" else "MOMENTUM_WITH_MACRO_CONFLICT"
    elif bear_breakdown:
        regime = "BEAR_BREAKDOWN"
        phase = "MARKDOWN"
        risk_state = "RISK_OFF_TREND"
    elif trend == "BULLISH" and trend_strength == "STRONG_UP":
        regime = "BULL_TREND_STRONG"
        phase = "MARKUP"
        risk_state = "RISK_ON_TREND"
    elif trend == "BULLISH":
        regime = "BULL_TREND_WEAK"
        phase = "RECOVERY_RALLY" if macro_alignment == "CONFLICT" else "MARKUP"
        risk_state = "MOMENTUM_RISK"
    elif trend == "BEARISH" and trend_strength == "STRONG_DOWN":
        regime = "BEAR_TREND_STRONG"
        phase = "MARKDOWN"
        risk_state = "RISK_OFF_TREND"
    elif trend == "BEARISH":
        regime = "BEAR_TREND_WEAK"
        phase = "DISTRIBUTION"
        risk_state = "DEFENSIVE"
    elif low_vol:
        regime = "LOW_VOL_RANGE"
        phase = "ACCUMULATION"
        risk_state = "QUIET_RISK"
    elif volatility_state in {"EXPANDING", "EXTREME_EXPANSION"} and wide_range:
        regime = "VOLATILITY_EXPANSION"
        phase = "TRANSITION"
        risk_state = "CHOP_RISK"
    else:
        regime = "SIDEWAYS_CHOP"
        phase = "RANGE"
        risk_state = "CHOP_RISK"

    transition_risk = "LOW"
    if volatility_state in {"EXPANDING", "EXTREME_EXPANSION"} and trend == "NEUTRAL":
        transition_risk = "HIGH"
    elif macro_alignment == "CONFLICT" or regime in {"VOLATILITY_EXPANSION", "SIDEWAYS_CHOP"}:
        transition_risk = "MEDIUM"
    if regime == "PANIC_SELL":
        transition_risk = "EXTREME"

    return {
        "regime": regime,
        "phase": phase,
        "risk_state": risk_state,
        "transition_risk": transition_risk,
        "macro_alignment": macro_alignment,
    }


def _strategy_bias_for_regime(regime: str, risk_state: str, transition_risk: str) -> Dict[str, Any]:
    if regime in {"BULL_BREAKOUT", "BULL_TREND_STRONG", "BULL_TREND_WEAK"}:
        family = "trend_following"
        preferred = ["supertrend_ema200", "xauby_actionzone"]
        allowed = ["BUY", "HOLD", "SELL"]
        posture = "risk_on"
    elif regime in {"BEAR_BREAKDOWN", "BEAR_TREND_STRONG", "PANIC_SELL"}:
        family = "defensive"
        preferred = ["capital_protection", "mean_reversion_only_after_reclaim"]
        allowed = ["HOLD", "SELL"]
        posture = "risk_off"
    elif regime in {"LOW_VOL_ACCUMULATION", "LOW_VOL_RANGE"}:
        family = "range_or_accumulation"
        preferred = ["mean_reversion", "pullback", "small_size_breakout_watch"]
        allowed = ["BUY", "HOLD", "SELL"]
        posture = "selective"
    else:
        family = "chop_filter"
        preferred = ["reduced_size", "wait_for_breakout_confirmation"]
        allowed = ["HOLD", "SELL"]
        posture = "cautious"

    confidence_modifier = 1.0
    if transition_risk in {"HIGH", "EXTREME"}:
        confidence_modifier = 0.75
    elif risk_state in {"CHOP_RISK", "QUIET_RISK"}:
        confidence_modifier = 0.85

    return {
        "family": family,
        "preferred": preferred,
        "allowed_actions": allowed,
        "posture": posture,
        "confidence_modifier": confidence_modifier,
    }

def classify_market(
    prices: List[Dict[str, Any]],
    indicators: Optional[Dict[str, Any]] = None,
    macro_state: Optional[Dict[str, Any]] = None,
    timeframe: str = "4h",
    macro_weights: Optional[Dict[str, float]] = None,
    macro_bias_threshold: Optional[float] = None,
) -> GoldRegime:
    """Classify the current market regime using technical indicators and macro data.

    Prices should be historical candles in chronological order (oldest first) with:
    'open', 'high', 'low', 'close', 'volume'. ``macro_weights`` controls how DXY,
    rates, and news combine into the macro bias; defaults are gold-tuned but can
    be overridden per asset class (see :func:`resolve_macro_weights`).
    ``macro_bias_threshold`` sets the RISK-ON/RISK-OFF cutoff on the combined
    macro score (defaults to :data:`DEFAULT_MACRO_BIAS_THRESHOLD`; resolve from
    config with :func:`resolve_macro_bias_threshold`).
    """
    if not prices:
        return GoldRegime(
            regime="UNKNOWN",
            trend="NEUTRAL",
            volatility="NORMAL",
            macro_bias="NEUTRAL",
            confidence=0.5,
            gold_score=50,
            reasons=[],
            phase="UNKNOWN",
            risk_state="UNKNOWN",
            trend_strength="NEUTRAL",
            volatility_state="NORMAL",
            liquidity_state="UNKNOWN",
            transition_risk="LOW",
            strategy_bias=_strategy_bias_for_regime("UNKNOWN", "UNKNOWN", "LOW"),
            features={},
        )

    closes = [float(p["close"]) for p in prices]
    highs = [float(p["high"]) for p in prices]
    lows = [float(p["low"]) for p in prices]
    latest_close = closes[-1]

    # 1. Trend Classification via EMAs
    ind = indicators or {}
    tf_suffix = f"_{timeframe.lower()}"
    ema_12 = float(ind.get(f"ema_12{tf_suffix}") or compute_ema(closes, 12))
    ema_26 = float(ind.get(f"ema_26{tf_suffix}") or compute_ema(closes, 26))

    # EMA200 is only trustworthy if the registry supplied it (full-history
    # compute) or we have at least one full period of closed bars. Otherwise the
    # fallback is a slow EMA over too little data — see EMA200_MIN_BARS.
    ema_200_supplied = ind.get(f"ema_200{tf_suffix}")
    if ema_200_supplied:
        ema_200 = float(ema_200_supplied)
        ema_200_reliable = ema_200 > 0
    else:
        ema_200 = compute_ema(closes, 200)
        ema_200_reliable = ema_200 > 0 and len(closes) >= EMA200_MIN_BARS

    # Without a trustworthy EMA200 we must not assert a directional trend off it,
    # and its distance signal must not leak into regime synthesis.
    if ema_200_reliable and ema_12 > ema_26 > ema_200 and latest_close > ema_200:
        trend = "BULLISH"
    elif ema_200_reliable and ema_12 < ema_26 < ema_200 and latest_close < ema_200:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    ema_spread_pct = (ema_12 - ema_26) / latest_close if latest_close > 0 else 0.0
    if ema_200_reliable and latest_close > 0:
        ema200_distance_pct = (latest_close - ema_200) / latest_close
    else:
        ema200_distance_pct = 0.0
    momentum_5 = _pct_change(closes, 5)
    momentum_20 = _pct_change(closes, 20)
    direction_consistency = _directional_consistency(closes, 20)
    realized_vol_20 = _stdev(_pct_returns(closes, 20))
    realized_vol_60 = _stdev(_pct_returns(closes, 60))
    range_pct_20 = _range_pct(highs, lows, latest_close, 20)

    # 2. Volatility Classification via ATR
    atr = float(ind.get(f"atr{tf_suffix}") or compute_atr(highs, lows, closes, 14))
    vol_ratio = atr / latest_close if latest_close > 0 else 0.0

    historical_vols = _historical_atr_ratios(highs, lows, closes, period=14, lookback=120)
    avg_vol_ratio = sum(historical_vols) / len(historical_vols) if historical_vols else 0.005
    vol_percentile = _percentile_rank(vol_ratio, historical_vols) if historical_vols else 0.5
    if vol_ratio > avg_vol_ratio * VOL_HIGH_RATIO:
        volatility = "HIGH"
    elif vol_ratio < avg_vol_ratio * VOL_LOW_RATIO:
        volatility = "LOW"
    else:
        volatility = "NORMAL"

    volume_ratio = _volume_ratio(ind, prices, timeframe=timeframe)
    trend_strength = _trend_strength(
        trend=trend,
        ema_spread_pct=ema_spread_pct,
        ema200_distance_pct=ema200_distance_pct,
        momentum_20=momentum_20,
    )
    trend_quality = _trend_quality_score(
        trend=trend,
        ema_spread_pct=ema_spread_pct,
        ema200_distance_pct=ema200_distance_pct,
        momentum_20=momentum_20,
        directional_consistency=direction_consistency,
    )
    detailed_volatility_state = _volatility_state(volatility, vol_ratio, avg_vol_ratio, vol_percentile)
    liquidity_state = _liquidity_state(volume_ratio)

    # 3. Macro Bias from macro guard parameters
    macro = macro_state or {}
    # Incorporate DXY score, FED rates, and News sentiment score. Each input is
    # clamped to the sentiment-guard's [-1, 1] contract before combining so the
    # three sit on one scale — see MACRO_INPUT_CLIP for why news_score in
    # particular needs this.
    dxy_score = _clip_macro(macro.get("dxy_score", 0.0))
    fred_score = _clip_macro(macro.get("fred_score", 0.0))
    news_score = _clip_macro(macro.get("news_score", 0.0))

    # Combined macro score: positive is bullish (Risk-On), negative is bearish (Risk-Off).
    # The inputs are already oriented to the traded asset by the sentiment guard
    # (positive = bullish), so the default weights are all +1.0 and must not
    # re-invert DXY/rates. See DEFAULT_MACRO_WEIGHTS for the convention.
    weights = {**DEFAULT_MACRO_WEIGHTS, **(macro_weights or {})}
    combined_macro = (
        weights["news"] * news_score
        + weights["dxy"] * dxy_score
        + weights["fred"] * fred_score
    )
    macro_conviction = _macro_conviction_score(combined_macro)

    threshold = (
        macro_bias_threshold
        if macro_bias_threshold is not None and macro_bias_threshold > 0
        else DEFAULT_MACRO_BIAS_THRESHOLD
    )
    if combined_macro < -threshold:
        macro_bias = "RISK-OFF"
    elif combined_macro > threshold:
        macro_bias = "RISK-ON"
    else:
        macro_bias = "NEUTRAL"

    detailed = _synthesise_detailed_regime(
        trend=trend,
        volatility=volatility,
        volatility_state=detailed_volatility_state,
        liquidity_state=liquidity_state,
        macro_bias=macro_bias,
        trend_strength=trend_strength,
        momentum_20=momentum_20,
        range_pct_20=range_pct_20,
        ema200_distance_pct=ema200_distance_pct,
        trend_quality=trend_quality,
        vol_percentile=vol_percentile,
    )

    # 4. Target Regime Synthesis
    regime = detailed["regime"]
    macro_alignment_score = 0.72
    if detailed["macro_alignment"] == "ALIGNED":
        macro_alignment_score = 0.95
    elif detailed["macro_alignment"] == "CONFLICT":
        macro_alignment_score = 0.42
    vol_clarity = abs(vol_percentile - 0.5) * 2.0
    liquidity_score = _liquidity_confirmation_score(volume_ratio)
    regime_stability = 1.0
    if detailed["transition_risk"] == "MEDIUM":
        regime_stability = 0.72
    elif detailed["transition_risk"] == "HIGH":
        regime_stability = 0.55
    elif detailed["transition_risk"] == "EXTREME":
        regime_stability = 0.85
    trend_component = trend_quality if trend != "NEUTRAL" else 0.45
    confidence = (
        0.34 * trend_component
        + 0.20 * macro_alignment_score
        + 0.14 * macro_conviction
        + 0.14 * vol_clarity
        + 0.10 * liquidity_score
        + 0.08 * regime_stability
    )
    if regime in {"LOW_VOL_RANGE", "LOW_VOL_ACCUMULATION", "SIDEWAYS_CHOP"}:
        confidence = max(confidence, 0.48 + 0.20 * (1.0 - trend_quality))
    if detailed["macro_alignment"] == "CONFLICT":
        confidence -= 0.08
    if detailed["transition_risk"] == "HIGH":
        confidence -= 0.08
    if not ema_200_reliable:
        # Trend axis was suppressed for lack of a trustworthy EMA200; don't
        # present the resulting (necessarily non-trending) call with high
        # confidence.
        confidence = min(confidence, 0.5) - 0.05
    confidence = _clamp(confidence, 0.05, 0.98)
    gold_score = int(round(confidence * 100))
    reasons = _build_user_reasons(
        trend=trend,
        dxy_score=dxy_score,
        fred_score=fred_score,
        fred_rate=float(macro.get("fred_rate", 0.0)),
        indicators=ind,
        prices=prices,
        timeframe=timeframe,
    )
    features = {
        "ema_spread_pct": _safe_round(ema_spread_pct),
        "ema200_distance_pct": _safe_round(ema200_distance_pct),
        "atr_pct": _safe_round(vol_ratio),
        "avg_atr_pct": _safe_round(avg_vol_ratio),
        "atr_percentile": _safe_round(vol_percentile),
        "volume_ratio": _safe_round(volume_ratio),
        "momentum_5_pct": _safe_round(momentum_5),
        "momentum_20_pct": _safe_round(momentum_20),
        "realized_vol_20_pct": _safe_round(realized_vol_20),
        "realized_vol_60_pct": _safe_round(realized_vol_60),
        "range_20_pct": _safe_round(range_pct_20),
        "directional_consistency": _safe_round(direction_consistency),
        "trend_quality": _safe_round(trend_quality),
        "macro_conviction": _safe_round(macro_conviction),
        "liquidity_confirmation": _safe_round(liquidity_score),
        "macro_alignment": detailed["macro_alignment"],
        "institutional_confidence": _safe_round(confidence),
        "ema200_reliable": ema_200_reliable,
    }

    return GoldRegime(
        regime=regime,
        trend=trend,
        volatility=volatility,
        macro_bias=macro_bias,
        confidence=round(confidence, 2),
        gold_score=gold_score,
        reasons=reasons,
        phase=detailed["phase"],
        risk_state=detailed["risk_state"],
        trend_strength=trend_strength,
        volatility_state=detailed_volatility_state,
        liquidity_state=liquidity_state,
        transition_risk=detailed["transition_risk"],
        strategy_bias=_strategy_bias_for_regime(regime, detailed["risk_state"], detailed["transition_risk"]),
        features=features,
    )


def compute_volume_ratio(volumes: List[float], sma_window: int = 20) -> float:
    """Calculate ratio of last completed volume vs SMA of completed volumes.

    Replicates compute_volume_ratio_4h cleanly without strategy imports.
    """
    if not volumes or len(volumes) < sma_window + 1:
        return 0.0
    completed = volumes[:-1]
    if len(completed) < sma_window:
        return 0.0
    last_completed_vol = completed[-1]
    sma = sum(completed[-sma_window:]) / sma_window
    if sma <= 0:
        return 0.0
    return round(last_completed_vol / sma, 4)


def _volume_ratio(indicators: Dict[str, Any], prices: List[Dict[str, Any]], timeframe: str = "4h") -> float:
    tf_suffix = f"_{timeframe.lower()}"
    vol_ratio = float(indicators.get(f"volume_ratio{tf_suffix}") or 0.0)
    if vol_ratio > 0:
        return vol_ratio
    volumes = [float(p.get("volume", 0) or 0) for p in prices if p.get("volume") is not None]
    return compute_volume_ratio(volumes)


def _build_user_reasons(
    *,
    trend: str,
    dxy_score: float,
    fred_score: float,
    fred_rate: float,
    indicators: Dict[str, Any],
    prices: List[Dict[str, Any]],
    timeframe: str = "4h",
) -> List[Dict[str, Any]]:
    """Plain-language checklist for dashboard users."""
    reasons: List[Dict[str, Any]] = []

    if dxy_score > 0.05:
        reasons.append({"label": "DXY Weak", "supportive": True})
    elif dxy_score < -0.05:
        reasons.append({"label": "DXY Strong", "supportive": False})
    else:
        reasons.append({"label": "DXY Neutral", "supportive": False})

    if fred_score > 0.05:
        reasons.append({"label": "Inflation Supportive", "supportive": True})
    elif fred_score < -0.05:
        reasons.append({"label": "Rates Headwind", "supportive": False})
    elif 0 < fred_rate <= 4.0:
        reasons.append({"label": "Inflation Supportive", "supportive": True})
    else:
        reasons.append({"label": "Inflation Neutral", "supportive": False})

    if trend == "BULLISH":
        reasons.append({"label": "Trend Bullish", "supportive": True})
    elif trend == "BEARISH":
        reasons.append({"label": "Trend Bearish", "supportive": False})
    else:
        reasons.append({"label": "Trend Neutral", "supportive": False})

    vol_ratio = _volume_ratio(indicators, prices, timeframe=timeframe)
    if vol_ratio >= 1.0:
        reasons.append({"label": "Volume Strong", "supportive": True})
    else:
        reasons.append({"label": "Volume Weak", "supportive": False})

    return reasons
