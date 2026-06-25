"""Exchange plugin modules.

Each ``exchange_<name>.py`` module here registers one exchange's REST gateway
(``@register_exchange``) and, when available, its websocket adapter
(``@register_ws``). They are auto-discovered by
:mod:`xauby.api.exchange_registry` and :mod:`xauby.api.ws_registry`.
"""
