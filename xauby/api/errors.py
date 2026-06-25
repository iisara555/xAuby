from typing import Any


class ExchangeAPIError(Exception):
    """Exchange-neutral API error used by all gateway adapters."""

    def __init__(self, code: int | str, msg: str, raw: Any = None):
        self.code = code
        self.msg = msg
        self.raw = raw
        super().__init__(f"ExchangeAPIError({code}): {msg}")
