# BTC LONG-D1 Challenger — locked finalist certificate

- **Run date:** 2026-08-09
- **Workflow:** [GitHub Actions 31319988968](https://github.com/iisara555/xAuby/actions/runs/31319988968)
- **Measured commit:** `0f7e0cfbae11cf38233d3a6973098e820385a92c`
- **Protocol:** `docs/research/protocols/btc_long_d1_challenger_v1.json`
- **Manifest SHA-256:** `8f929ece13ab61e2d0d720bb461614e32833f4f29fe7e88a59382f7a7a18a64c`
- **Results SHA-256:** `77ece7b7ad31c37ffb7248c9970530c233d810f5d2032ab5a1df55299762a6f3`
- **Proposed-certificate SHA-256:** `b728a77986638e6a60dee3057e16b562f6300b63662e1df5fb08d084f4ce30da`

## Verdict

**CERTIFIED FOR FORWARD SHADOW, NOT FOR LIVE TRADING.** The frozen
`okx-btc-supertrend-long-d1-v1` configuration passed all twelve gates that were
merged into `main` before the run. Its catalog approval remains
`live_certified=false`; this certificate only makes the candidate eligible to
enter the credential-free Strategy Arena beside the BTC Champion.

The Challenger keeps the Champion's strategy, long/short support, SuperTrend
structure, risk sizing, and exits. Its single change is to require the last
closed daily regime to permit new LONG entries, while SHORT entries remain
ungated.

## Full native history

OKX `BTC-USDT-SWAP`: 14,570 4H bars from 2019-12-16 through 2026-08-09, plus
2,446 native 1D regime bars. Replay retained the locked 0.05% fee, 2 bps
slippage, 0.004%/8H funding approximation, 2% risk sizing, and 25% position cap.

| role | PF | net | MDD | trades | decision |
|---|---:|---:|---:|---:|---|
| Champion | 1.510 | +19.16% | 9.81% | 136 | live baseline unchanged |
| LONG-D1 Challenger | **1.581** | +17.35% | **8.79%** | 103 | forward-shadow eligible |

The Challenger improved PF by 4.7% and reduced maximum drawdown by 10.4%, while
retaining 90.6% of net return and 75.7% of trades. It does not establish a
large enough historical advantage to replace the Champion; it establishes a
credible, lower-exposure configuration to test forward.

## Five chronological folds

Each fold used up to 300 past-only warmup bars. Warmup bars could not trade.

| fold | Champion PF / net / trades | Challenger PF / net / trades | PF winner |
|---:|---:|---:|---|
| 1 | 1.274 / +2.58% / 26 | **1.289 / +2.11% / 17** | Challenger |
| 2 | **1.815 / +6.75% / 28** | 1.769 / +6.12% / 24 | Champion |
| 3 | 1.499 / +2.13% / 21 | **1.976 / +2.91% / 16** | Challenger |
| 4 | 2.568 / +9.51% / 32 | **2.805 / +8.41% / 26** | Challenger |
| 5 | **0.596 / -2.71% / 29** | 0.413 / -2.93% / 20 | neither profitable |

Both configurations lost in the most recent fold. That is a material warning,
not a hidden exception: the locked gate required four profitable folds and the
Challenger achieved exactly four. The comparative PF gate also passed at its
minimum of three fold wins. Forward evidence is therefore mandatory.

## Remaining gates

This certificate does not authorize a live config change, capital split, or
automatic selector. Before promotion, the durable shadow run must retain common
Champion/Challenger provenance and meet all existing Arena policy gates:

- at least 30 forward days;
- at least 20 closed trades;
- PF at least 1.10 and MDD no more than 25%;
- score at least 10 points above the Champion on the same window;
- lead for three reviewed evaluations;
- manual Trade PIN confirmation and controlled-restart safety gates.

This candidate is a configuration variant, not a genuinely orthogonal alpha
source. Squeeze Momentum, SMC, BBRSI, CDC, Donchian, and volume-breakout evidence
reviewed so far did not justify forcing them into the ensemble. Orthogonal
research should continue separately while this conservative comparison runs.
