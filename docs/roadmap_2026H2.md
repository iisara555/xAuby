# xAuby — CTO Roadmap ครึ่งปีหลัง 2026

## บริบท

`main` เป็นปัจจุบันแล้ว (`8cc6396` — branch ตรงกับ origin ไม่มีอะไรต้อง pull)

นี่ไม่ใช่โปรเจกต์เริ่มใหม่ xAuby เป็นระบบ production ที่รันอยู่จริง: engine เทรด
OKX perpetual swap ด้วยเงินจริง 2 คู่, FastAPI multi-tenant control plane, Next.js
Pilot Workspace บน Vercel และ deploy path ที่ hardened มาอย่างดี audit 3 ฉบับใน
3 สัปดาห์ที่ผ่านมาไม่พบ path ที่เปิดออเดอร์ซ้ำหรือเปิดผิดฝั่งแบบควบคุมไม่ได้
ทั้งโค้ดเบส 54k LOC มี TODO จริงแค่ **1 จุด** CI รัน pytest เต็มชุด + Next.js build
จริง + secret scan ทั้ง tracked และ history

คุณภาพวิศวกรรมดีจริง ปัญหาอยู่ที่อื่น และเป็นปัญหาเชิงโครงสร้าง:

> **ระบบ validation ไม่ได้ชี้ไปที่ exchange ที่เทรดจริง และ surface area
> วิ่งนำหลักฐานไปไกลแล้ว**

เป้าหมายที่ระบุไว้: **business + alpha พร้อมกัน, ทำคนเดียว + AI agents,
บัญชี live เงินตัวเองขนาดเล็ก** — ด้วยกำลังเท่านี้ คำว่า "ทั้งสองอย่าง" ไม่ควร
แปลว่าทำขนานกัน แต่ต้องแปลว่าจัดลำดับให้งาน alpha *คือ* งาน business เพราะสิ่ง
ที่ลูกค้าซื้อคือหลักฐานว่ากลยุทธ์ผ่านการรับรอง — ซึ่งเป็นสิ่งที่ยังขาดอยู่พอดี

---

## 4 ข้อค้นพบที่กำหนดลำดับงานทั้งหมด

ทุกข้อตรวจสอบจากโค้ดจริงโดยตรง ไม่ใช่การอนุมาน

### 0. `partial_tp_pct: 12.0` ของ XAU ยิงไม่ได้เลย — เป็น config ที่ตายแล้ว

**นี่คือข้อที่ต้องแก้ก่อนทุกอย่าง เพราะแก้ที่เดียวจบ และมันเปลี่ยนพฤติกรรม exit
ของคู่หลักที่เทรดเงินจริงอยู่**

Config XAU ตั้งไว้ทั้งสองตัวพร้อมกัน:
- `minimal_roi: {"0": 8.0, "1440": 5.0, "4320": 3.0}` — ขั้นแรกคือ **+8% ตั้งแต่นาทีที่ 0**
- `partial_tp_pct: 12.0`, `partial_tp_fraction: 0.5` — แบ่งขายครึ่งที่ **+12%**

ลำดับใน `xauby/engine/loop.py`: `_apply_minimal_roi_exit` ถูกเรียกที่บรรทัด **2201**
แล้ว `_maybe_take_partial_tp` ถูกเรียกที่บรรทัด **2213** และ guard แรกสุดของ
partial TP คือ `action == "SELL"` (loop.py:305) พร้อม docstring ที่เขียนไว้ตรง ๆ ว่า
*"Runs after the exit pipeline so a full exit (SELL) always wins the tick"*

แปลว่า: ROI ที่ถึง 12% ย่อมถึง 8% ไปแล้ว → `_apply_minimal_roi_exit` เปลี่ยน action
เป็น `SELL` (ปิดเต็มจำนวน) → `_maybe_take_partial_tp` return ทันทีเพราะ
`action == "SELL"` **partial TP ที่ 12% จึงไม่มีทางทำงานได้เลย ไม่ใช่แค่ยาก
แต่เป็นไปไม่ได้เชิงตรรกะ** ตราบใดที่ rung แรกของ minimal_roi ต่ำกว่า 12%

Simulator ก็เรียงเหมือนกัน (`xauby/observability/replay.py`: minimal_roi ที่บรรทัด
614 แล้ว `return events` ที่ 626 ก่อนถึง partial TP ที่ 631) — **ข่าวดีคือ parity
ระหว่าง live กับ backtest ไม่เพี้ยน ข่าวร้ายคือทั้งสองฝั่งไม่เคยทำ partial TP เลย**

**อย่าแก้ ladder เพื่อ "ปลุก" partial TP**

การยก rung แรกให้สูงกว่า 12% *จะ* ทำให้ partial TP ทำงานได้จริง แต่นั่นคือการ
เปลี่ยนกลยุทธ์ ไม่ใช่การแก้บั๊ก — ทางที่ถูกคือลบคีย์ที่ยิงไม่ได้ออก แล้วปล่อยตาราง
exit ไว้ตามที่ deploy อยู่ จนกว่าจะมีงานวิจัยรองรับการเปลี่ยน (ดูข้อ 2: ตอนนี้
config ที่ deploy ยังไม่มี certificate อยู่แล้ว การไปเปลี่ยนตาราง exit เพิ่มอีกชั้น
จะทำให้ยิ่งไม่รู้ว่ากำลังเทรดอะไร)

*เกร็ดที่เกือบพาไปผิดทาง:* stress test ในรายงาน 13 ก.ค. ตัด top-5 winner แล้ว net
ลด 39.8pp ÷ 5 ≈ **8.0% ต่อไม้** ซึ่งพอดีกับ rung แรก (8.0%) จนดูเหมือนหลักฐานว่า
ladder ทำงานอยู่ตอนรับรอง — แต่การทำซ้ำ (ข้อ 2) แสดงว่ารายงานวัด config อื่น
ทั้งชุด ความพอดีนั้นจึงเป็นเรื่องบังเอิญ **บทเรียน: ตัวเลขที่ลงล็อกพอดีไม่ใช่การยืนยัน
ให้รันซ้ำ**

ผลกระทบที่เหลืออยู่จริง 2 ข้อ (เป็นเรื่องความสะอาดของ config และเอกสาร ไม่ใช่
เรื่องผลตอบแทน):
1. **`CLAUDE.md` อธิบายพฤติกรรมที่เกิดขึ้นไม่ได้** — เอกสารเขียนว่า "engine แบ่งขาย
   ครึ่งหนึ่งที่ +12% มาร์ก `trade_states.partial_tp_taken` แล้วปล่อยส่วนที่เหลือให้
   ออกตอน zone flip" ซึ่งไม่เคยเกิดขึ้นจริงแม้แต่ครั้งเดียว รวมถึงใน TUI /
   Pilot Workspace ที่โชว์ `PTP` / `Partial TP` เป็น state ที่ไม่มีทางเปลี่ยนเป็น
   `banked`
2. **`partial_tp_pct` / `partial_tp_fraction` เป็น config ตายที่ทำให้เข้าใจผิด** — ใครมา
   อ่าน config จะเชื่อว่ามีการแบ่งขายครึ่งเกิดขึ้น ทั้งที่ไม่มี

### 1. Backtest ยิงไปผิด exchange — และล้มเหลวแบบเงียบ ๆ

`bot_config.yaml:604-605` ตั้ง `data_source: okx` / `data_base_url:
https://www.okx.com` แล้ว `xauby/backtest/service.py:212` ส่งค่านี้ตรงเข้า
`download_klines` ซึ่งพูดได้แต่ภาษา Binance เท่านั้น
(`xauby/backtest/data.py:82-86`: ถ้าเป็น `binance.com` → `/api/v3/klines`
นอกนั้นทั้งหมด → `/api/v1/klines`)

ผลลัพธ์คือยิงไปที่ `https://www.okx.com/api/v1/klines` พร้อม params ทรง Binance
ซึ่งพังเข้า `except Exception: break` (`data.py:123`) แล้ว return **DataFrame
ว่าง** โดยไม่มี error โผล่ขึ้นมาเลย

ยิ่งไปกว่านั้น: คู่หลักถูก backtest บน **asset ตัวแทน**
(`coin_whitelist.json` → XAU `backtest_data_proxy: "PAXGUSDT"`) และข้อมูลที่
cache ไว้บนดิสก์มีแต่ Binance **spot** CSV

แปลว่าทุกพารามิเตอร์ใน `bot_config.yaml` ถูกเลือกมาจากข้อมูลคนละ exchange
คนละประเภทสินค้า (spot vs perp) และสำหรับคู่หลักคือ **คนละ asset** —
ไม่เคยมีกลยุทธ์ไหนที่เทรด XAUUSDT-SWAP ถูก validate บนข้อมูล XAUUSDT-SWAP จริงเลย

`tests/test_no_binance_coupling.py` เป็น guard กัน coupling ที่ดีมาก แต่
`GUARDED_DIRS` (บรรทัด 15-19) ครอบแค่ `engine`, `observability`, `scripts`
และ **ไม่ครอบ `backtest`** — ช่องนี้แหละที่ทำให้ปัญหารอดจากการย้ายมา OKX มาได้

### 2. config XAU ที่เทรดเงินจริงอยู่ **ไม่มี certificate ที่ผ่าน**

*(ยืนยันด้วยการทำซ้ำ 2026-07-26 — แก้จากที่เอกสารฉบับนี้เคยเขียนผิดว่า "ผ่านแล้ว")*

รายงาน `docs/research/xau_4strategy_comparison_2026-07-13.md` ระบุแถว ActionZone
ว่าเป็น "config live ปัจจุบัน" **ป้ายนั้นผิด** ทำซ้ำด้วย `run_plugin_replay` ของ
รีโปเอง บนข้อมูลชุดเดียวกัน (12,810 แท่ง PAXGUSDT 4h):

| Metric | รายงาน ("live") | ทำซ้ำ long-only + D1 **ON** | ทำซ้ำ config ที่ **deploy** |
|---|---|---|---|
| Trades | 133 | **132** | 421 |
| Profit factor | 2.00 | **2.00** | **1.28** |
| Win rate | 45.9% | **46.2%** | 34.4% |
| Net | +95.9% | **+96.4%** | +73.7% |
| Max drawdown | 8.3% | **7.6%** | **29.2%** |
| IS PF | 1.75 | **1.76** | **1.02** |
| OOS PF | 2.27 | **2.26** | 1.74 |

7 ตัวชี้วัด บน 2 หน้าต่างเวลาที่แยกกัน ตรงกับ long-only + D1 ON และไม่ตรงกับ
config ที่ deploy เลย ยืนยันซ้ำจาก sweep ที่ commit ไว้ (`stageC.jsonl`): config
ตระกูล short-on/D1-off ให้ 345 trades PF 1.21 — family เดียวกับที่วัดได้

**ผลที่ตามมา:**
1. profile จริงของ config ที่ deploy คือ **PF 1.28, MDD 29.2%, IS PF 1.02**
   (แทบเสมอตัวในช่วง chop 2020–2023)
2. **MDD 29.2% เกินรั้ว `risk.drawdown_guard.max_drawdown_pct: 25.0`** — historical
   drawdown ของ config นี้สูงกว่าระดับที่ kill-switch จะตัดการเทรด
3. เอกสาร 2 ฉบับที่เคยดูขัดกัน **จริง ๆ เห็นตรงกัน** ว่า long-only + D1 คือ config
   ที่ผ่าน — ด้วยโปรโตคอลที่ต่างกัน (144-combo + 5-fold WFA / 4-strategy +
   bootstrap CI) ซึ่งแข็งกว่าฉบับใดฉบับเดียว ป้าย "SUPERSEDED" ที่เคยติดไว้บน
   `docs/actionzone_config_search_2026-07.md` **ถูกถอนแล้ว** — คำแนะนำในนั้นยังใช้อยู่
4. การเปรียบเทียบกับ Donchian / SMC Pro / SuperTrend ไม่กระทบ — 3 ตัวนั้นแพ้
   ในทุกการอ่าน

**บทเรียนที่ต้องเข้าโค้ด:** certificate ที่ไม่ระบุ config ที่ใช้รัน **ตรวจไม่ได้** —
นี่คือเหตุที่ป้ายผิดรอดมา 2 สัปดาห์ P1.4 (certification pipeline) ต้องบังคับให้
certificate แนบ resolved strategy config มาด้วยทุกครั้ง

เรื่องที่เกี่ยวเนื่อง: คู่นี้รัน `disable_stop_loss: true` ที่ `position_pct: 0.95` —
95% ของ allocation โดยไม่มี hard stop โค้ดซื่อสัตย์กับเรื่องนี้
(`engine/base.py:545`) แต่ตอนนี้ตัวเลขที่เคยคิดว่ารองรับมันอยู่ (MDD 8.3%) เป็นของ
config อื่น จึงกลับไปเป็น **exposure ที่ยังไม่มีหลักฐานรองรับ**

### 3. เครื่องมือวัดยัง verify พฤติกรรม live ไม่ได้ และไม่มีอะไรปลุกคน

- `observability.durable_high_frequency_events: false` → replay validation รายงาน
  `no_tick_pair` **ไม่มี run ย้อนหลังไหน validate หรือ backfill ได้เลย**
- `xauby/observability/README.md` ระบุว่า short-side replay parity ยังไม่ certified
  และเขียนไว้ตรง ๆ ว่า *"ก่อนใช้ replay output เป็นหลักฐานเพื่อเพิ่มทุน live"* —
  ขณะที่ทั้งสองคู่ที่ live อยู่ **เทรด short**
- `xauby/track_record/validator.py::audit_track_record` — กลไกเดียวที่กระทบยอด
  event log กับตาราง trade — **ไม่มี call site เลย** และถึงเรียกก็พังอยู่ดี เพราะเก็บ
  `active_position` เป็น global ตัวเดียว (`validator.py:21`) และ query event
  โดยไม่กรอง symbol พอมี 2 คู่พร้อมกันจะรายงาน anomaly ปลอมทันที
- `weekly_reviews/` หยุดที่ `2026-07-12` ทั้งที่ `weekly_review.enabled: true`
  หายไป 2 ฉบับ และไม่มีใครรู้มา 2 สัปดาห์ การแจ้งเตือนมีช่องทางเดียวคือ Telegram
  ที่ *engine เป็นคนส่ง* — ถ้า engine ตาย ก็ไม่มีการแจ้งเตือน
  (`heartbeat_interval_minutes: 0`) ส่วน `xauby-healthcheck.timer` มีอยู่ใน
  `deploy/systemd/` แต่ `scripts/install_saas_host.sh` ไม่เคยติดตั้งมัน ขณะที่
  `README.md` อ้างว่ารันทุก 5 นาที

**สรุป: ไตรมาสนี้ยังไม่ควรทำ billing**

*(ข้อสรุปนี้เคยถูกผ่อนลงหลังอ่านรายงาน 13 ก.ค. แล้วต้องกลับมาเข้ม เพราะการทำซ้ำ
แสดงว่ารายงานนั้นไม่ได้วัด config ที่ deploy — ดูข้อ 2)*

**มี config ที่ผ่านการรับรองอยู่จริง แต่มันไม่ใช่ config ที่รันอยู่** — long-only + D1
ผ่านสองงานวิจัยด้วยโปรโตคอลต่างกัน (PF 2.00, MDD 7.6%) ขณะที่ตัวที่ deploy วัดได้
PF 1.28 / MDD 29.2% นั่นเป็นข่าวดีในแง่ว่า **edge มีอยู่และรู้ว่าอยู่ที่ไหน** เพียงแต่
ยังไม่ได้เอามาใช้

รวมกับที่ขาดอยู่เดิม: live trade ~19 ครั้ง ~+11.5 USDT ตลอด 7 สัปดาห์ (น้อยเกินกว่า
จะยืนยันอะไร), preset certified 2 จาก 8 ในแคตตาล็อกที่ลูกค้าเห็น (และตัวเลขของ
XAU preset ยังไม่ได้ verify), ไม่มี track record ที่ commit, replay validation ใช้ไม่ได้
(ข้อ 3), งานวิจัยยังผูกกับ proxy asset (ข้อ 1)

พูดให้ตรง: **ยังไม่มีหลักฐานว่า live สร้างผลตาม backtest ได้ และตอนนี้ยังไม่ได้รัน
config ที่ backtest บอกว่าดี** — ปิดสองช่องว่างนั้นก่อน แล้ว billing จะขายตัวมันเอง

ทางที่ควรทำ: รัน pilot ฟรีกับ design partner แล้วใช้เวลาไตรมาสนี้เปลี่ยน
"ความสามารถทำวิจัย" ให้เป็น "certification pipeline ที่ทำซ้ำได้" — ซึ่งเป็นทั้ง
สินทรัพย์แกนกลางของสินค้า และเป็นเอกสารขายหลักไปพร้อมกัน

---

## Phase 0 — หันเครื่องมือวัดให้ตรงกับความจริง (สัปดาห์ 1–3)

ตราบใดที่การวัดยังผิดและความล้มเหลวยังเงียบ งานหลังจากนี้ไม่มีความหมาย

**P0.0 — ลบ `partial_tp` ที่ตายแล้ว + guard กันตั้งค่าขัดกันเอง**
✅ **ทำแล้ว 2026-07-26** (commit `b8023fe` — 1147 tests ผ่าน, Next.js build ผ่าน)

**คง `minimal_roi` ไว้ตามเดิม** งานนี้เป็นการทำความสะอาด ไม่เปลี่ยนพฤติกรรม
การเทรดแม้แต่ tick เดียว — พิสูจน์จากการอ่านโค้ด ไม่ต้องพึ่งงานวิจัยใด ๆ

1. ลบ `partial_tp_pct` / `partial_tp_fraction` ออกจาก `strategy_params` ของ XAU ใน
   `coin_whitelist.json` — มันยิงไม่ได้อยู่แล้ว การลบไม่เปลี่ยนพฤติกรรมแม้แต่ tick เดียว
2. แก้ `CLAUDE.md` ให้ตรงความจริง: exit ของ XAU คือ zone flip + `minimal_roi`
   ladder (8% → 5% หลัง 1 วัน → 3% หลัง 3 วัน) **ไม่มี** partial TP และแก้ส่วนที่
   ระบุว่า TUI / Pilot Workspace โชว์ `PTP` เป็น `pending`/`banked` ด้วย
3. **เพิ่ม startup guard** ใน `xauby/runtime/exits.py` (หรือจุดที่ validate strategy
   config): ถ้า `partial_tp_pct > 0` และ rung แรกของ `minimal_roi` ≤ `partial_tp_pct`
   → fail หรือ warn ดัง ๆ ตอน start เพราะเป็นการตั้งค่าที่ขัดกันเองเชิงตรรกะ
   รูปแบบเดียวกับ `validate_open_positions_config` ที่เพิ่มตอน F-1
   (audit 2026-07-21) — ปัญหาคลาสเดียวกันคือ "คีย์หลายตัวที่ผู้บริโภคต่างกัน
   และไม่มีใครตรวจว่าสอดคล้อง"
4. เพิ่มเทสต์: (ก) guard ยิงเมื่อ config ขัดกัน (ข) parity test ที่ยืนยันว่าเมื่อ
   `minimal_roi` rung แรก > `partial_tp_pct` แล้ว `partial_tp_triggered` เกิดขึ้นจริง
   ทั้งใน live path และ simulator — เพื่อล็อกว่า partial TP ยังใช้ได้กับคู่อื่นในอนาคต
   (`tests/test_partial_tp_live.py` + `tests/test_replay_parity.py` มีโครงอยู่แล้ว)
5. ยืนยันว่างานวิจัย 13 ก.ค. รันด้วย config อะไร → **ทำแล้ว และเจอว่าไม่ใช่ config
   ที่ deploy** ดูข้อ 2 ทั้งหมด นี่คือผลลัพธ์ที่สำคัญที่สุดของรอบนี้

**สิ่งที่เจอเพิ่มระหว่างทำ (ไม่อยู่ในแผนเดิม):**
- คีย์ `partial_tp` อยู่ **2 ที่** — ทั้ง `coin_whitelist.json` และ `bot_config.yaml`
  (`strategy.config.xauby_actionzone`) ถ้าลบแค่ที่แรก **engine จะสตาร์ตไม่ขึ้น**
  เพราะ guard ตรวจ *resolved* config เจอตอนรัน resolver ไม่ใช่ตอนอ่านโค้ด
- `bot_config.yaml` เดิมมี `minimal_roi: {}` (ว่าง) คู่กับ partial TP — คือตอนที่
  A/B ทดสอบไว้ปี 2026-07-03 (PF 1.66→1.78) partial TP **ทำงานได้จริง** แล้ว ladder
  ที่ใส่เข้ามาที่ระดับ per-symbol ทีหลังเป็นตัวที่ฆ่ามันเงียบ ๆ — เก็บประวัตินี้ไว้ใน
  คอมเมนต์ของ `bot_config.yaml` แล้ว
- guard เวอร์ชันแรกของผมเทียบ `steps[0][1]` โดยไม่ดู age → ปฏิเสธ config ที่ ladder
  เริ่มทีหลัง (`{"1440": 5.0}`) ซึ่งยังมีหน้าต่างให้ partial TP ทำงาน แก้เป็นใช้
  `minimal_roi_pct(steps, 0.0)` เทสต์เป็นตัวจับ

ข้อ 1 แตะ `coin_whitelist.json` จึงต้องไม่มี position ค้างก่อน deploy

**P0.1 — แก้ backtest data path** ✅ **ทำแล้ว 2026-07-26**

- ย้าย OKX candle primitives จาก `scripts/fetch_okx_xau_history.py` ไปเป็น
  `xauby/backtest/okx_data.py` (ทิศ dependency ถูกต้อง: `scripts -> xauby`)
  แล้วให้สคริปต์ re-export กลับเพื่อไม่ให้ `evaluate_okx_xau_migration.py` พัง
- `download_klines` route ไป OKX v5 เมื่อ base_url เป็น OKX และ
  **host ที่ไม่รู้จัก raise** แทนที่จะ fall through ไป `/api/v1/klines` แล้ว return
  frame ว่าง
- `_source_tag_from_url` มี tag `okx` แยกแล้ว — เดิม cache ของ OKX ถูกแท็กเป็น
  `th` และชนกับไฟล์ Binance.TH ของ symbol/timeframe เดียวกัน
- เพิ่ม `xauby/backtest` เข้า `GUARDED_DIRS` — การที่มันไม่อยู่ในลิสต์คือสาเหตุที่
  data layer รอดจากการย้ายมา OKX มาได้
- host matching ใช้ `urlsplit` เทียบ hostname จริง ไม่ใช่ substring — ตอนทดสอบพบว่า
  `okx.com.evil.invalid` ผ่าน substring check ได้ (จะส่ง instId ไปให้เจ้าของโดเมนนั้น)

**ยืนยันปลายทางจริงแล้ว:** ดึง XAU-USDT-SWAP ได้ 249 แท่ง 4h ถึงวันนี้ (close ~4065)
และ BTC-USDT-SWAP 119 แท่ง — path ที่เคยคืน frame ว่างเงียบ ๆ ใช้งานได้จริงแล้ว
18 เทสต์ใหม่ใน `tests/test_backtest_okx_data.py`

**P0.2 — Re-validate ทั้งสองคู่บนข้อมูล venue จริง** ✅ **ทำแล้ว 2026-07-26**
ผลเต็ม: `docs/research/venue_data_revalidation_2026-07-26.md`
เครื่องมือ: `scripts/validate_on_venue_data.py --pair all`

- **BTC ผ่าน** — clip ให้ตรงหน้าต่างของ certificate แล้วได้ **111 trades ตรงกัน
  เป๊ะ** กับที่ certificate รายงาน และบน venue จริงผลดีขึ้น: **+16.39%** (OKX swap)
  เทียบ **+9.8%** ที่เคลมบน Binance spot, PF 1.59, MDD 3.87% →
  **caveat ข้อ 1 ของ BTC certificate (proxy data) ปิดได้แล้ว**
- **XAU รับรองบนข้อมูล venue ตัวเองไม่ได้** — OKX เปิด XAU-USDT-SWAP
  2025-04-09 มีแค่ 1.29 ปี สั้นเกินจะทำ walk-forward
- **สมมติฐาน gold-token proxy ผ่านการวัดแล้ว** — correlation ราคา **1.00**
  return **0.99** เมตริกต่างกันในระดับ noise (PF 1.42 vs 1.39) →
  **วิธีการใช้ proxy ของ certificate ถูกต้อง** ที่ผิดคือ config ที่มันวัด
- **ชุดข้อมูลที่สามยืนยันว่า config XAU ที่ deploy อยู่ marginal** — XAUT-USDT
  4.02 ปี: **PF 1.17, MDD 17.66%** รวมกับ PAXG 6 ปี (PF 1.28) และ swap จริง
  1.29 ปี (PF 1.42) → สามชุดข้อมูลเห็นตรงกันว่าอยู่ราว **PF 1.2 ไม่ใช่ 2.00**
- **กับดักที่ P0.2 เกือบพาไปเอง:** ถ้า validate บนข้อมูล venue จริงอย่างเดียว
  (1.29 ปี) จะได้ PF 1.42 / MDD 9.4% / Sharpe 1.27 ซึ่ง **สวยเกินจริง** เพราะ
  2025-04 ถึงตอนนี้เป็นช่วงทองขาขึ้น proxy ที่ยาวกว่าคือสิ่งที่กันความผิดพลาดนี้ →
  **proxy ไม่ใช่ของชั่วคราวที่ต้องเลิกใช้ แต่เป็นตัวเดียวที่มีประวัติพอจะซื่อสัตย์ได้**
- **พบว่า backtest path ของ XAU พังอยู่:** `data_source: okx` กับ
  `backtest_data_proxy: PAXGUSDT` **ใช้ร่วมกันไม่ได้** (OKX ไม่มี PAXG-USDT-SWAP
  → error 51001) ก่อน P0.1 มันพังเงียบแล้วใช้ cache เก่า
  **เสนอให้เปลี่ยน proxy เป็น OKX `XAUT-USDT`** — 4.02 ปี (3 เท่าของ native),
  venue เดียวกัน, correlation วัดแล้ว 0.99/1.00 (ยังไม่แก้ให้ เพราะแตะ config
  ของคู่ที่มี certificate → เป็นงานของ P0.3)
- **แก้จุดพังเงียบที่สอง:** Binance Global บล็อกภูมิภาคนี้ (HTTP 451 / 200 +
  error object) แล้ว `download_klines` คืน frame ว่าง — "venue ไม่ให้ข้อมูล" กับ
  "เหรียญนี้ไม่มีประวัติ" แยกกันไม่ออก ตอนนี้ raise เมื่อได้ 0 แท่งและรู้สาเหตุ
  แต่ยังคืน partial data ถ้าพลาดกลางทาง และคืน frame ว่างเงียบ ๆ ถ้าไม่มีจริง

**P0.3 — ตัดสินว่าจะทำอย่างไรกับ config XAU ที่ยังไม่มี certificate**
🟡 **ตัดสินใจแล้ว 2026-07-26: เลือกทางที่ไม่อยู่ในตารางเดิม —
D1 กั้น short ปล่อย long** (`L:D1ปิด S:D1เปิด`) เจ้าของสั่งเปิด หลังเห็นตัวเลข
ครบทั้ง 6 configuration

**แก้แล้วในรีโป (ยัง deploy ไม่ได้เอง — VPS ต้องทำมือและรอ position ว่าง):**
`coin_whitelist.json` + `bot_config.yaml` → `use_d1_regime_filter: true`,
`use_d1_regime_filter_long: false`

**ยืนยัน parity เป๊ะ:** รัน replay จาก config ที่ resolve จริง (ไม่มี override)
ได้ n=216, PF 1.3791, net 56.539%, MDD 14.4189%, Sharpe 0.892, Calmar 0.827 —
ตรงกับตัวเลขที่วัดไว้ทุกตัว จึงพิสูจน์ได้ว่า config ที่จะรัน = config ที่ทดสอบ

**บันทึกตามตรง — config นี้ไม่ได้รับการรับรอง:**
ตกเกณฑ์ `backtest.acceptance` −7.87pp และ `long-only + D1 on` ยังชนะด้าน
PF (1.96 vs 1.38) และ MDD (9.22% vs 14.42%) → เป็นการตัดสินใจของเจ้าของที่
ยอมรับ trade-off นี้ **ห้ามเรียกว่า certified และห้ามแก้ `min_profit_edge_pp`
ให้ผ่าน** เพราะเกณฑ์นั้นคือบาร์ที่กำหนดไว้ล่วงหน้าซึ่งงานทั้งชุดนี้ยืนอยู่บนมัน

**เหตุผลที่ดีกว่าของเดิมที่รันอยู่ (ทุกแกน):**
PF 1.17→**1.38**, net 31.04→**56.54%**, MDD 17.66→**14.42%**, Sharpe 0.51→**0.89**
และเป็น cell ที่แข็งที่สุดในขาลงปัจจุบัน **+11.22%** (ซื้อถือ −23.21%)

**ที่ยังเหลือ:** รันโปรโตคอลรับรองให้ config นี้เมื่อมีข้อมูลขาลงมากพอ
(ตอนนี้ bear = 1 เดือนเต็ม) — ตามหลักที่ P1.4 จะบังคับ

ทำแล้วส่วนเอกสาร ✅: commit เอกสารรับรอง + PDF, ถอนป้าย SUPERSEDED ที่ติดผิด,
ใส่กล่องเตือนเรื่องป้าย "live config" พร้อมสคริปต์ทำซ้ำแบบ copy-paste ได้

**อัปเดต 2026-07-26 — regime attribution เปลี่ยนภาพของข้อนี้**
(`docs/research/xau_regime_attribution_2026-07-26.md`,
`scripts/xau_phase_breakdown.py`)

คำถาม "มันทำคะแนนดีในช่วง bear ควรได้ certificate ไหม" — **คำตอบคือใช่ในทิศทางนั้น**
แต่ไม่ใช่ certificate แบบที่ PDF เคลม:

| regime | ผู้ชนะ | ส่วนต่าง (compounded) |
|---|---|---|
| bull (37 เดือน) | long-only + D1 | **+48.06pp** |
| bear (1 เดือน) | **long+short** | +2.69pp |
| sideways (2 เดือน) | **long+short** | +19.09pp |

**ช่วงขาลงที่กำลังเกิดจริง** (ทองพีค 5340 ก.พ. → 4072 มิ.ย. = **-22.9%**):
long+short D1 ปิด **+8.75%** / long-only+D1 **−4.74%** / **ซื้อถือทองเปล่า -22.88%** →
deployed ชนะซื้อถือประมาณ **40 percentage points** และเดือน 2026-06 cert
**ไม่เทรดเลย** (D1 filter กันออก) ขณะที่ deployed ทำ +10.69% **นี่คือ alpha จริง**

**สิ่งที่ "ไม่โอเค" จึงแคบกว่าที่เคยสรุป — ไม่ใช่ "กลยุทธ์นี้ขาดทุน":**
1. **PDF วัด config อื่น** (PF 2.00/MDD 8.3% = long-only+D1) — ข้อบกพร่องอยู่ที่
   เอกสาร ไม่ใช่กลยุทธ์
2. **ตลอด 48 เดือน แพ้การซื้อถือทองเปล่าทั้งคู่** (+39.37% / +69.22% vs **+121.86%**) —
   นี่คือข้อโต้แย้งที่หนักที่สุดต่อการให้ certificate แบบ all-weather
3. **จุดอ่อนอยู่ที่ bull ซึ่งครอง 90% ของประวัติทอง** — 4 เดือนที่แย่ที่สุดของ
   deployed เป็น bull ทั้งหมด (-12.01%, -11.53%, -7.02%, -6.44%) + turnover
   533 vs 207 เทรด = ค่าธรรมเนียม/slippage ~2.6 เท่า
4. **bear มีตัวอย่าง 1 เดือน** — ออก certificate ด้านนี้ด้วยสถิติล้วนไม่ได้

**อัปเดต 2026-07-26 (รอบสอง) — ทดสอบ 2×2 แล้ว**
(`docs/research/xau_d1_short_matrix_2026-07-26.md`, `scripts/xau_d1_short_matrix.py`)

สอง config เป็นคู่เสริมกันจริง แต่ **ไม่ต้องใช้ regime router** เพราะ
`xauby_actionzone` มี regime gate แยกทิศทางในตัวอยู่แล้ว (long ต้องการ D1
GREEN/YELLOW/ORANGE, short ต้องการ RED/BLUE/LBLUE) → คอมบิเนชัน
**long+short + D1 เปิด** เป็น config ที่ระบบรองรับอยู่แล้วและไม่มีใครวัดมาก่อน:

| variant (ต่อเนื่อง 4.02 ปี) | n | PF | net% | MDD% | Sharpe |
|---|---|---|---|---|---|
| long-only D1 ปิด | 155 | 1.63 | 64.41 | 11.12 | 1.10 |
| long-only D1 เปิด (cert) | 110 | **1.96** | **72.86** | **9.22** | **1.35** |
| long+short D1 ปิด (**deploy อยู่**) | 296 | **1.17** | 31.02 | **17.66** | 0.51 |
| long+short D1 เปิด (**candidate**) | 171 | 1.51 | 64.56 | 12.20 | 1.07 |
| *ซื้อถือทองเปล่า* | — | — | *136.68* | *25.11* | — |

**กลไกที่พัง:** เทียบแถว D1 ปิด → long-only PF 1.63 แต่ long+short PF 1.17
**การ short โดยไม่มี D1 ยืนยัน** คือตัวทำลาย (short ยิงใส่ขาขึ้น ตรงกับที่เดือน
แย่สุดเป็น bull ทั้งหมด) เปิด D1 แล้ว PF กลับมา 1.51 โดยยังเก็บ alpha ขาลงไว้ →
**ฝั่ง short ไม่ใช่ปัญหา การ short โดยไม่ยืนยันคือปัญหา**

**อัปเดต 2026-07-26 (รอบสาม) — รันโปรโตคอลรับรองแล้ว**
(`docs/research/xau_certification_2026-07-26.md`, `scripts/certify_xau_candidate.py`)

**candidate ตกเกณฑ์** `backtest.acceptance` กำหนดไว้ล่วงหน้าว่า long+short ต้อง
net บวก **และ** ชนะ long-only ที่เทียบเท่า ≥ `min_profit_edge_pp: 5.0`:

| long+short | net% | long-only | net% | edge | ผล |
|---|---|---|---|---|---|
| D1 เปิด (candidate) | 64.59 | D1 เปิด | 72.86 | **−8.27pp** | ❌ |
| D1 ปิด (deploy อยู่) | 31.04 | D1 ปิด | 64.41 | **−33.36pp** | ❌ |

cross-check ด้วย simulator อิสระบน **XAU-USDT-SWAP จริง ~1 ปี**: edge **+4.15pp**
→ ตกอีก (ต้องการ 5.0) สองชุดข้อมูล สอง simulator ผลตรงกัน

**ตัวที่ผ่าน: `long-only + D1 ON`** — PF 1.96 / MDD 9.22% / Sharpe 1.35 /
Calmar 1.60, WFA 25/40 เดือนบวก, เดือนแย่สุด −3.36%
bootstrap median +68.65% (90% CI +29%..+125%) — **แต่ P(profit) ~100% อ่านเป็น
หลักฐานความปลอดภัยไม่ได้** เพราะ resample จากหน้าต่างที่เป็น bull 37/40 เดือน
มันวัดความผันผวนของการสุ่ม *ภายใน regime mix เดิม* ไม่ได้ทดสอบขาลงยาว

**สิ่งที่ต้องแลก:** ช่วงขาลงปัจจุบัน long-only+D1 ได้ **−4.74%** ขณะที่
long+short D1 ปิดได้ +8.75% และตัวที่ deploy อยู่ +11.22% — ทิ้งของจริงไป
แต่การเอาหน้าต่าง 4 เดือนมาล้ม
เกณฑ์ 4 ปีที่กำหนดไว้ก่อน **คือกลไกเดียวกับที่ทำให้ certificate ก.ค. ผิด**
ถ้าเห็นว่าเกณฑ์เอง (วัดแต่ผลตอบแทนดิบ) ไม่เหมาะกับส่วนที่ทำหน้าที่ hedge →
**เปลี่ยนเกณฑ์เป็นการตัดสินใจเชิงนโยบาย ต้องเถียงและ commit ก่อนรันใหม่**
ไม่ใช่เปลี่ยนเพื่อให้ผลลัพธ์นี้หายไป (และเปลี่ยนไปก็ไม่ช่วย config ที่ deploy อยู่:
long-only+D1 ชนะ candidate ทั้ง MDD 9.22 vs 12.20 และ Calmar 1.60 vs 1.09)

**ข้อเท็จจริงที่ต้องยอมรับ: ทั้ง 4 config แพ้การซื้อถือทองเปล่าด้านผลตอบแทนดิบ**
(ดีสุด 72.86% เทียบ 136.68% = เก็บได้ 53%) 3 ใน 4 ชนะเมื่อปรับความเสี่ยง
(Calmar 1.60/1.20/1.09 vs 0.95) — config ที่ deploy อยู่เป็นตัวเดียวที่**แพ้ทั้งสองด้าน**
และ `max_leverage: 1` แปลว่าไม่มีช่องอัดเลเวอเรจเพื่อปิดช่องว่างผลตอบแทน

**ที่ยังต้องตัดสิน** — ทางเลือก (ตัวเลข MDD 29.2% มาจาก PAXG 6 ปี; บน XAUT 4 ปี
ซึ่งเป็น venue จริงคือ MDD 17.66% ซึ่ง **ไม่เกิน** รั้ว 25%):

| ทางเลือก | ผลกระทบ |
|---|---|
| **A. เปลี่ยน live เป็น long-only + D1 ON** | ไปอยู่บน config ที่ 2 งานวิจัยยืนยันตรงกัน (PF 2.00, MDD 7.6%) **แต่ตอนนี้ตลาดเป็นขาลง ซึ่งเป็นช่วงที่ config นี้แพ้ชัดเจน** (มิ.ย. ไม่เทรดเลย) — สลับตอนนี้คือสลับเข้าจุดอ่อนของมันพอดี |
| **B. คง config เดิม แล้วรับรองมันให้จบ** | รันโปรโตคอลเต็ม (WFA + bootstrap) บน config ที่ deploy จริง — **ตอนนี้มีน้ำหนักขึ้นมาก**: บน venue จริง MDD 17.66% ไม่เกินรั้ว และมันกำลังทำเงินในขาลงนี้ (+8.75% vs ซื้อถือ -22.88%) |
| **C. ลด XAU เป็น sim ชั่วคราว** | หยุดความเสี่ยงทันที **แต่ต้นทุนสูงขึ้น**: จะพลาดช่วงที่กลยุทธ์นี้ทำงานได้ดีที่สุดพอดี |
| **D. คงไว้ + เขียนรับทราบความเสี่ยงเป็นลายลักษณ์อักษร** | เร็วที่สุด — และตอนนี้ป้องกันได้ดีกว่าเดิม เพราะ MDD บน venue จริง (17.66%) อยู่ใต้รั้ว 25% |
| ~~**E. เปิด regime router ให้ XAU**~~ | ❌ **ตกไปแล้ว — พิสูจน์แล้วว่าไม่จำเป็น** router map regime → **strategy id** (`validate_mapping` เช็คกับ `available_strategies()`) จึง route ระหว่าง *config* ของ strategy เดียวกันไม่ได้ และไม่ต้องทำ เพราะ `xauby_actionzone` มี regime gate แยกทิศทางอยู่ในตัวแล้ว |
| ~~**F. เปิด `use_d1_regime_filter` คงฝั่ง short**~~ | ❌ **ตกเกณฑ์รับรอง** ชนะ config ที่ deploy 7/8 ตัวชี้วัดจริง แต่ **ไม่ผ่าน acceptance gate** (edge −8.27pp ต้องการ ≥ +5.0pp) |
| ~~**G. D1 กั้น long ปล่อย short** (per-side)~~ | ❌ **ทดสอบแล้วตก** PF 1.23 (อันดับ 5/6) edge **−35.08pp** แย่ที่สุดในทุก cell และ **แพ้ที่จุดประสงค์ของตัวเอง**: ช่วงขาลงได้ +4.12% เทียบตัวกลับข้าง +11.22% ตัวกลับข้าง (กั้น short ปล่อย long) ดีกว่าทุกแกน → **D1 สำคัญกับฝั่ง short มากกว่า long** (`xau_per_side_d1_test_2026-07-26.md`) |
| **A′. long-only + D1 ON** ⭐ **ผ่านโปรโตคอล** | ตัวเดียวที่รอด: PF **1.96**, MDD **9.22%**, Sharpe **1.35**, Calmar **1.60** — ชนะทุกแกนความเสี่ยง WFA 25/40 เดือนบวก เดือนแย่สุด −3.36% (คือ option A เดิม แต่ตอนนี้มีหลักฐานบน venue) |

ไม่ว่าเลือกทางไหน ต้องทำเพิ่ม:
1. commit ข้อมูลดิบ (JSONL/CSV) + สคริปต์ของงานวิจัย 13 ก.ค. — ตอนนี้มีแต่ตัวเลข
   สรุป ทำซ้ำจากศูนย์ไม่ได้
2. commit OKX cross-check ที่ PDF อ้างถึง — หลักฐานชิ้นเดียวที่ผูกงานวิจัยเข้ากับ
   venue จริง และยังไม่อยู่ในรีโป
3. บันทึกว่าพารามิเตอร์ XAU แต่ละตัวอ้างอิง certificate ฉบับไหน

*(P1.4 — certification pipeline — ต้องบังคับให้ certificate แนบ **resolved strategy
config** มาด้วยเสมอ นั่นคือสิ่งเดียวที่จะกันเคสนี้ได้: ป้ายผิดรอดมา 2 สัปดาห์เพราะ
รายงานไม่เคยบอกว่ามันวัดอะไร)*

**P0.4 — เปิด durable events**
ตั้ง `observability.durable_high_frequency_events: true` เป็นเงื่อนไขจำเป็นของทุก
ข้อสรุปเรื่อง parity หลังจากนี้ ทำพร้อมกับนโยบาย retention/rotation ใน change
เดียวกัน — `core/logs/events/` โตเร็วบน VPS 2 GB

**P0.5 — Short-side replay parity** ✅ **ทำแล้ว 2026-07-26**
เร่งขึ้นเพราะ config ใหม่ของ XAU มีหัวใจอยู่ที่ฝั่ง short พอดี พบว่าไม่ใช่ 1 บั๊ก
แต่เป็น **4 บั๊กแยกกัน**:

1. `signal_evaluated` ส่งแค่ `action` ซึ่ง**กำกวม**: `open_short` กับ "ปิด long"
   เป็น `SELL` เหมือนกัน, `close_short` กับ "เปิด long" เป็น `BUY` เหมือนกัน
   (`Signal` มี `intent`/`position_side` อยู่แล้ว แต่ไม่เคยถูกส่งออก)
2. การเทียบใช้ `action` อย่างเดียว → **"live เปิด short, replay ปิด long" นับเป็น
   MATCH** ซึ่งซ่อนความต่างจริงไว้
3. `ReplayValidationState` ไม่เคยเก็บว่าฝั่งไหนเปิดอยู่ และ `MarketContext` ที่สร้าง
   ใหม่ไม่เคยได้รับ `position_side` → strategy replay ทุก tick ของ short
   **ราวกับว่ามี long เปิดอยู่**
4. การเช็ค SL ใช้ `price <= stop_loss` ซึ่งถูกสำหรับ long แต่**กลับด้านสำหรับ short**
   (stop ของ short อยู่*เหนือ*ราคาเข้า) → short ที่กำลังกำไรถูกนับว่าโดน stop

แก้ครบทั้ง 4 + long `position_opened` ส่ง `position_side="LONG"` ชัดเจนแล้ว
(เดิม short ส่ง แต่ long ไม่ส่ง ต้องเดาจากการไม่มีค่า) เทสต์ใหม่ 21 ตัวใน
`tests/test_replay_short_parity.py` — event เก่าที่ไม่มีฟิลด์ใหม่จะ degrade ไปเทียบ
แบบ action อย่างเดียว ไม่ report mismatch ปลอมย้อนหลัง

**P0.6 — Dead-man's switch** ✅ **ทำแล้ว 2026-07-27**

`scripts/deadman_switch.py` + `deploy/systemd/xauby-deadman@.{service,timer}`

ยืนยันว่า timer ตกหล่นจริง: `xauby-healthcheck.service/.timer` **มีอยู่ในรีโป**
แต่ loop ใน `install_saas_host.sh` ไม่ได้ copy และ `systemctl enable` ก็ไม่ได้ใส่
→ เครื่องที่ติดตั้งใหม่จะ**ไม่มีการเฝ้าระวังจากภายนอกเลย** แก้แล้วทั้งสองจุด

และ healthcheck เดิมตรวจแค่ URL สาธารณะ (frontend + control plane `/healthz`) →
**เขียวได้ทั้งที่ engine ตายสนิท** ทั้งยังไม่มีช่องแจ้งเตือน แค่ exit non-zero
ลง journald ที่ไม่มีใครเปิดดู

ตัวใหม่อ่าน state file ที่ engine แตะทุก tick แล้วเตือนเมื่อค่าอายุเกินเกณฑ์

**การตัดสินใจออกแบบที่สำคัญ:**
- **stdlib ล้วน ไม่ import `xauby` เลย** — ตัวตรวจที่ import แพ็กเกจจะตายไป
  พร้อมแพ็กเกจ ถ้า deploy พัง import มันจะ raise แล้วเงียบ ซึ่งแยกไม่ออกจาก
  "ปกติดี" — คือความล้มเหลวที่มันมีไว้จับพอดี (มีเทสต์บังคับ)
- **POST ตรงแบบ synchronous** ไม่ใช้ `TelegramNotifier` เพราะมัน queue ลง
  background thread — process แบบ one-shot อาจ exit ก่อน flush แล้วข้อความหาย
- **debounce + แจ้งตอนกลับมา** — timer 2 นาทีเจอ outage 1 วันจะยิง ~720 ข้อความ
  ตอนนี้เตือนครั้งแรก แล้วซ้ำทุก `--realert-sec` และแจ้ง 1 ครั้งเมื่อ engine ฟื้น
- **ไม่ผูกกับ `xauby-engine@%i.service`** (ไม่มี `After=`/`BindsTo=`/`PartOf=`)
  ถ้าผูกไว้ มันจะหยุดทำงานตอนที่ engine ตาย ซึ่งคือตอนที่ต้องการมันที่สุด
- **ไม่อยู่ใน `xauby-service-control`** ที่ control plane เรียกผ่าน sudo ได้ —
  ตัวเฝ้าระวังที่ระบบซึ่งถูกเฝ้าสั่งปิดได้ ไม่ใช่ตัวเฝ้าระวัง การปิดต้องใช้ root
  arm ต่อ tenant ตอน provisioning แทน

**state file หายไป = ถือว่าเงียบ** (path ผิดหลัง deploy / engine ไม่เคยขึ้น
ล้วนคือ "ไม่ได้ทำงาน") — 23 เทสต์ใน `tests/test_deadman_switch.py`

**P0.7 — เปิด API circuit breaker** ✅ **ทำแล้ว 2026-07-27**

arm แล้ว (`api_circuit_breaker_enabled: true`) พร้อมบล็อก `api_resilience`
ที่ระบุค่าจูนไว้ชัดเจน — rollback ยังเป็นการพลิก flag เหมือนเดิม

**แต่มันไม่ใช่แค่พลิก flag** `CCXTClient._call` เป็นคอขวดเดียวของ**ทุก**คอล CCXT
รวม `create_order` ด้วย → เบรกเกอร์ที่เปิดค้าง 60 วินาทีตอน venue กระตุก
**จะปฏิเสธคำสั่งปิด position ด้วย** และ XAU รันด้วย `disable_stop_loss: true`
คือบอทเป็น stop เอง การบล็อกคำสั่งขาออกจึงอันตรายกว่าไม่มีเบรกเกอร์

**กฎที่ตั้งไว้: เบรกเกอร์คุม read loop ไม่คุมคำสั่งซื้อขาย**
- `DEFAULT_ALWAYS_ALLOW` = `create_order` / `cancel_order` / `cancel_all_orders`
  ข้ามด่านเบรกเกอร์เสมอ และไม่รอ token (คอลอ่านยังรอได้ถึง `acquire_timeout`)
- แต่ยัง **record ผลลัพธ์** เข้าเบรกเกอร์ การข้ามด่านไม่ทำให้เบรกเกอร์ตาบอด
- ต่อขยายได้ทาง `api_resilience.always_allow` (เพิ่มได้ ลดไม่ได้)

**แก้พฤติกรรมการฟื้นตัวด้วย:** เดิม `record_success()` ปิดเบรกเกอร์เฉพาะจาก HALF
แปลว่าคำสั่งซื้อขายที่**สำเร็จ**ตอนเบรกเกอร์เปิดอยู่ จะไม่ปิดมัน → engine ตาบอด
ข้อมูลตลาดต่ออีก 60 วินาที ทั้งที่เพิ่งพิสูจน์แล้วว่า venue ติดต่อได้
ตอนนี้ OPEN → CLOSED เมื่อคอล critical สำเร็จ (มีแต่คอลที่ข้ามด่านเท่านั้นที่
สำเร็จตอน OPEN ได้ และ traffic จริงที่สำเร็จเป็นหลักฐานแข็งกว่า probe ของ HALF)

การจูน: engine tick ทุก 60 วินาที 2 คู่ → อัตราจริงต่ำกว่า 10/s มาก limiter
จึงไม่ควรกัดในการทำงานปกติ มีไว้รับ reconnect storm กับ backfill burst
25 เทสต์ (`tests/test_api_circuit_breaker_armed.py` + ของเดิม)

**เกณฑ์ผ่าน:** ไม่มี config ที่ `minimal_roi` กับ `partial_tp` ขัดกันเองหลุดผ่าน
startup ได้ และ `CLAUDE.md` อธิบาย exit ของ XAU ตรงกับที่โค้ดทำจริง · backtest ที่ชี้ไป
venue ที่ติดต่อไม่ได้ต้องพังแบบดัง ๆ · ทั้งสองคู่มี certificate บนข้อมูลที่ถูก venue ·
ฆ่า engine ทิ้งแล้วมีการแจ้งเตือนภายใน 10 นาที · มี live run ที่ผ่าน
`scripts/replay_validate.py` โดยรวม trade ฝั่ง short ด้วย

---

> ⚠️ **แก้ตัวเลข 2026-07-27** — ตัวเลขแบบ **รายเดือน/window ทั้งหมด** ใน Phase 0
> ถูกคำนวณใหม่ harness เดิมเทรด warmup 300 แท่งด้วย (slice มาแต่ไม่ส่ง
> `min_bars_override`) เดือนที่ติดกันจึงทับกันราวหนึ่งเดือนและตัวเลขบวมราวเท่าตัว
> **อันดับและข้อสรุปทุกข้อไม่เปลี่ยน** และ **ตัวเลข continuous ทั้งหมดไม่กระทบ**
> (เกณฑ์ −7.87pp, ตาราง PF/MDD, BTC certificate — ยืนยันด้วยการรันซ้ำแล้ว)
> ดู `xauby/backtest/walkforward.py` และ `scripts/xau_windowed_regen.py`

## Phase 1 — ทำ certification ให้ทำซ้ำได้ (สัปดาห์ 4–8)

นี่คือจุดที่ alpha กับ business เป็นงานเดียวกัน certification เป็นทั้งด่านวิจัยและ
แคตตาล็อกสินค้า — วันนี้มันคือ boolean ที่แก้ด้วยมือ 8 ตัวใน `xauby/saas/catalog.py`
ซึ่ง 6 ตัวยังเป็น `pending` หรือ `insufficient`

**P1.1 — ยก walk-forward เข้า library** ✅ **เสร็จ 2026-07-27**
`grep -rn "walk_forward" xauby/` เจอแค่คอมเมนต์เดียว ความสามารถ WFA ทั้งหมดอยู่ใน
สคริปต์ใช้แล้วทิ้ง 2 ไฟล์ที่ไม่มีเทสต์ (`scripts/btc_wfa_multi_strategy.py`,
`scripts/actionzone_wfa_sweep.py`) — ทั้งที่สองไฟล์นี้ผลิตงานวิจัยที่ดีที่สุดของรีโป
ทำให้เป็น API ระดับหนึ่งใน `xauby/backtest/` พร้อมเทสต์ และเรียกใช้เป็นด่านได้

- `xauby/backtest/walkforward.py` + 21 เทสต์ — `WindowSlice` ผูก "จำนวนแท่ง warmup"
  กับ "จำนวนแท่งที่ห้ามเทรด" ไว้ด้วยกัน และ `run_slice` ส่ง `min_bars_override`
  ให้เสมอ (ลืมไม่ได้เพราะไม่มีพารามิเตอร์ให้ลืม) ส่วน `resolve_variant` ปฏิเสธ
  variant ที่ตั้งค่าคีย์ใน control group ไม่ครบ
- `scripts/xau_harness.py` + 18 เทสต์ — ตาราง variant ชุดเดียวสำหรับงานวิจัย XAU
  ทั้งหมด และ `deployed_variant_name()` อ่านจาก config ว่าตัวไหนคือตัวที่รันจริง
  แทนการเขียนป้าย "deployed" ตายตัวไว้ในเอกสาร
- harness เดิมทั้งสาม (`certify_xau_candidate`, `xau_phase_breakdown`,
  `xau_d1_short_matrix`) รันผ่าน library แล้ว — รันซ้ำได้ตัวเลขเดียวกับ
  `xau_windowed_regen` ทุกตัว และตาราง continuous ยังตรงกับที่ตีพิมพ์
- `scripts/actionzone_wfa_sweep.py` **มีบั๊กเดียวกัน** (เจอตอนตรวจ ไม่ใช่ตอนเดา) —
  แก้ให้ `_run` รับ `skip_bars` เป็น positional แล้ว วัดผลกระทบจริงบน config ที่
  เอกสารแนะนำ: OOS net +47.78% → **+43.29%**, fold3 PF 1.07 → **1.50**,
  fold ที่กำไร 4/5 ทั้งสองแบบ, OOS PF 2.26 เท่าเดิม — **ข้อสรุปของเอกสารไม่เปลี่ยน**
  เพราะ window ของมันใหญ่กว่าส่วนที่ทับกันราว 13 เท่า (ต่างจาก window รายเดือนที่
  ส่วนทับใหญ่กว่าตัว window เอง) `scripts/btc_wfa_multi_strategy.py` ส่ง
  `min_bars_override` ถูกต้องอยู่แล้ว — BTC certificate ไม่กระทบ (รันซ้ำยืนยันแล้ว)
- **ยังเหลือ:** ทั้งสองสคริปต์ยังมี loop ของตัวเองไม่ได้ย้ายมาใช้ library และยังไม่ได้
  ทำให้ certification เรียกเป็นด่านอัตโนมัติ — อยู่ใน P1.4

**P1.2 — แก้ความซื่อสัตย์เชิงสถิติของ optimizer**
`backtest.optimizer` รัน `max_runs: 4` บน `max_bars: 300`
(`bot_config.yaml:619-621`) — มันเรียง Cartesian product ตามระยะห่างจาก baseline
แล้วสุ่มตรวจ 4 จุด ดีไซน์ OOS-split (`optimizer.py:54-130`) ถูกต้อง แต่ข้อมูลน้อยเกิน
ที่ 300 bars การแบ่ง 70/30 แทบไม่ผ่านเกณฑ์ขั้นต่ำของตัวเอง — เลือกเอาว่าจะเพิ่ม
budget แล้วรันนอก VPS เทรด (ตามข้อจำกัดใน `AGENTS.md`) หรือเลิกเรียกมันว่า optimization

**P1.3 — เพิ่มการทดสอบนัยสำคัญทางสถิติ**
ยังไม่มี bootstrap, Monte Carlo, trade-shuffle, deflated Sharpe หรือการแก้ปัญหา
multiple testing เลย ทั้งที่มี 17 กลยุทธ์ × grid search
`docs/agent-strategy-checklist-indicator.md:718` ระบุข้อนี้เป็นสิ่งที่ต้องมีอยู่แล้ว
และยังไม่ติ๊ก — ด้วยจำนวน candidate ขนาดนี้ นี่คือเส้นแบ่งระหว่าง certificate จริง
กับสิ่งประดิษฐ์จากการขุดข้อมูล

**P1.4 — ให้ certification pipeline generate แคตตาล็อก**
คือข้อเสนอแนะข้อ 4 ที่ยังค้างจาก `docs/audit_system_2026-07-21.md` ให้สคริปต์รัน
protocol จาก P1.1 ออก certificate ลง `docs/research/` แล้ว **generate** บล็อกใน
`catalog.py` — preset ที่ไม่ผ่านด่านจะถูกมาร์กว่า certified ด้วยมือไม่ได้อีก
พร้อมกันนี้ให้เพิ่มฟิลด์ maturity ให้ชัด ทุกวันนี้ `research` / `paper-test` /
production เดาเอาจาก tag ที่เป็น free text และ 16 จาก 17 plugin ยังเป็น `0.1.0`
โดยไม่มีอะไรกันไม่ให้ถูก whitelist ขึ้น live

**P1.5 — รวม metric สองชุดให้ตรงกัน**
`analytics/calculator.py` (live/UI) คำนวณ Sharpe ราย trade แบบไม่ annualize
เทียบกับ `initial_balance=1000.0` ที่ hardcode ไว้ และวัด drawdown แบบ
close-to-close ส่วน `backtest/metrics.py` annualize และมี Calmar/CAGR ด้วย
แปลว่า Sharpe บนแดชบอร์ด **เทียบกับ** Sharpe ใน backtest ไม่ได้ และไม่มีเทสต์ไหน
ยืนยันว่าสองอันนี้ตรงกัน ยิ่งกับคู่ที่รัน `disable_stop_loss: true` การวัด drawdown
แบบ close-to-close ยังซ่อน intra-trade excursion ซึ่งเป็นสิ่งที่ต้องเห็นที่สุดพอดี

**P1.6 — ปลุก self-audit กลับมา**
แก้ `audit_track_record` ให้รองรับหลายคู่ (`active_position` แยกตาม symbol,
query event แบบกรอง symbol) แล้วต่อเข้า scheduler จริง ๆ และ commit track record
รายเดือนจาก `track_record/generator.py` — หมายเหตุ `drawdown_pct` ยัง hardcode
เป็น `0.0` ที่ `generator.py:52` ให้เริ่มนับ track record อย่างเป็นทางการที่จุดย้ายมา
OKX เพราะการเปลี่ยนสัญลักษณ์ `XAUTUSDT` → `XAUUSDT` ทำให้ประวัติก่อนหน้าเทียบกันไม่ได้

**P1.7 — Parity ของ *ผลลัพธ์* ระหว่าง live กับ backtest**
`replay_validation.py` ตรวจแค่ว่าสัญญาณตรงกัน (และตัวมันเองไม่มีเทสต์)
`scripts/live_parity_report.py` ตรวจแค่ว่า config ตรงกัน — ยังไม่มีอะไรกระทบยอด
PnL, slippage และ fee ที่เกิดขึ้นจริง กับที่ simulator ทำนายไว้ในช่วงเวลาเดียวกัน
`tests/test_fixed_tp_backtest_parity.py` คือรูปแบบที่ถูกต้อง เพียงแต่ทำไว้ฟีเจอร์เดียว
ให้ขยายไปที่ trailing, partial TP, minimal-ROI, funding และ short
นี่ยังเป็นเอกสารที่น่าเชื่อถือที่สุดสำหรับโชว์ลูกค้าด้วย

**เกณฑ์ผ่าน:** `catalog.py` ถูก generate ไม่ใช่แก้มือ · ทุก preset ที่
`live_certified` สืบย้อนไปถึง walk-forward certificate บนข้อมูลที่ถูก venue ได้ ·
track record รายเดือนและรายงาน parity ออกอัตโนมัติ

---

## Phase 2 — ทำให้รับ tenant คนที่ 2 ได้จริง (สัปดาห์ 8–12)

สายงาน onboarding สร้างครบแล้ว — invite → provision → encrypted keys → TOTP →
Trade PIN → live gate พร้อมเทสต์ ~1,900 บรรทัด, AES-256-GCM envelope ที่ผูก AAD
กับ tenant/target, plaintext อยู่บน tmpfs เท่านั้น เหลือบั๊ก production 2 ตัวกับ
ปัญหาการเก็บกุญแจ 1 เรื่องที่ขวางอยู่

**P2.1 — แก้ `deploy/xauby-materialize-credentials`** (บล็อกอยู่ 2 เรื่องอิสระกัน)
- ไม่เคยเรียก `set_telegram_loader` ทำให้ `_telegram_env_lines`
  (`xauby/saas/supervisor.py:561`) เขียน `TELEGRAM_ENABLED="false"` ทุกครั้งที่
  systemd สั่ง start จะปิด per-tenant alert เงียบ ๆ ล้างงานของ `de4f69a` ทิ้งทุกครั้ง
  ที่รีบูต
- `materialize_credentials` return `None` เมื่อ tenant ยังไม่มี exchange key แล้วไป
  ชน `raise SystemExit(...)` ใน `ExecStartPre` → engine ของ tenant SIM-only
  สตาร์ตไม่ขึ้น ซึ่งคือสถานะเริ่มต้นของผู้ใช้ใหม่ทุกคนพอดี

**P2.2 — การเก็บกุญแจและ backup นอกเครื่อง**
master key (`/etc/xauby/control.env`), DB ที่เข้ารหัส และ backup
(`/var/lib/xauby/backups`, เก็บ 7 วัน) อยู่บน VPS เครื่องเดียวกันทั้งหมด
เครื่องหายเมื่อไหร่ = exchange connection ของทุก tenant หายถาวร ย้าย backup ออกนอก
เครื่องและเขียนขั้นตอนดูแลกุญแจให้ชัด — คอลัมน์ `key_version` มีอยู่แต่ไม่มีโค้ด
rotation เลย เลือกเอาว่าจะทำ rotation จริง หรือลบคอลัมน์ทิ้งเพื่อไม่ให้สื่อว่ามี
ความสามารถที่ไม่มี

**P2.3 — สแกน dependency/CVE และรัน linter ที่ตั้งค่าไว้แล้ว**
CI ไม่มี Dependabot, CodeQL, `pip-audit`/`npm audit`, SBOM หรือ scheduled run
`docs/security-saas-audit.md` ระบุข้อนี้เป็น residual risk ที่เปิดอยู่ตรง ๆ
ส่วน `ruff` ตั้งค่าไว้ใน `pyproject.toml` (`select = ["E","F","I","UP","B"]`)
แต่**ไม่เคยถูกเรียก** ข้อควรระวัง: `AGENTS.md` อนุญาตให้ agent merge PR ตัวเองได้เมื่อ
CI เขียว และ `main` คือสิ่งที่ engine เงินจริง pull ไปใช้ — CI จึงเป็นด่านเดียวระหว่าง
commit ของ agent กับเงินจริง มันควรจะเชื่อถือได้สมกับหน้าที่นั้น

**P2.4 — Audit ความปลอดภัยใหม่บนสถาปัตยกรรมปัจจุบัน**
`docs/security-saas-audit.md` (2026-07-09) audit `xauby/webui/server.py` ซึ่ง
**ถูกลบไปแล้ว** ทั้งข้อค้นพบและ "Minimum SaaS Deployment Baseline" ทั้งหมดในนั้น
อธิบายคอมโพเนนต์ที่ไม่มีอยู่แล้ว control plane ปัจจุบันยังไม่เคยถูก audit เทียบเท่า
รายการ residual risk เดิม (tenant RBAC, secret manager, plugin isolation) ยังเป็น
เช็กลิสต์ที่ถูกต้องอยู่ อีกเรื่อง: คำรับรอง "ปิดสิทธิ์ถอนเงินแล้ว" ที่โชว์ให้ลูกค้าเห็น
ถูกเขียนแบบไม่มีเงื่อนไข (`saas/app.py:1114`) — เป็นการที่ผู้ใช้กรอกเอง ไม่เคยถูก
ตรวจกับสิทธิ์จริงบน exchange

**P2.5 — รับ design partner ฟรี 2 ราย** ผ่าน invite flow ที่มีอยู่ แบบ SIM-only
capacity ตั้ง hardcode ไว้ (`max_users=3`, `max_active_engines=2` ใน
`xauby/saas/settings.py`) และนั่นคือขนาดที่ถูกต้องสำหรับเฟสนี้ อย่าเพิ่งเพิ่ม
ตั้งค่า SMTP ให้เรียบร้อยเพื่อให้ invite เลิกตกไปเป็นการส่งลิงก์ด้วยมือ
(`mailer.py:14`)

**เกณฑ์ผ่าน:** tenant ที่ไม่ใช่เจ้าของ provision ได้ ต่อ key ได้ รัน SIM ได้ 2 สัปดาห์
และยังได้รับ Telegram alert ต่อเนื่องหลังรีบูตเครื่อง

---

## Phase 3 — เริ่มขายจริง (Q4 — มีเงื่อนไขปลดล็อก)

**อย่าเริ่มก่อนครบเงื่อนไขนี้:** track record live ต่อเนื่อง ≥6 เดือนบน certificate
ที่ใช้ข้อมูลถูก venue · preset ที่ certified จาก pipeline ≥4 ตัว · design partner
2 รายใช้งานได้เองโดยไม่ต้องช่วย · ไม่มี incident ระดับ P1 ติดต่อกัน 60 วัน

จากนั้นเรียงลำดับ: **งานด้านกฎหมาย** (ToS, privacy, risk disclosure, data export,
ลบบัญชี — วันนี้ไม่มีสักอย่างใน `Website/`) → **billing** (ยังไม่มีโค้ด Stripe/plan/
entitlement เลยแม้แต่บรรทัดเดียว และการบังคับ entitlement ต้องอยู่ที่ engine/
exec-mode gate ไม่ใช่ที่ UI) → **capacity** ซึ่งเพดานจริงคือสถาปัตยกรรมแบบ VPS
เครื่องเดียว/เจ้าของคนเดียวที่ hardcode ไว้: อีเมลเจ้าของ, GitHub repo, Vercel
project/org ID และ IP production ฝังอยู่ทั้งใน `settings.py`, `.env.saas.example`
และสคริปต์ deploy ทั้งสองตัว ส่วน public signup ตอนนี้เป็น 404 ตายตัว
(`saas/app.py:409`)

---

## สิ่งที่ตั้งใจ "ยังไม่ทำ" (บันทึกไว้เพื่อไม่ให้หายเงียบ)

- **ระบบที่หลับอยู่ ให้หลับต่อ** — `regime_router_enabled` เปิดระดับ global แต่ทั้ง
  สองคู่ปิด gate ระดับ pair ไว้ ส่วน `regime_statistical_crosscheck`,
  `auto_mode_switch`, `regime_policy`, `macro_sentiment_guard`, `rebalance`,
  `use_fractional_kelly` สร้างเสร็จแล้วและปิดอยู่ทั้งหมด **ไตรมาสนี้อย่าเปิดสักตัว**
  ด้วยเหตุผลเฉพาะ 2 ข้อ: (ก) mapping ของ router ชี้ไปที่ `xauby_donchian_trend`
  และ `bbrsi_mean_reversion` ซึ่งเป็น `0.1.0` และไม่เคยเทรดจริง — เปิดคือส่งเงินจริง
  เข้ากลยุทธ์ที่ไม่มีประวัติ (ข) GMM crosscheck ปิดอยู่เพราะ *วัดแล้ว* ไม่ใช่เพราะลืม
  (`docs/regime_accuracy_report.md`: agreement 50.0% / 45.2% กับ rule-based
  classifier = เท่ากับโยนหัวก้อย) เอกสารฉบับนั้นเป็น artifact ที่แข็งแรงที่สุดในรีโป
  และข้อสรุปของมัน — *"ใช้ regime เพื่อกำหนดขนาดตามความผันผวน ไม่ใช่เพื่อทำนาย
  ทิศทาง"* — ถูกต้องแล้ว เก็บไว้อย่างนั้น
- **การแตก `_tick_body` / `execute_buy`** — `loop.py:1517-2522` เป็น method
  ~1,000 บรรทัดที่ถือทุก entry, exit, trailing และ force-close โดยมี broad
  `except Exception` 41 จุดในไฟล์เดียว (และอีก 35 จุดใน `orders.py`) และไม่มี unit
  test ตรง ๆ `docs/code-quality-audit-2026-07-12.md` เลื่อนงานนี้ไว้เพราะ "ใหญ่เกิน
  กว่าจะรีวิวได้อย่างปลอดภัย" ซึ่งยังเป็นคำตัดสินที่ถูกสำหรับคนทำคนเดียว — แต่มันคือ
  refactor ที่คุ้มที่สุดเมื่อ Phase 0/1 ให้ parity harness มาให้ refactor *เทียบกับ*
  **อย่าทำก่อนหน้านั้น**
- **กับดัก timezone ใน daily-loss guard** — `risk.py:498` เทียบ `now` แบบ
  tz-naive กับ `datetime.fromisoformat(closed_at)` ถ้าค่าที่ได้เป็น tz-aware จะ raise
  เข้า `except` ที่แค่ log ทิ้ง ทำให้ trade นั้นหลุดจากการนับผลขาดทุน ตอนนี้ยัง
  ปลอดภัยเพราะ `database/db.py:762,819,1002` เขียนแบบ naive ตามธรรมเนียม
  แต่มันคือ *ธรรมเนียม* ไม่ใช่การบังคับ ส่วนที่เป็นรูปธรรมกว่าคือเพดานแถว
  `limit=50`/`limit=200` และการนับเฉพาะ realized PnL (ขาดทุนที่ยังไม่ปิดไม่ถูกนับ
  เข้ารั้ว 6%)
- **PR ค้าง #9, #10, #14** — ทั้งสามเกิดก่อนสถาปัตยกรรมปัจจุบัน roadmap ใน #9
  อ้างอิง `xauby/webui` ที่ถูกลบไปแล้ว และ `xauby/marketdata` กับ
  `xauby/runtime/command_queue.py` ใน Phase 0/2 ของมันไม่เคยถูกสร้าง ปิดทิ้ง
  แล้วให้เอกสารฉบับนี้แทนที่ #9
- **`strategy_sandbox_strict: false`** — ปิดไว้ได้ตราบที่ plugin ทั้งหมดเป็นของเราเอง
  ให้เปิดเป็น `true` ก่อนรับ strategy จากภายนอก เรื่องเกี่ยวเนื่อง: `manifest.py`
  รองรับบล็อก `permissions` ต่อ plugin แต่ **ไม่มี plugin ไหนเลย** ที่มี
  `strategy.yaml` — permissions จึงเป็นแค่ของประดับในตอนนี้
- **`exchange_plugin_registry_enabled`** — เอกสารในรีโประบุเองว่า DEPRECATED และ
  ไม่ได้ gate อะไรแล้ว ลบ knob ทิ้งดีกว่าปล่อยให้มี knob ที่ตายแล้ว

---

## การตรวจสอบ

- **Phase 0:** `PYTHONPATH=. python3 -m pytest -q` ผ่านทั้งชุด · backtest ที่ตั้งค่า
  ไปยัง venue ที่ติดต่อไม่ได้ต้อง **พังแบบดัง ๆ** ไม่ใช่คืน frame ว่าง · ฆ่า engine บน
  VPS แล้วยืนยันว่ามีแจ้งเตือนมาทางช่องที่ engine ไม่ได้เป็นเจ้าของ ·
  `python scripts/replay_validate.py <run_id> --symbol XAUUSDT` คืนผลเปรียบเทียบ
  ที่มีข้อมูล รวมถึง trade ฝั่ง short
- **Phase 1:** รันสคริปต์ certification กับ preset ที่ live ทั้งสองตัว แล้ว diff บล็อก
  แคตตาล็อกที่ generate ออกมา เทียบกับ `xauby/saas/catalog.py` ปัจจุบัน — ความต่าง
  ที่เจอคือ bug ของ pipeline หรือ drift ของค่าที่แก้ด้วยมือ ทั้งสองกรณีต้องอธิบายให้ได้
  ก่อนจะเชื่อ pipeline
- **Phase 2:** provision tenant ทดสอบบน VPS รีบูตเครื่อง แล้วยืนยันว่า engine กลับมา
  พร้อม `TELEGRAM_ENABLED=true` และ tenant แบบ SIM-only สตาร์ตได้โดยไม่เจอ
  `SystemExit`
- **ตลอดทาง:** รัน `python scripts/scan_secrets.py --tracked --history` ก่อน push
  ทุกครั้ง และ **ห้าม**รัน `npm run build`, `pytest` เต็มชุด, optimizer หรือ backtest
  บน VPS ที่เทรดอยู่ — 1 vCPU / 2 GB แชร์กับ engine เงินจริง และ `CPUQuota=30%`
  เป็นเพดาน ไม่ใช่โควตาที่จองไว้ (`AGENTS.md`)

## หมายเหตุการทำงาน

- เอกสารนี้เป็นแผน ไม่ใช่การอนุมัติให้เปลี่ยน config หรือเปิด live SaaS โดยอัตโนมัติ
  การลงมือแต่ละข้อเริ่มเมื่อ phase นั้นได้รับอนุมัติ
- แต่ละข้อในแต่ละ phase ควรแยกเป็น branch และ PR ของตัวเอง ตาม `AGENTS.md`
  (หนึ่ง branch หนึ่ง agent, prefix `claude/*` หรือ `codex/*`)
- ข้อใดที่แตะ `coin_whitelist.json` หรือ `bot_config.yaml` ต้องไม่มี position ค้าง
  ก่อน deploy และต้อง restart แบบควบคุม (`scripts/controlled_restart_engine.sh`)
- เอกสารฉบับนี้แทนที่ roadmap ใน PR #9 (`docs/roadmap_webapp_saas.md`) ซึ่งเขียน
  ก่อนที่ `xauby/webui` จะถูกลบ และอ้างอิงคอมโพเนนต์ที่ไม่มีอยู่แล้ว
- **สภาพแวดล้อม agent:** Claude Code บนเว็บรันชุดเทสต์ไม่ได้ เพราะ container
  ไม่มี `pandas` / `pytest` ติดตั้งและไม่มี SessionStart hook ควรเพิ่ม hook ไว้ใน
  `.claude/settings.json` เพื่อให้ session บนเว็บ verify งานตัวเองได้ ไม่งั้นทุก agent
  session จะส่งงานโดยไม่ได้รันเทสต์ — ซึ่งสำคัญเป็นพิเศษเพราะ CI เป็นด่านเดียว
  ระหว่าง commit ของ agent กับ engine เงินจริง (P2.3)
