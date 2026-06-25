from xauby.engine.base import BaseEngine
from xauby.engine.alerts import AlertMixin
from xauby.engine.risk import RiskMixin
from xauby.engine.orders import OrderMixin
from xauby.engine.reconcile import ReconcileMixin
from xauby.engine.loop import LoopMixin


class LiteTradingEngine(BaseEngine, AlertMixin, RiskMixin, OrderMixin, ReconcileMixin, LoopMixin):
    pass
