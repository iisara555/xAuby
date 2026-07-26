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

ผลกระทบ 3 ชั้น:
1. **`CLAUDE.md` อธิบายพฤติกรรมที่เกิดขึ้นไม่ได้** — เอกสารเขียนว่า "engine แบ่งขาย
   ครึ่งหนึ่งที่ +12% มาร์ก `trade_states.partial_tp_taken` แล้วปล่อยส่วนที่เหลือให้
   ออกตอน zone flip" ซึ่งไม่เคยเกิดขึ้นจริงแม้แต่ครั้งเดียว
2. **exit profile จริงคือ "ปิดเต็มจำนวนที่ +8%"** ไม่ใช่ "แบ่งครึ่งที่ +12% แล้วถือต่อ"
   — เป็นกลยุทธ์คนละตัวกันในทางปฏิบัติ
3. **ขัดกับ thesis ของ CDC-pure** — เหตุผลที่ปิด stop loss และให้ exit ตาม zone flip
   คือเพื่อ "ขี่" เทรนด์ยาว แต่ ladder ที่ cap ทุกไม้ไว้ที่ ≤8% (และลดเหลือ 5% หลัง 1 วัน,
   3% หลัง 3 วัน) ตัดขาบนออกหมด ซึ่งน่าจะอธิบายได้ส่วนหนึ่งว่าทำไม PnL live ยังบางมาก

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

### 2. เหตุผลที่รองรับ config ของคู่หลัก ไม่ได้ถูก commit ไว้ในรีโป

*(ข้อนี้แก้ตามที่เจ้าของชี้แจง — ประเด็นไม่ใช่ config ผิด แต่คือ provenance หาย)*

Config ที่ live อยู่ (`coin_whitelist.json`): XAU รัน `use_d1_regime_filter: false`,
`enable_short: true`, `allowed_sides: ["long","short"]` ซึ่งอิงงานวิจัยล่าสุดที่
**แบ่งช่วงต่าง ๆ ของตลาด** ตามที่เจ้าของระบุ

ปัญหาคือ **งานวิจัยชิ้นนั้นไม่ได้อยู่ในรีโป** — sweep ที่ commit ไว้ใน
`docs/research/actionzone_sweep_2026-07/` เป็น IS/OOS/5-fold ล้วน ๆ ทุกไฟล์
(`folds` มีแต่ metric ไม่มี label ช่วงตลาดเลย ส่วนคำว่า `regime`/`bear` ที่ grep เจอ
คือชื่อคีย์ config `use_d1_regime_filter` / `exit_on_bear_cross` ไม่ใช่มิติการแบ่งช่วง)

ผลคือเอกสารวิจัยฉบับเดียวที่ commit ไว้ — `docs/actionzone_config_search_2026-07.md`
— **สรุปตรงข้ามกับ config ที่ใช้จริง** โดยแนะนำ `use_d1_regime_filter: true` และ
long-only พร้อมตัวเลข "fold ที่กำไร 3/5 vs 0/5" และเรียก D1 filter ว่า "คันโยก
คุณภาพที่สำคัญที่สุดตัวเดียว" นี่คือสิ่งที่ auditor, agent ตัวถัดไป หรือตัวเราเองอีก 6
เดือนข้างหน้าจะอ่านเจอเป็นอย่างแรก และมีโอกาสสูงที่จะ "แก้กลับ" ให้ตรงเอกสาร

**นี่คือความเสี่ยงเชิงกระบวนการ ไม่ใช่ความเสี่ยงเชิงกลยุทธ์** และแก้ได้ด้วยการ commit
งานวิจัยใหม่ ไม่ใช่การเปลี่ยน config

ข้อสังเกตที่ต้องยืนยันกับเจ้าของ: งานวิจัยชิ้นใหม่ใช้ข้อมูลจากไหน ถ้ารันผ่าน
`backtest/service.py` ที่ตั้ง `data_source: okx` ไว้ จะได้ frame ว่างตามข้อ 1
แปลว่าน่าจะรันจาก CSV ที่ fetch ไว้ล่วงหน้า (Binance spot PAXG) เหมือน sweep เดิม —
ถ้าใช่ ข้อจำกัดเรื่อง venue/asset ในข้อ 1 ก็ยังครอบงานวิจัยชิ้นใหม่ด้วย

แยกอีกเรื่องหนึ่ง (ไม่เกี่ยวกับงานวิจัย): คู่นี้รัน `disable_stop_loss: true` ที่
`position_pct: 0.95` และ `fixed_tp_pct: 0.0` — ใช้ 95% ของ allocation โดยไม่มี
hard stop ไม่มี fixed TP การป้องกันเป็น soft ล้วน (trailing, partial TP ที่ +12%,
minimal_roi, drawdown guard) โค้ดซื่อสัตย์กับเรื่องนี้ (`engine/base.py:545`
`_warn_if_stop_loss_unsupported`) แต่นี่คือความเสี่ยง live เดี่ยว ๆ ที่ใหญ่ที่สุดในรีโป
และควรมีตัวเลขจากงานวิจัยชิ้นใหม่มารองรับอย่างเปิดเผย

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

**สรุป: ไตรมาสนี้ยังไม่ควรทำ billing** — preset ที่ certified 2 จาก 8, live trade
~19 ครั้ง, ~+11.5 USDT ตลอด 7 สัปดาห์ และงานวิจัยที่รองรับ config คู่หลักยังไม่ได้
อยู่ในรีโป สิ่งนี้คือ *คำสัญญา* ที่ขายได้ ไม่ใช่ *สินค้า* ที่ขายได้ การเก็บเงินตอนนี้
สร้างภาระผูกพันที่ฐานหลักฐานยังรับไม่ไหว

ทางที่ควรทำ: รัน pilot ฟรีกับ design partner แล้วใช้เวลาไตรมาสนี้เปลี่ยน
"ความสามารถทำวิจัย" ให้เป็น "certification pipeline ที่ทำซ้ำได้" — ซึ่งเป็นทั้ง
สินทรัพย์แกนกลางของสินค้า และเป็นเอกสารขายหลักไปพร้อมกัน

---

## Phase 0 — หันเครื่องมือวัดให้ตรงกับความจริง (สัปดาห์ 1–3)

ตราบใดที่การวัดยังผิดและความล้มเหลวยังเงียบ งานหลังจากนี้ไม่มีความหมาย

**P0.0 — ตัดสินว่า XAU จะใช้ `minimal_roi` หรือ `partial_tp` (ทำก่อนทุกข้อ)**

ตอนนี้ตั้งไว้ทั้งคู่ และ `partial_tp` แพ้ตลอด (ข้อค้นพบ 0) ต้องเลือก **หนึ่งอย่าง**:

- **ถ้าต้องการพฤติกรรมตามที่ `CLAUDE.md` เขียนไว้** (แบ่งครึ่งที่ +12% แล้วถือส่วนที่
  เหลือไปถึง zone flip): ต้องยกขั้นแรกของ ladder ให้สูงกว่า 12% หรือเอา key `"0"`
  ออกเพื่อให้ ladder เริ่มทำงานหลังจากนั้น (เช่น `{"1440": 15.0, "4320": 8.0}`)
- **ถ้าต้องการ cap ที่ +8% จริง ๆ ตามที่เกิดขึ้นอยู่**: ลบ `partial_tp_pct` /
  `partial_tp_fraction` ออกจาก config เพื่อไม่ให้เข้าใจผิดว่ามันทำงาน แล้วแก้
  `CLAUDE.md` ให้ตรงความจริง

**เพิ่ม startup guard** ใน `xauby/runtime/exits.py` (หรือจุดที่ validate strategy
config): ถ้า `partial_tp_pct > 0` และ rung แรกของ `minimal_roi` ต่ำกว่าหรือเท่ากับ
`partial_tp_pct` → ต้อง fail หรือ warn ดัง ๆ ตอน start เพราะเป็นการตั้งค่าที่
ขัดกันเองเชิงตรรกะ นี่เป็นรูปแบบเดียวกับ `validate_open_positions_config` ที่เพิ่ม
ไปแล้วตอน F-1 (audit 2026-07-21) — ปัญหาคลาสเดียวกันคือ "คีย์หลายตัวที่ผู้บริโภค
ต่างกันและไม่มีใครตรวจว่าสอดคล้อง"

เพิ่มเทสต์: parity test ที่ยืนยันว่า config ซึ่ง `minimal_roi` rung แรก > `partial_tp_pct`
ทำให้ `partial_tp_triggered` เกิดขึ้นจริงทั้งใน live path และ simulator
(`tests/test_partial_tp_live.py` + `tests/test_replay_parity.py` มีโครงอยู่แล้ว)

**ต้องไม่มี position ค้างก่อนเปลี่ยน** — การแก้ตาราง exit ระหว่างถือของไม่ปลอดภัย

**P0.1 — แก้ backtest data path**
เพิ่ม OKX klines ใน `xauby/backtest/data.py` — `scripts/fetch_okx_xau_history.py`
มีรูปแบบ `/api/v5` ที่ถูกต้องอยู่แล้ว ให้ reuse อย่าเขียน client ตัวที่สอง
ทำให้ `data_source` ที่ไม่รู้จัก **raise แทนที่จะ return frame ว่าง** — การพังเงียบ
แย่กว่าการไม่รองรับ venue เพิ่ม branch `okx` ใน `_source_tag_from_url`
(`data.py:21`) เพื่อไม่ให้ cache ของ OKX ชนกับ tag ของ Binance.TH และเพิ่ม
`backtest` เข้า `GUARDED_DIRS` ใน `tests/test_no_binance_coupling.py`

**P0.2 — Re-validate ทั้งสองคู่บนข้อมูล venue จริง**
หลัง P0.1 เสร็จ รัน BTC บนข้อมูล OKX swap และ XAU บน OKX XAU-USDT-SWAP ตรง ๆ
เก็บ PAXG proxy ไว้แค่เป็นส่วนต่อขยายประวัติช่วงก่อนเปิดเทรด และติดป้ายให้ชัด
**ถ้า certificate ใน `docs/research/` เอาตัวไม่รอดบนข้อมูลจริง นั่นคือสิ่งที่สำคัญ
ที่สุดที่จะได้เรียนรู้ในไตรมาสนี้**

**P0.3 — Commit งานวิจัยที่แบ่งช่วงตลาด แล้วมาร์กฉบับเก่าว่าถูกแทนที่**
*(งานเอกสาร ไม่แตะ config — ไม่ต้องรอ position ว่าง)*

**ไม่ต้องเปลี่ยน config XAU** — config ปัจจุบันมีเหตุผลรองรับอยู่แล้ว สิ่งที่ต้องทำคือ
ทำให้เหตุผลนั้นอยู่ในรีโป:
1. commit งานวิจัยที่แบ่งช่วงตลาดลง `docs/research/` พร้อมข้อมูลดิบ (JSONL/CSV)
   และวิธีการ ให้ระดับเดียวกับ sweep เดิม — อย่างน้อยต้องบอกได้ว่าแบ่งช่วงด้วยเกณฑ์
   อะไร แต่ละช่วงกินเวลาไหน และ config ปัจจุบันชนะ config long-only+D1 ในช่วงไหนบ้าง
2. เติมหัวข้อ "Superseded by" ที่ต้นเอกสาร
   `docs/actionzone_config_search_2026-07.md` ชี้ไปยังงานวิจัยใหม่ พร้อมวันที่ —
   เพื่อไม่ให้ใครอ่านฉบับเก่าแล้วไป "แก้กลับ"
3. บันทึกใน `coin_whitelist.json` (หรือเอกสารคู่กัน) ว่าพารามิเตอร์ XAU แต่ละตัว
   อ้างอิงงานวิจัยฉบับไหน

ถ้างานวิจัยใหม่รันบน CSV ชุด Binance spot PAXG (ตามข้อสังเกตในข้อ 2) ให้ระบุไว้
ในเอกสารตรง ๆ แล้วค่อยรันซ้ำบนข้อมูล venue จริงหลัง P0.2 เสร็จ — ผลอาจไม่เปลี่ยน
แต่ต้องรู้ว่าไม่เปลี่ยน ไม่ใช่เดาว่าไม่เปลี่ยน

อีกข้อที่ต้องเช็กกับงานวิจัยใหม่: มันสมมติว่า `partial_tp` ทำงานอยู่หรือไม่ ถ้าใน
สคริปต์วิจัยตั้ง `minimal_roi` ไว้ด้วย ผลที่ได้ก็จะเป็น "ปิดเต็มที่ +8%" เหมือน live
(ซึ่งดี — ตรงกัน) แต่ถ้าสคริปต์ไม่ได้ตั้ง `minimal_roi` ผลวิจัยจะสะท้อนกลยุทธ์
"แบ่งครึ่งที่ +12%" ที่ live ไม่ได้ทำ — กรณีหลังนี้ข้อสรุปของงานวิจัยจะไม่ใช้กับ
config ที่รันอยู่ ต้องยืนยันก่อนใช้อ้างอิง

*(หมายเหตุ: ข้อนี้ทำให้ P1.4 — certification pipeline — สำคัญขึ้น เพราะจุดประสงค์
หลักของมันคือผูก "พารามิเตอร์ที่ live" เข้ากับ "งานวิจัยที่รองรับ" แบบอัตโนมัติ
เพื่อไม่ให้ provenance หายแบบนี้อีก)*

**P0.4 — เปิด durable events**
ตั้ง `observability.durable_high_frequency_events: true` เป็นเงื่อนไขจำเป็นของทุก
ข้อสรุปเรื่อง parity หลังจากนี้ ทำพร้อมกับนโยบาย retention/rotation ใน change
เดียวกัน — `core/logs/events/` โตเร็วบน VPS 2 GB

**P0.5 — Short-side replay parity**
เพิ่ม `intent` และ `position_side` เชิงความหมายเข้า `signal_evaluated` และคืนค่า
position side เข้า `MarketContext` ตอน replay (`xauby/observability/replay.py`,
`replay_validation.py`) ก่อนงานนี้เสร็จ replay output ยังใช้เป็นหลักฐานฝั่ง short
ไม่ได้ — ซึ่งคือครึ่งหนึ่งของ exposure ที่ live อยู่

**P0.6 — Dead-man's switch**
ต้องมี heartbeat ภายนอกที่เตือนเมื่อ engine **เงียบ** ไม่ใช่เตือนเมื่อ engine error
ติดตั้ง `xauby-healthcheck.timer` ใน `install_saas_host.sh` (ตกหล่นจาก loop
ประมาณบรรทัด 38) และเพิ่มช่องทางแจ้งเตือนที่สองที่ไม่ได้ส่งโดย engine process
เคสทดสอบแรกคือ weekly review ที่หายไป

**P0.7 — เปิด API circuit breaker**
`architecture.api_circuit_breaker_enabled: true` — token-bucket limiter +
circuit breaker เขียนเสร็จและมีเทสต์แล้วใน `xauby/api/resilience.py` แต่ไม่ได้ arm
คอมเมนต์ในไฟล์เองบอกว่า rollback คือแค่พลิก flag

**เกณฑ์ผ่าน:** ไม่มี config ที่ `minimal_roi` กับ `partial_tp` ขัดกันเองหลุดผ่าน
startup ได้ และ `CLAUDE.md` อธิบาย exit ของ XAU ตรงกับที่โค้ดทำจริง · backtest ที่ชี้ไป
venue ที่ติดต่อไม่ได้ต้องพังแบบดัง ๆ · ทั้งสองคู่มี certificate บนข้อมูลที่ถูก venue ·
ฆ่า engine ทิ้งแล้วมีการแจ้งเตือนภายใน 10 นาที · มี live run ที่ผ่าน
`scripts/replay_validate.py` โดยรวม trade ฝั่ง short ด้วย

---

## Phase 1 — ทำ certification ให้ทำซ้ำได้ (สัปดาห์ 4–8)

นี่คือจุดที่ alpha กับ business เป็นงานเดียวกัน certification เป็นทั้งด่านวิจัยและ
แคตตาล็อกสินค้า — วันนี้มันคือ boolean ที่แก้ด้วยมือ 8 ตัวใน `xauby/saas/catalog.py`
ซึ่ง 6 ตัวยังเป็น `pending` หรือ `insufficient`

**P1.1 — ยก walk-forward เข้า library**
`grep -rn "walk_forward" xauby/` เจอแค่คอมเมนต์เดียว ความสามารถ WFA ทั้งหมดอยู่ใน
สคริปต์ใช้แล้วทิ้ง 2 ไฟล์ที่ไม่มีเทสต์ (`scripts/btc_wfa_multi_strategy.py`,
`scripts/actionzone_wfa_sweep.py`) — ทั้งที่สองไฟล์นี้ผลิตงานวิจัยที่ดีที่สุดของรีโป
ทำให้เป็น API ระดับหนึ่งใน `xauby/backtest/` พร้อมเทสต์ และเรียกใช้เป็นด่านได้

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
