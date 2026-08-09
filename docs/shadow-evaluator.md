# Durable Shadow Evaluator

The shadow evaluator is xAuby's first infrastructure step toward an ensemble
of strategies. It compares one Champion and one Challenger for the same pair,
target, venue, and candle schedule without adding either candidate to the live
engine loop.

This is a research lane, not an order lane:

- candles are read from the tenant engine SQLite database with SQLite
  `mode=ro` and `PRAGMA query_only=ON`;
- the worker has no broker object, exchange credentials, order sink, or live
  loop import;
- the systemd unit uses `PrivateNetwork=true` and exposes only the tenant's
  runtime through a read-only bind mount, with only that tenant's `shadow/`
  directory writable; credential, config, control DB, backup, and other-tenant
  runtime paths are inaccessible inside the worker mount namespace;
- every tenant worker runs as its own `xsh-<tenant>` OS identity, separate from
  both `xauby-control` and the money-trading engine user;
- every candidate has an isolated strategy context and virtual ledger;
- only fully closed candles are evaluated; the first run starts at the latest
  closed candle rather than turning old history into misleading forward data;
- results are labelled `research_only` and are never posted automatically to
  the promotion endpoint.

## Durable artifacts

For one prepared symbol, the control plane writes:

```text
/var/lib/xauby/runtime/<tenant>/shadow/<SYMBOL>/
├── spec.json
├── status.json
└── runs/<run-id>/events.jsonl
```

`spec.json` is deterministic for the exact catalog configurations. Changing a
candidate or promoting a new Champion produces a new spec hash and run id.

Each JSONL event contains the closed-candle market snapshot, both signals,
candidate-scoped ledger state, research metrics, the prior event hash, and its
own SHA-256. The append is flushed with `fsync`. On restart, the worker rebuilds
state from that hash chain. It may discard an incomplete final write, but it
fails closed on a malformed completed event or a broken chain.

`status.json` is an atomic, bounded projection for the SaaS Strategy Arena. The
UI distinguishes `prepared`, `healthy`, `stale`, and `degraded`; registry state
is not presented as running telemetry.

## Research fill model

MVP uses `signal_close_only_v1_research`:

- signal decisions occur after a closed candle;
- fills use that candle's close plus `0.02%` adverse slippage;
- each entry is allocated `25%` of virtual equity;
- each fill assumes a `0.05%` fee;
- ledgers start with 1,000 virtual USDT.

It deliberately does not simulate intrabar stop, trailing-stop, partial-fill,
funding, or order-book behavior. For that reason its metrics are directional
research evidence, not broker-grade PnL and not sufficient on their own for
promotion.

## Activation and safety gate

The installer places `xauby-shadow@.service` and `xauby-shadow@.timer`, but does
not enable a timer. A pair becomes `prepared` only when its Strategy Arena has
both a catalog-certified Champion and a catalog-certified Challenger with the
same target and comparable timeframes.

After verifying the prepared spec and tenant ACLs, an operator can start one
pass without touching the live engine:

```bash
/usr/local/libexec/xauby-provision-tenant <tenant>  # idempotent ACL/user upgrade
systemctl start xauby-shadow@<tenant>.service
systemctl status xauby-shadow@<tenant>.service
```

Only then should periodic evaluation be explicitly enabled:

```bash
systemctl enable --now xauby-shadow@<tenant>.timer
```

The OKX BTC catalog now carries a frozen LONG-D1 research Challenger, but it is
published as `not_assessed` and `live_certified=false`. The no-input GitHub
workflow `BTC LONG-D1 Challenger certification` runs its locked native-OKX
full-history and five-fold protocol. Until that artifact passes and a reviewed
matching certificate is committed, the Challenger cannot enter a Strategy
Arena and the timer remains intentionally unarmed. Adding a research preset
does not relax catalog certification, promotion evidence, manual Trade PIN
confirmation, live re-approval, or the controlled-restart gates.

## Direction after MVP

The next useful sequence is:

1. certify the conservative BTC LONG-D1 configuration Challenger with the
   frozen finalist protocol, while continuing separate research for a genuinely
   orthogonal strategy;
2. run Champion and Challenger forward for at least 30 days and 20 closed
   trades, retaining the durable artifact;
3. add intrabar/replay evaluation as a separate, explicitly named fill model;
4. review and post signed evaluation provenance through the admin endpoint;
5. keep promotion manual until several independent forward windows show a
   stable edge;
6. only after that evidence, test a constrained allocator (for example a
   regime-gated winner-take-all selector) in shadow before considering any
   capital-splitting ensemble.

This order keeps ensemble complexity out of the live money path while building
the evidence needed to decide whether combining strategies adds value rather
than merely averaging correlated signals.
