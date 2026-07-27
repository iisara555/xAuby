"""Base class every Strategy plugin must inherit from."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xauby.strategies.context import MarketContext
from xauby.strategies.signal import Signal

#: How far a plugin has actually been taken. Declared, never inferred from a
#: free-text tag: today `research` and `paper-test` live in ``tags`` beside
#: `btc` and `4h`, so "is this allowed near real money" is answered by string
#: matching against a list anyone can append to.
MATURITIES = ("research", "paper", "production")

#: Legacy tags that already carried this meaning, honoured so no existing plugin
#: changes behaviour when it has not declared ``maturity`` yet.
_TAG_MATURITY = {"research": "research", "paper-test": "paper"}


def strategy_maturity(strategy: Any) -> Optional[str]:
    """Resolve a plugin's maturity: declaration first, then legacy tags.

    ``None`` means undeclared, which is deliberately not the same as
    ``"production"``. Nothing may treat an unanswered question as a pass.
    """
    declared = getattr(strategy, "maturity", None)
    if declared:
        value = str(declared).lower()
        if value not in MATURITIES:
            raise ValueError(
                f"strategy {getattr(strategy, 'name', '?')!r} declares maturity "
                f"{declared!r}; expected one of {MATURITIES}"
            )
        return value
    tags = {str(tag).lower() for tag in getattr(strategy, "tags", []) or []}
    for tag, value in _TAG_MATURITY.items():
        if tag in tags:
            return value
    return None


@dataclass
class StrategyMeta:
    """Rich metadata describing a strategy plugin.

    Every strategy exposes this via :meth:`Strategy.get_meta`. The engine,
    CLI, dashboard, and future marketplace can inspect it without
    instantiating the strategy.
    """

    name: str
    version: str
    author: str = "unknown"
    description: str = ""
    tags: List[str] = field(default_factory=list)
    required_timeframes: List[str] = field(default_factory=lambda: ["4h"])
    min_bars: int = 100
    config_schema: Optional[Dict[str, Any]] = None
    #: One of :data:`MATURITIES`, or ``None`` for "nobody has classified this".
    maturity: Optional[str] = None


class Strategy(ABC):
    """Abstract base for all trading strategies.

    A Strategy is a pure "analyst": it looks at market data and returns a
    :class:`Signal`. It MUST NOT place orders, touch the database, log to
    Telegram, or call any engine method directly.

    Subclasses MUST set ``name`` (used as the plugin id in config) and SHOULD
    set ``version`` and ``required_timeframes``.
    """

    name: str = "base"
    version: str = "0.0.0"
    author: str = "unknown"
    description: str = ""
    tags: List[str] = []
    required_timeframes: List[str] = ["4h"]
    min_bars: int = 100
    #: How far this plugin has been taken: one of :data:`MATURITIES`. Left
    #: ``None`` it means undeclared, and the engine refuses to run an
    #: undeclared plugin on a live pair rather than assuming the best.
    maturity: Optional[str] = None

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config: Dict[str, Any] = dict(config or {})

    # ------------------------------------------------------------------ #
    # Required hook                                                      #
    # ------------------------------------------------------------------ #
    @abstractmethod
    def analyze(self, ctx: MarketContext) -> Signal:
        """Return a :class:`Signal` for the current market context."""

    # ------------------------------------------------------------------ #
    # Optional lifecycle hooks (default no-ops)                          #
    # ------------------------------------------------------------------ #
    def on_start(self) -> None:  # pragma: no cover - default no-op
        """Called once by the engine when the strategy is loaded."""

    def on_stop(self) -> None:  # pragma: no cover - default no-op
        """Called once by the engine on shutdown."""

    def on_trade_filled(self, side: str, price: float, qty: float) -> None:  # pragma: no cover
        """Called after the engine confirms a fill (for stateful strategies)."""

    # ------------------------------------------------------------------ #
    # Config hooks                                                       #
    # ------------------------------------------------------------------ #
    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        """Return default config values for this strategy.

        The engine merges these with the YAML config block so that every
        knob is documented and discoverable. Subclasses should override
        this to declare their tunables.
        """
        return {}

    def validate_config(self) -> List[str]:
        """Validate ``self.config`` and return a list of warnings.

        An empty list means the config is valid. The engine calls this on
        load and logs any warnings.  Subclasses may override.

        Authoring convention: phrase an *invalid* value as a "must" message
        (e.g. "rsi_min must be less than rsi_max") and a merely *advisory* note
        as "should"/"usually". :meth:`config_errors` keys off this so strict
        mode can fail closed on real misconfig without rejecting advice.
        """
        return []

    def config_errors(self) -> List[str]:
        """Return the *fatal* subset of :meth:`validate_config` warnings.

        By default these are the entries phrased as "must" (the invalid-value
        convention); "should"/"usually"/research notices stay advisory.
        When ``architecture.strategy_validate_strict`` is on, the engine refuses
        to load a strategy whose ``config_errors()`` is non-empty. Override for
        custom fatal-vs-advisory logic.
        """
        return [w for w in self.validate_config() if "must" in w.lower()]

    # ------------------------------------------------------------------ #
    # Metadata                                                           #
    # ------------------------------------------------------------------ #
    def get_meta(self) -> StrategyMeta:
        """Build a :class:`StrategyMeta` from class-level attributes."""
        return StrategyMeta(
            name=self.name,
            version=self.version,
            author=self.author,
            description=self.description,
            tags=list(self.tags),
            required_timeframes=list(self.required_timeframes),
            min_bars=self.min_bars,
            config_schema=self._config_schema(),
            maturity=strategy_maturity(self),
        )

    def _config_schema(self) -> Optional[Dict[str, Any]]:
        """Override to provide a JSON-Schema-like dict of config knobs."""
        return None

    def describe(self) -> Dict[str, Any]:
        """Serialize metadata for dashboard / CLI listing."""
        try:
            from xauby.strategies.manifest import strategy_manifest_from_class

            return strategy_manifest_from_class(type(self))
        except Exception:
            pass
        meta = self.get_meta()
        return {
            "manifest_version": 1,
            "id": meta.name,
            "name": meta.name,
            "version": meta.version,
            "author": meta.author,
            "description": meta.description,
            "tags": meta.tags,
            "maturity": meta.maturity,
            "required_timeframes": meta.required_timeframes,
            "min_bars": meta.min_bars,
            "config_schema": meta.config_schema,
            "default_config": self.default_config(),
            "permissions": {"network": False, "database": False, "orders": False},
        }

    # ------------------------------------------------------------------ #
    # Helpful representation                                             #
    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<Strategy {self.name} v{self.version}>"
