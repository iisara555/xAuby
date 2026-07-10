# SMC LuxAlgo Strategy

`smc_luxalgo` is an xAuby strategy plugin inspired by LuxAlgo's Smart Money
Concepts indicator. The original Pine script is mostly a drawing tool, so this
plugin turns the core concepts into deterministic trading gates:

- confirmed internal and swing BOS/CHoCH structure breaks
- liquidity sweep/reclaim diagnostics
- simple bullish/bearish order-block zones
- simple bullish/bearish fair-value-gap zones
- premium, discount, and equilibrium diagnostics from the latest swing range
- optional short entries for derivative markets

This is an engineering implementation, not financial advice.

## Strategy ID

```yaml
strategy:
  active: smc_luxalgo
```

## Example Config

```yaml
strategy:
  active: smc_luxalgo
  config:
    smc_luxalgo:
      timeframe: 4h
      internal_length: 5
      swing_length: 20
      atr_period: 14
      volume_ma_period: 20
      vol_min_ratio: 0.0
      entry_event_window: 3
      require_swing_bias: true
      confluence_min_score: 1.0
      require_liquidity_sweep: false
      use_fair_value_gap: true
      fvg_lookback: 20
      fvg_min_atr: 0.0
      use_order_block: true
      order_block_lookback: 12
      order_block_ttl_bars: 60
      zone_proximity_atr: 0.35
      use_discount_premium: true
      sl_atr_mult: 2.0
      sl_buffer_atr: 0.2
      trailing_atr_mult: 1.8
      allow_short: false
      exit_on_opposite_structure: true
      max_calc_bars: 520
```

For spot trading, keep `allow_short: false`. For derivatives, set it true only
after backtesting the symbol, timeframe, and exchange execution mode.

## Signal Rules

Long entry requires:

- latest SMC event is bullish BOS or CHoCH within `entry_event_window`
- swing bias is not bearish when `require_swing_bias` is enabled
- bullish confluence score is at least `confluence_min_score`
- volume ratio passes `vol_min_ratio` when enabled
- ATR is available for stop and trailing hints

Short entry is symmetric and only active when `allow_short: true`.

Open positions exit on confirmed stop-loss or a fresh opposite structure event
when `exit_on_opposite_structure` is enabled.

## Confluence Score

The score adds one point for each active matching condition:

- sweep/reclaim
- order-block support or resistance
- fair-value-gap support or resistance
- discount for longs or premium for shorts
- volume confirmation when `vol_min_ratio` is greater than zero

## Validation

Run the focused tests:

```bash
./venv/bin/python -m pytest tests/test_smc_luxalgo_strategy.py tests/test_indicator_registry.py
```

Then replay or backtest the intended symbol before enabling it live.
