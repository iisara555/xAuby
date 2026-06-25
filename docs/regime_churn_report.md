# Regime Router Churn — Analysis, Root Cause & Fix

**คำถาม:** การมีหลาย strategy แมปกับทุก market regime ทำให้ "strategist เปลี่ยนไปมาถี่ๆ
จน position ยังไม่ทันเปิด ตลาดก็เปลี่ยนเงื่อนไขแล้ว" ใช่ปัญหานี้ไหม?

**คำตอบ: ใช่ และมีต้นตอที่ชัดเจนเป็น bug — แก้แล้ว**

อาการ "เปลี่ยนไปมาถี่ๆ" ไม่ได้มาจากการมีหลาย strategy เป็นหลัก แต่มาจาก **bug ของจังหวะ
debounce**: router/debounce ถูกเรียก **ทุก tick (~60s)** แต่ trade บน timeframe สูง
(BTC 1h) ทำให้ `debounce_candles: 3` ที่ควรหมายถึง "regime ต้องอยู่ครบ 3 แท่ง"
กลายเป็น **"3 ticks ≈ 3 นาที"** ตัวกรองหลายแท่งจึงแทบไม่ทำงาน strategy เลยสลับทันที
ที่ regime กระพริบ — ก่อนที่ strategy เดิมจะมีโอกาสหา entry

---

## 1) Root cause (หลักฐานจากโค้ด)

- main loop วน `time.sleep(interval_seconds=60)` แล้วเรียก `_tick_body()` ทุกรอบ
  (`xauby/engine/loop.py:1688,1737`)
- ทุก tick จะรัน `classify_market()` + `RegimeRouter.evaluate()` →
  `RegimeDebouncer.update()`
- `RegimeDebouncer` **นับจำนวนครั้งที่ถูกเรียก** ไม่มี guard ผูกกับ candle timestamp
  (`sc.last_candle_timestamp` ถูกตั้งไว้แต่ไม่เคยถูกใช้ gate)
- ภายใน 1 แท่ง closed candle ไม่เปลี่ยน → label เท่าเดิมทุก tick → debounce วิ่งถึง
  threshold ภายใน 3 ticks แรกของแท่งที่ regime ใหม่โผล่ → ยืนยันใน ~3 นาที ไม่ใช่ 3 แท่ง

ดังนั้นเจตนา "3-candle confirmation" ที่เขียนใน CLAUDE.md / คอมเมนต์ ถูก bypass จริงใน
production การวัดรอบแรก (รัน classify 1 ครั้ง/แท่ง) จึงเห็นแต่พฤติกรรม "อุดมคติ" —
ของจริง churn หนักกว่านั้นมาก

---

## 2) สิ่งที่แก้ (commit นี้)

1. **Gate router ให้ทำงานครั้งเดียวต่อแท่งที่ปิดแล้ว** — `xauby/engine/regime_gate.py`
   (`should_route_on_candle`) + gate ใน `loop.py` ด้วย `sc._last_routed_candle_ts`
   → `debounce_candles/recovery_candles/force_close_candles` กลับมานับ "แท่ง" จริง
   (3 แท่ง = 3 ชม.บน 1h กว่าจะสลับ) classification ยังรันทุก tick เพื่อให้ dashboard สด
2. **เปิด `regime_confidence_filter: true`** (`bot_config.yaml architecture`) — ระงับการ
   สลับไป strategy ตอน regime มั่นใจต่ำ/transition (มี warmup guard 30 samples)
3. **ปิดช่องโหว่ความปลอดภัยของ confidence filter** — เดิม `evaluate()` เช็ค confidence
   **ก่อน** ตรรกะ NO_TRADE ทำให้ regime หมี/แพนิคที่ confidence ต่ำ **ถูกบล็อกไม่ให้เข้า
   โหมดป้องกัน** แก้ให้ confidence gate ใช้เฉพาะการสลับไป strategy อื่นที่ tradeable เท่านั้น
   ส่วน NO_TRADE/ป้องกันจะทำงานเสมอไม่ว่า confidence เท่าไร
4. เทสต์: `tests/test_regime_gate.py` (gate ต่อแท่ง) + เคสใหม่ใน
   `tests/test_confidence_filter.py` (NO_TRADE ที่ confidence ต่ำต้องยังป้องกัน)

> ทั้งหมดเป็น flag/พฤติกรรมแบบ rollback ง่าย: ตั้ง `regime_confidence_filter: false`
> เพื่อย้อน, candle-gate เป็น bug fix ที่ทำให้คอนฟิกเดิมมีความหมายตามชื่อ

---

## 3) ผลการวัด (harness ขับโค้ดจริง, 3 seeds, 1050 แท่ง 1h)

`scripts/regime_churn_analysis.py` จำลอง **60 ticks ต่อแท่ง** สำหรับเคส PRE-FIX และถือ
position แบบ flat ตลอด (worst case ที่ "strategy ไม่ทันเปิด position") ตัวเลข =
strategy reloads / confirmed switches ต่อ 1050 แท่ง

| สถานการณ์ | confirmed switches | strategy reloads | หมายเหตุ |
|-----------|:-----:|:-----:|----------|
| **PRE-FIX** (router ทุก tick, filter off) | 119–149 | 32–48 | อาการเดิม สลับถี่ |
| **POST-FIX A** (gate ต่อแท่ง, filter off) | 60–63 | 25–30 | debounce เป็น 3 แท่งจริง; ยังคงหลาย strategy |
| **POST-FIX B** (gate + confidence filter) ← **shipped** | 35–38 | 0–2 | สลับเฉพาะ regime มั่นใจสูง |

- **candle-gate อย่างเดียว** ตัด confirmed switch ลง ~2 เท่า (149→61) และ reload ~50%
  (48→25) โดย **ยังเก็บความหลากหลายของ strategy** (donchian/bbrsi/supertrend แบ่งเวลากัน)
- **เพิ่ม confidence filter** ตัด reload เหลือเกือบ 0 บนชุดข้อมูลสังเคราะห์นี้ เพราะช่วง
  chop ของมัน confidence ~0.65 < 0.7 จึงไม่สลับไป mean-reversion — ทำให้คาอยู่กับ
  strategy เทรนด์ (donchian) นี่คือพฤติกรรม "อยู่นิ่งเว้นแต่มั่นใจจริง" ตามที่ต้องการ

> ⚠️ **ข้อควรรู้เรื่องการจูน:** ตัวเลข "donchian 100% / 0 reload" ใน POST-FIX B เป็นผลของ
> ข้อมูลสังเคราะห์ที่ choppy/มั่นใจต่ำเป็นพิเศษ (worst case) ข้อมูลจริงที่มีเทรนด์ชัดจะมี
> ช่วง confidence > 0.7 ทำให้ strategy อื่น ๆ ได้ทำงานบ้าง ถ้าต้องการให้ routing
> หลากหลายขึ้น/ไวขึ้น ให้ลด `regime_router.confidence_threshold` (เช่น 0.6) หรือปิด
> `regime_confidence_filter` แล้วพึ่ง candle-gate อย่างเดียว (POST-FIX A)

---

## 4) ตอบคำถาม "หลาย strategy × ทุก regime = สลับถี่จนไม่ทันเปิด position?"

- **บางส่วนใช่** การมีหลายปลายทาง (donchian / bbrsi_mean_reversion / supertrend_ema200 /
  bbkc_squeeze) ทำให้มีโอกาส ping-pong ระหว่างกันมากขึ้น โดยเฉพาะช่วง chop ที่ regime
  กระพริบระหว่าง SIDEWAYS_CHOP ↔ *_TREND_WEAK
- **แต่ตัวขยายหลักคือ bug จังหวะ tick-vs-candle** เมื่อแก้แล้วการสลับต้องให้ regime อยู่
  จริง 3 แท่ง (3 ชม.) + ผ่าน confidence gate strategy จึงมีเวลาหา entry ก่อนถูกสลับ
- ตัวเลือกลดความหลากหลายเพิ่มเติม (ถ้ายังถี่ไป): ยุบ mapping ให้ regime ที่ใกล้กันชี้
  strategy เดียว เช่น `SIDEWAYS_CHOP` กับ `BEAR_TREND_WEAK` ใช้ตัวเดียวกัน — **อันนี้
  เปลี่ยนพฤติกรรมการเทรด ควร backtest ก่อน** จึงยังไม่แตะใน commit นี้

---

## 5) Config ปัจจุบันหลังแก้

| ค่า | ที่ | ค่า | ผล |
|-----|-----|-----|----|
| candle-gate | `loop.py` + `regime_gate.py` | เปิด (bug fix) | debounce นับแท่งจริง |
| `debounce_candles` | `regime_router` | 3 | = 3 แท่ง (3 ชม.บน 1h) |
| `regime_confidence_filter` | `architecture` | **true** (เดิม false) | กันสลับช่วงมั่นใจต่ำ |
| `confidence_threshold` | `regime_router` | 0.7 | knob หลักในการจูนความไว |
| `instant_switch_when_flat` | `regime_router` | false | คงไว้ ลด churn |

---

## 6) วัดเทียบ: ลด strategy ปลายทาง × confidence threshold

รัน `scripts/regime_churn_analysis.py --matrix --seeds 7,11,23` (post-fix gate,
debounce=3, flat worst case) cell = **reloads / regime switches / NO_TRADE% / จำนวน
strategy ที่ใช้จริง (≥1% ของแท่ง)**

| mapping (จำนวน strat) | thr=off | 0.60 | 0.65 | **0.70** | 0.75 |
|---|---|---|---|---|---|
| V0 current (4) | 28/61/23%/4 | 28/61/23%/4 | 24/59/23%/3 | **0/36/28%/1** | 0/36/28%/1 |
| V3 three (3) | 26/61/23%/3 | 26/61/23%/3 | 22/59/23%/2 | 0/36/28%/1 | 0/36/28%/1 |
| V1 two (2) | 23/61/23%/2 | 23/61/23%/2 | 22/61/23%/2 | 0/39/28%/1 | 0/39/28%/1 |
| V2 trend-only (1) | 0/61/23%/1 | 0/61/23%/1 | 0/61/23%/1 | 0/61/23%/1 | 0/61/23%/1 |

ตีความสำคัญ:

1. **regime switches คงที่ ~61 ทุก mapping** เพราะ regime เปลี่ยนระดับเดิม การลด strategy
   แค่ทำให้ regime ที่ติดกันชี้ strategy เดียวกัน → ไม่ reload (ไม่ใช่ลดการเปลี่ยน regime)
2. **ลด strategy ปลายทางช่วย churn เล็กน้อย** (off: 28→26→23→0 reloads จาก 4→3→2→1 ตัว)
   ผลใหญ่อยู่ที่ตัวสุดท้าย (เหลือ 1 ตัว = 0 reload โดยปริยาย)
3. **threshold มี "หน้าผา" ระหว่าง 0.65 → 0.70** บนข้อมูลนี้ confidence ช่วง chop ≈ 0.65–0.69
   ที่ 0.70 จึงกรองการสลับเกือบหมด (เหลือ 1 strategy, 0 reload) ที่ ≤0.65 แทบไม่กรอง
4. **ที่ threshold 0.70 ที่ ship อยู่ การลด mapping แทบไม่มีผลเพิ่ม** — เพราะ filter ยุบให้
   เหลือ 1 strategy อยู่แล้ว สองคันโยก (filter กับ ลด mapping) ทำงานทับซ้อนกัน

⚠️ **ข้อจำกัดที่ต้องเน้น:** harness นี้วัด **churn** ไม่ใช่ **กำไร** การเหลือ strategy เดียว/
churn 0 ไม่ได้แปลว่ากำไรดีกว่า — ในตลาด range จริง mean-reversion อาจทำเงินได้ที่ trend
follower เสีย ต้อง backtest P&L ก่อนตัดสินใจ และ "หน้าผา 0.70" เป็นของข้อมูลสังเคราะห์
ตำแหน่งจริงบน BTC 1h ต่างออกไป — **ต้องรันซ้ำด้วย `--csv` ของจริง**

**สรุปคำแนะนำ:**
- ถ้าเป้าหมายคือ "churn น้อยสุด" → config ที่ ship (4-strat + 0.70) ทำถึงแล้ว การลด mapping
  ไม่จำเป็น
- ถ้าอยากคง multi-strategy ให้ทำงานจริง + churn ปานกลาง → ลด threshold เป็น ~0.65
  (คง 2–3 strategy, reloads ~22–24) อาจคู่กับ V3 (3 ตัว) เพื่อความเรียบง่าย
- ถ้าอยากตัด strategy thrash ทิ้งทั้งหมดอย่างเด็ดขาด → V2 (trend-only) reloads 0 ทุก
  threshold, filter เหลือหน้าที่แค่ gate NO_TRADE — แต่ทิ้งกลยุทธ์ range ไป
- ทุกตัวเลือก **ควร backtest P&L** ด้วยข้อมูลจริงก่อน apply

### ✅ Config ที่ apply (Balanced)

เลือกแนว Balanced: **3 strategy ปลายทาง + confidence_threshold 0.65**

- `LOW_VOL_ACCUMULATION` / `LOW_VOL_RANGE` ย้ายจาก `bbkc_squeeze` → `bbrsi_mean_reversion`
  (เหลือ 3 ตัว: `donchian_trend`, `supertrend_ema200`, `bbrsi_mean_reversion`)
- `regime_router.confidence_threshold: 0.70 → 0.65` (คง `regime_confidence_filter: true`)
- ผลคาดจาก matrix: ~22 reloads, คง 2–3 strategy ทำงานจริง (vs 0.70 ที่ยุบเหลือ 1)
- ⚠️ ยัง **ไม่ได้ backtest P&L** (network bล็อก) — ควรรัน
  `scripts/replay_backtest.py` / `--matrix --csv` ด้วยข้อมูลจริงก่อนเปิด live

## 7) Reproduce / วัดกับข้อมูลจริง

```bash
# offline (synthetic, ขับโค้ดจริง, แสดง before/after)
PYTHONPATH=. python3 scripts/regime_churn_analysis.py --seed 7

# matrix: ลด strategy ปลายทาง x confidence threshold
PYTHONPATH=. python3 scripts/regime_churn_analysis.py --matrix --seeds 7,11,23

# กับข้อมูลจริง (เมื่อเครือข่ายเปิด)
python scripts/fetch_global_klines.py --symbol BTCUSDT --timeframe 1h --years 2
PYTHONPATH=. python3 scripts/regime_churn_analysis.py --csv <klines.csv> --timeframe 1h
PYTHONPATH=. python3 scripts/regime_churn_analysis.py --matrix --csv <klines.csv> --timeframe 1h
```

เทสต์ที่เกี่ยวข้อง (ไม่พึ่ง pandas_ta):
```bash
PYTHONPATH=. python3 -m unittest \
  tests.test_regime_gate tests.test_confidence_filter \
  tests.test_no_trade_handoff tests.test_regime_router_mapping
```
