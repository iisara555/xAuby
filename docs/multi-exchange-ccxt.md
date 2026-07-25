# Multi-Exchange CCXT Adapter

xAuby routes exchange access through a pluggable `IExchangeGateway`. The
**current committed live path is CCXT against OKX** USDT-settled perpetual
swap (`exchange.provider: ccxt`, `ccxt_id: okx`) — see
[OKX USDT perpetual and short safety](#okx-usdt-perpetual-and-short-safety)
below. The native Binance.th REST/WS client (`LiteBinanceClient` /
`BinanceWebSocket`, `xauby/api/exchanges/exchange_binance.py`) still exists in
the plugin registry (`provider: binance`) for spot deployments or older
configs, but it is not the active baseline.

## Switching exchange from the launcher (no file editing)

`python launcher.py` → option 4 → **Exchange & Flags** → "Switch / set up exchange
(guided wizard)" walks through: pick provider (from the registry) → `ccxt_id` →
`quote_asset` → enter the API key/secret (the wizard writes the *correct* `.env`
var for that exchange via `credential_env_names`, e.g. `KRAKEN_API_KEY`) → runs a
**connection test** → offers a restart. The same submenu also exposes individual
credential editors and a standalone "Test connection". This is the recommended
path; the YAML below is the equivalent hand-edit.

Minimal config:

```yaml
exchange:
  provider: ccxt
  ccxt_id: kraken
  api_key_env: KRAKEN_API_KEY
  api_secret_env: KRAKEN_API_SECRET
  quote_asset: USDT
```

Optional settings:

```yaml
exchange:
  provider: ccxt
  ccxt_id: binance
  sandbox: true
  enable_rate_limit: true
  base_url: https://api.example.test
  params:
    options:
      defaultType: spot   # the live OKX baseline uses `swap` — see below
```

Current adapter behavior:

- Accepts engine symbols like `BTCUSDT` and maps them to CCXT symbols like
  `BTC/USDT` through exchange market metadata.
- Normalizes tickers, candles, balances, open orders, fetched orders, and new
  orders into the Binance-shaped dictionaries the current engine expects.
- Converts BUY order `amount` from quote notional into base amount, matching
  the current engine convention.
- Streams live tickers via `ccxt.pro` (`watch_ticker`) when the exchange plugin
  registry is enabled (see below). If `ccxt.pro` is unavailable, the adapter
  declines and the engine falls back to its REST polling loop — no crash.
- Does not enable exchange-side `STOP_LOSS_LIMIT` by default. Until a dedicated
  exchange-specific adapter is added, use local stop monitoring or opt in with
  an exchange-specific capability after testing that exchange's CCXT params.

## Exchange plugin registry + websocket streaming

The exchange layer is a plugin bundle (REST gateway + websocket adapter) backed
by registries that mirror the strategy/indicator pattern. Enable it with the
architecture feature flag (default `false` keeps the legacy hardcoded factory):

```yaml
architecture:
  exchange_plugin_registry_enabled: true   # rollback = set back to false
```

With the flag on, `exchange.provider` is resolved through the registry
(`available_exchanges()`), and the websocket adapter through `create_ws_adapter()`:

- `provider: binance` → native `LiteBinanceClient` + `BinanceWebSocket`.
- `provider: ccxt` (or any `ccxt_id`) → `CCXTExchangeClient` + `CCXTProWebSocket`
  (real streaming for any ccxt.pro-supported exchange).

### Adding a new exchange (drop-in, no factory edits)

Create one module `xauby/api/exchanges/exchange_<name>.py` that registers both
halves of the bundle:

```python
from xauby.api.exchange_registry import register_exchange
from xauby.api.ws_registry import register_ws
from xauby.api.ws_base import ThreadedWebSocketBase   # or implement ExchangeWebSocket

@register_exchange("myexchange")
def build_myexchange_gateway(config, api_key="", api_secret="", base_url=None):
    return MyGateway(api_key, api_secret, base_url, config=config)

@register_ws("myexchange")
class MyExchangeWebSocket(ThreadedWebSocketBase):
    @staticmethod
    def default_url() -> str: ...
    def _stream_url(self) -> str: ...
    def _parse_message(self, message): ...   # return the canonical tick dict
```

The modules are auto-discovered by `pkgutil` (filename prefix `exchange_`). A
websocket adapter must emit the canonical tick dict
(`{symbol, last, bid, ask, percent_change_24h, timestamp, monotonic_ts}`) and
status events (`{"event": "ws_disconnected" | "ws_reconnected", ...}`) — see
`xauby/api/ws_base.py`. The one-off `exchange.gateway_class` escape hatch still
works and takes priority over the registry.

Environment variables are resolved from `api_key_env`, `api_secret_env`, and
`base_url_env`. If those are omitted, xAuby falls back to exchange-specific
names such as `KRAKEN_API_KEY`, then to the legacy Binance defaults only on the
legacy Binance path.

## Gateway contract

The engine talks to exchanges only through `IExchangeGateway`
(`xauby/api/interface.py`). It never imports `LiteBinanceClient` directly. A
drop-in adapter must implement the `get_*` raw-dict surface documented on the
interface (ticker, candles, exchange info, balances, open orders, order, plus
`place_order`/`cancel_order`). It does **not** need to implement
`get_symbol_filters`: the interface derives lot/price/notional filters from the
adapter's own `get_exchange_info` via the shared helpers in `xauby/api/utils.py`
(`find_symbol_entry`, `parse_exchange_filters`). The same module provides the
neutral `make_client_id` and `round_step` used by the order mixins. A custom
gateway can also be wired in without code changes via `exchange.gateway_class`.

The engine, observability layer, and operational scripts
(`controlled_restart_preflight.py`, `reduce_live_position.py`) all build their
client via `create_exchange_client()` and read credentials via
`resolve_exchange_credentials()`, so they follow the configured exchange rather
than assuming Binance. This is enforced by `tests/test_no_binance_coupling.py`.

## OKX USDT perpetual and short safety

The certified derivatives path uses `market_type: swap`, `margin_mode: isolated`,
`position_mode: one_way`, and a USDT settle asset. Engine symbols remain compact
(`BTCUSDT`) while the adapter resolves the native contract (`BTC/USDT:USDT`) and
converts base quantity to contracts using `contractSize`.

Short signals use semantic `OPEN/CLOSE + SHORT`; the adapter maps these to SELL
and reduce-only BUY. The committed baseline defaults to and caps leverage at 1x
(the runtime hard safety ceiling is 3x). A short strategy must be explicitly
allowed per pair and remains paper-only unless
`short_live_enabled` is set. Startup fails closed when the selected adapter does
not expose swap, positions, and reduce-only capabilities.

CCXT Pro subscribes to ticker, OHLCV, public trades, and order book. Every market
event carries exchange and market identity. A degraded channel blocks new shorts;
REST fallback never changes exchange or market type.

The launcher exchange wizard stages the candidate, verifies the old exchange is
flat, probes all REST and WebSocket channels, then atomically activates the
exchange block. Failed candidates leave the active config untouched.
