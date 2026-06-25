from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List

@dataclass
class GoldRegime:
    regime: str
    trend: str
    volatility: str
    macro_bias: str
    confidence: float
    gold_score: int = 50
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    phase: str = "UNKNOWN"
    risk_state: str = "NORMAL"
    trend_strength: str = "NEUTRAL"
    volatility_state: str = "NORMAL"
    liquidity_state: str = "NORMAL"
    transition_risk: str = "LOW"
    strategy_bias: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
