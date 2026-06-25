# Regime Strategy Selection Protocol (BTCUSDT 1h)

เอกสารนี้บันทึกวิธีคัดเลือก strategy ตาม regime ด้วยข้อมูลจริง — strategy ใหม่จะแทนที่ของเดิมใน
`regime_router.mapping` ได้ก็ต่อเมื่อชนะตามเกณฑ์ด้านล่างเท่านั้น (ข้อมูลเป็นคนตัดสิน ไม่ใช่ความเชื่อ)

## ข้อมูล

- Source: Binance **global** public API (`api.binance.com/api/v3/klines`) — BTCUSDT 1h, ~3 ปี
  (มิ.ย. 2023 → มิ.ย. 2026, 26,297 แท่งปิดแล้ว) ดึงด้วย `scripts/fetch_global_klines.py`
- ไฟล์: `core/backtest_candles_1h_btcusdt_full.csv` (แยกจาก cache ปกติของ TUI โดยเจตนา)
- Fee model: เท่ากันทุก strategy (จาก `backtest.fee_pct` / `trading.fee_pct` ใน bot_config)

## การ label regime (ไม่มี lookahead)

- ใช้ `classify_market()` ตัวเดียวกับ live engine, rolling window 250 แท่ง (เท่า `get_candles(limit=250)` ใน loop)
- Label ของแท่ง i ใช้ข้อมูลถึงแท่ง i เท่านั้น; cache ที่ `core/regime_labels_1h_btcusdt.csv`
- ข้อจำกัดที่ทราบ: live ส่ง `indicators_cache` + macro sentiment เข้า classifier ด้วย แต่ harness ให้
  classifier คำนวณเองจาก window และ `macro_state={}` → label ใกล้เคียง live แต่ไม่ identical ทุกแท่ง

## กลุ่ม regime → strategy เดิม (incumbent)

| Family | Regime labels | Incumbent |
|---|---|---|
| trend | BULL_BREAKOUT, BULL_TREND_STRONG, BULL_TREND_WEAK | cdc_action_zone |
| lowvol | LOW_VOL_ACCUMULATION, LOW_VOL_RANGE | bbkc_squeeze |
| chop | SIDEWAYS_CHOP, BEAR_TREND_WEAK | bbrsi_mean_reversion |
| vol_expand | VOLATILITY_EXPANSION | supertrend_ema200 |

Candidates: `donchian_trend` (trend), `rsi2_meanrev` (lowvol + chop), `vol_breakout` (vol_expand)

## วิธีวัด (masked replay)

- `scripts/regime_strategy_eval.py` รัน ReplayEngine ตัวจริง (PositionSimulator + fee + SL/trailing เดียวกับ
  backtest ปกติ) แต่ gate ไม่ให้ BUY นอกแท่งที่ regime อยู่ในกลุ่มของ strategy นั้น (จำลอง RegimeRouter;
  exit ทำงานปกติเหมือน handoff ที่ position เดิมถูกบริหารต่อ)
- ทั้ง candidate และ incumbent ถูกวัด **แบบเดียวกัน บนข้อมูลเดียวกัน**
- ข้อจำกัด: ไม่จำลอง debounce/force-close ของ router เต็มรูปแบบ (มี `--debounce` ลด gap ได้)

## Split

- **train** = 70% เก่าสุด — ใช้จูน parameter (grid เล็ก ≤ 12 combos/strategy) และคัดผู้ชนะรอบแรก
- **test** = 30% ใหม่สุด — out-of-sample แท้ (มี warmup 420 แท่ง, ตัด trade ที่ entry ก่อน test ออก)
  **ห้ามจูนบน test เด็ดขาด**
- **recent** = ~6 เดือนล่าสุด (~4,420 แท่ง) — sanity check เทียบ window ของ binance.th

## เกณฑ์ตัดสิน (ครบทุกข้อจึงแทนที่ incumbent ได้)

1. PF_candidate > PF_incumbent ทั้ง **train และ test** (วัด masked แบบเดียวกัน)
2. จำนวน trade ≥ 20 (train) และ ≥ 8 (test)
3. PF_test ≥ 1.0 และ maxDD_test ≤ 1.5 × ของ incumbent (และ ≤ 15% absolute)
4. Recent window: PF ≥ 0.8 (sanity — ไม่พลิกหัวกลับในตลาดปัจจุบัน)

ไม่ผ่านข้อใดข้อหนึ่ง → **incumbent อยู่ต่อ** สำหรับ mapping key นั้น

## ผลการตัดสิน (2026-06-11)

ข้อมูล: 26,297 แท่ง, sha256[:16] = `eb6ab09f3a5b1aa5`, train = แท่ง 0..18407,
test เริ่ม ts 1752757200 (2025-07-17), grid winners freeze ไว้ใน `core/regime_overrides.json`

| Family | Strategy | Split | PF | WR | Trades | maxDD | Net% |
|---|---|---|---|---|---|---|---|
| trend | cdc_action_zone (เดิม) | train | 0.77 | 29.9% | 498 | 31.4% | −29.7% |
| trend | cdc_action_zone (เดิม) | test | 0.53 | 29.7% | 165 | 21.3% | −21.3% |
| trend | donchian_trend | train | **1.16** | 32.7% | 113 | 9.2% | **+7.0%** |
| trend | donchian_trend | test | 0.32 | 23.3% | 43 | 11.5% | −11.3% |
| lowvol | bbkc_squeeze (เดิม) | train / test | 0.27 / 0.51 | — | 33 / 17 | 3.7 / 1.7% | −3.7 / −1.4% |
| lowvol | rsi2_meanrev | train / test | 0.30 / 0.18 | — | 58 / 14 | 4.5 / 1.0% | −4.5 / −0.9% |
| chop | bbrsi_mean_reversion (เดิม) | train / test | 0.60 / 0.40 | — | 76 / 52 | 11.8 / 12.1% | −10.1 / −12.1% |
| chop | rsi2_meanrev | train / test | 0.65 / 0.29 | — | 71 / 28 | 5.2 / 4.9% | −4.3 / −4.6% |
| vol_expand | supertrend_ema200 (เดิม) | train / test | 1.25 / 0.00 | — | **4 / 1** | — | +0.5 / −0.2% |
| vol_expand | vol_breakout | train / test | 0.50 / 0.52 | — | 21 / 6 | 8.9 / 1.0% | −5.1 / −0.5% |

### คำตัดสินตาม protocol

**ไม่มี candidate ตัวไหนผ่าน gate — mapping เดิมคงไว้ทั้งหมด**

- `donchian_trend` ชนะ train ขาด (PF 1.16 vs 0.77, DD 9.2% vs 31.4%) แต่พังบน test
  (PF 0.32 < 0.53 ของเดิม และ < 1.0) → ไม่ผ่านข้อ 1 และ 3
- `rsi2_meanrev` แพ้ทั้งสองกลุ่มบน test → ไม่ผ่าน
- `vol_breakout` PF < 1 ทุก split; vol_expand มี trade น้อยมากทั้งคู่ (regime แค่ 2.2% ของแท่ง) → ไม่ผ่าน

### ข้อค้นพบที่สำคัญกว่า

**Strategy เดิม (TradingView ports) ก็ขาดทุน out-of-sample ทุกกลุ่มเช่นกัน** ช่วง ก.ค. 2025 – มิ.ย. 2026:
cdc −21.3% (DD 21%), bbrsi −12.1%, bbkc −1.4%, supertrend แทบไม่เทรด

Attribution ราย label ของ cdc (กลุ่ม trend):
- train: BULL_TREND_WEAK −277 (382 trades), BULL_TREND_STRONG −89, BULL_BREAKOUT **+70**
- test: ติดลบทุก label (BULL_TREND_WEAK −120, BULL_TREND_STRONG −48, BULL_BREAKOUT −45)

ข้อสรุปจากข้อมูล: BTC 1h long-only ด้วยชุด strategy ที่มีทั้งหมด (เก่าและใหม่) ไม่มี edge
ที่พิสูจน์ได้ในตลาดยุคปัจจุบัน — ตัวเลือกที่ข้อมูลสนับสนุนคือลดการเทรด BTC (sim/null)
หรือเปลี่ยนแนวทาง (timeframe 4h, holding ยาวขึ้น) แล้ววัดใหม่ด้วย harness เดิม

## รอบ 2: BTCUSDT 4h (2026-06-11)

ข้อมูล: 6,574 แท่ง 4h (3 ปีเดียวกัน), grid winners ใน `core/regime_overrides_4h.json`,
รายงานเต็ม `core/regime_eval_btcusdt_4h.json`

| Family | Strategy | Train PF (net) | Test PF (net) | Recent PF |
|---|---|---|---|---|
| trend | cdc_action_zone (เดิม) | 1.45 (+29.4%) | 0.58 (−8.0%) | 0.49 |
| trend | donchian_trend (entry 24, ADX off) | **1.75** (+26.1%) | 0.38 (−5.9%) | 0.02 |
| lowvol | ทั้งคู่ | trade 3-4 ตัว — วัดไม่ได้ | ~0 trade | — |
| chop | bbrsi_mean_reversion (เดิม) | 2.39 (+6.5%) | **1.07** (+0.2%, 8 trades) | 0.00 (2 trades) |
| chop | rsi2_meanrev | 0.70 (−4.8%) | 0.59 (−1.8%) | 1.64 (4 trades) |
| vol_expand | ทั้งคู่ | trade 0-2 ตัว — inert | 0 trade | — |

### คำตัดสินรอบ 4h

**ไม่มี candidate ผ่าน gate เช่นกัน — mapping เดิมคงไว้**

ลายแทงที่ชัดเจนข้ามทั้งสอง timeframe: ทุก strategy ฝั่ง trend ทำกำไรดีบน train
(ครอบตลาดกระทิง 2023-2025) แล้วพังบน test (ก.ค. 2025 → มิ.ย. 2026) เหมือนกันหมด —
ปัญหาไม่ใช่ตัว indicator แต่คือ**ยุคตลาด**: ช่วง 11 เดือนล่าสุด BTC ไม่เอื้อ long-only
ทุกตระกูลที่ทดสอบ จุดเดียวที่ OOS เป็นบวกคือ bbrsi บน 4h chop (PF 1.07, 8 trades —
น้อยเกินกว่าจะมีนัยยะ)

### สถานะหลังการตัดสินใจ

- BTC → **sim mode** (commit b8154a2) ระหว่างรอ edge ที่พิสูจน์ได้ / ตลาดเปลี่ยนยุค
- XAUT → live ตามเดิม (CDC 4h บนทองไม่ได้ถูก indict โดยผลนี้ — คนละ asset/dynamics)
- Router ยัง soak บน BTC sim เก็บข้อมูล live-parallel ต่อ

## รอบ 3: Short-side research (2026-06-11)

Research only (ไม่แตะ engine/mapping) — `ShortPositionSimulator` ใน harness, convention
BUY=เปิด short, fee model Binance futures (taker 0.05%/ข้าง + funding 0.01%/8ชม.)
Families: `bear` = {BEAR_BREAKDOWN, BEAR_TREND_STRONG, PANIC_SELL} (ปัจจุบัน null = ไม่เทรด),
`bear_weak` = {BEAR_TREND_WEAK} เทียบ bbrsi (long) บน label เดียวกัน
Gate (เข้มกว่ารอบ long เพราะ baseline คือไม่เทรด): PF_train ≥ 1.3 และ PF_test ≥ 1.1,
trades ≥ 20/8, maxDD ≤ 15%

### Grid บน train (1h)

| Candidate | Family | Best combo | PF train | Trades |
|---|---|---|---|---|
| donchian_short | bear | entry_len 72, SL 2.5×ATR | **0.68** ❌ | 79 |
| supertrend_short | bear | — | trade < 10 ❌ | — |
| rsi2_short | bear_weak | — | trade < 10 ❌ | — |

ตัวที่ดีที่สุดขาดทุนตั้งแต่ **in-sample** — ไม่ผ่าน gate ตั้งแต่ด่านแรก
คำอธิบายเชิงเศรษฐศาสตร์: label regime เป็น lagging indicator — กว่า BEAR_BREAKDOWN
จะถูก confirm ราคาลงไปมากแล้ว การเปิด short หลัง confirmation = short ใส่จังหวะเด้ง
ส่วน supertrend_short/rsi2_short เงื่อนไขเข้า + การ gate ให้เหลือเฉพาะแท่งหมี
ทำให้แทบไม่มีโอกาสเข้าเลย

### คำตัดสินรอบ short

**NO-GO — ไม่มี short candidate ตัวไหนเข้าใกล้ gate** ตัวที่ดีที่สุดไม่ผ่านด่านแรก
(PF_train 0.68 < 1.3) จึงไม่จำเป็นต้องดู test split ตาม protocol (จะรันเพิ่มเป็น
เอกสารเสริมได้ด้วยคำสั่งใน section reproduce ข้างบน + `--skip-long --short-strategies ...`)

ผลรวมทั้ง 3 รอบ (long 1h, long 4h, short 1h) ชี้ทางเดียวกัน: regime-gated
strategy บน BTC ในยุคตลาดปัจจุบันไม่มี edge ที่พิสูจน์ได้ทั้งสองทิศทาง —
การไม่เทรด (sim/null) คือ position ที่ข้อมูลสนับสนุนที่สุด จนกว่ายุคตลาดจะเปลี่ยน
หรือมีแนวทางที่ต่างระดับ (ไม่ใช่ permutation ของ indicator เดิม)

## คำสั่ง reproduce

```bash
python scripts/fetch_global_klines.py --symbol BTCUSDT --timeframe 1h --years 3
python scripts/regime_strategy_eval.py --grid          # จูน candidates บน train เท่านั้น
python scripts/regime_strategy_eval.py --config-overrides core/regime_overrides.json   # ตารางตัดสิน
```
