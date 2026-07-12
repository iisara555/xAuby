# Roadmap: xAuby → 100% Webapp → Full SaaS

## เป้าหมายและขอบเขต

Roadmap นี้พา xAuby จาก trading system ที่ควบคุมหลักผ่าน CLI, Textual TUI และ Telegram ไปสู่ **webapp 100%** สำหรับผู้ดูแลคนเดียว แล้วต่อยอดเป็น **multi-tenant SaaS** โดยไม่ลดมาตรฐานด้าน risk isolation, observability และการป้องกัน exchange account

ขอบเขตของเอกสารคือแผนการทำงานและเกณฑ์ตัดสินใจเท่านั้น งานรอบนี้ **ไม่เปลี่ยน production code** การลงมือทำเริ่มเมื่อมีการอนุมัติ Phase ที่เกี่ยวข้อง

### ข้อจำกัดที่ออกแบบให้รองรับ

- VPS เริ่มต้น: **1 vCPU / 2 GB RAM** และขยายเป็น **2 vCPU / 4 GB RAM** เมื่อเข้าสู่ SaaS
- ต้องรักษาแนวคิด multi-strategy, multi-exchange และ multi-pair
- Live engine ต้อง isolate ต่อ tenant/account เพื่อจำกัด blast radius
- ค่า default ต้องปลอดภัย: simulation ก่อน live, router live-gate และ account lock ข้าม instance

## สถานะปัจจุบัน: สิ่งที่นำไปต่อยอดได้ทันที

โครงสร้างปัจจุบันมี seam สำคัญสำหรับ webapp และ SaaS อยู่แล้ว:

| พื้นฐานที่มีแล้ว | ความหมายต่อ roadmap |
|---|---|
| `xauby/webui/server.py` | WebUI แบบ stdlib มี password/session cookie (HMAC), bearer token, Google OAuth + email allowlist, CSP/security headers และ secret redaction อยู่แล้ว |
| `xauby/runtime/paths.py` | รองรับ `XAUBY_HOME`, `XAUBY_INSTANCE_ID`, `XAUBY_CONFIG_DIR` ทำให้แยก DB, logs, state และ locks ต่อ instance ได้ |
| Account lock ข้าม instance | ป้องกันสอง live engines ใช้ exchange account เดียวกันพร้อมกัน |
| File-IPC ที่พิสูจน์แล้ว | `manual_order_request.json` และ `telegram_control.json` แสดงรูปแบบ request → drain ใน `tick()` ที่มี expiry/claim semantics |
| Atomic config writers | `xauby/launcher/config_io.py` และ `xauby/runtime/pair_config.py` เขียน config/whitelist ได้อย่างปลอดภัยและ engine hot-reload ภายในราว 30 วินาที |
| Exchange plugin registry + CCXT adapter | การเพิ่ม exchange เป็นงาน config เป็นหลัก ไม่ใช่การแก้ core engine |

### ช่องว่างที่ต้องปิดก่อนเป็น SaaS

1. Control plane ยังอยู่ใน Telegram เป็นหลัก และยังไม่มี HTTP command API สำหรับ webapp
2. หนึ่ง process ยังผูกกับหนึ่ง tenant/exchange และยังไม่มี supervisor/orchestrator
3. ส่วนหนึ่งยังตั้งสมมติฐานว่าเป็น single user: `.env`, Telegram chat และ control file ชุดเดียว
4. `config_io.py` ยังมี path ที่อิง current working directory จึงต้องทำให้ instance-aware
5. RAM เป็นคอขวดหลัก: engine หนึ่งตัวคาดว่าใช้ประมาณ 150–300 MB RSS ทำให้ 1c/2GB รองรับ live engines ได้จำกัด

## Technical decisions

| เรื่อง | การตัดสินใจ | เหตุผล |
|---|---|---|
| Web server | คง `ThreadingHTTPServer` ถึง Phase 2; เพิ่ม FastAPI เฉพาะ control plane ใน Phase 3 | WebUI ปัจจุบันมี auth/hardening แล้ว และไม่ควรเพิ่ม RAM ราว 60–80 MB ต่อ process บน 2 GB โดยไม่จำเป็น |
| Live updates | ใช้ **SSE** ไม่ใช่ WebSocket | state ไหลทางเดียวจาก server ไป browser; คำสั่งใช้ POST; SSE ใช้กับ stdlib server และ reconnect ได้ง่าย |
| Command transport | ใช้ **File-IPC command queue** | รักษา invariant ว่า UI ไม่เรียก engine loop โดยตรง, crash-safe และขยายจาก pattern ที่มีอยู่ |
| Data store | SQLite-per-tenant + WAL ถึง Phase 3; Postgres เฉพาะ control plane ใน Phase 4 | หนึ่ง writer ต่อ tenant เหมาะกับ SQLite; backup/erase/migrate ต่อ tenant เป็นการจัดการไฟล์ชัดเจน |
| Secrets | Fernet + master key ที่ `/etc/xauby/master.key` (mode `0400`) | encrypt exchange keys; decrypt เฉพาะช่วง spawn engine; ไม่เพิ่มภาระ Vault บน VPS เล็ก |
| Frontend | คง vanilla JS สำหรับ operator dashboard; `Website/` (Next.js) ใช้ marketing/signup แบบ static export | ไม่เพิ่ม Node runtime แข่ง RAM กับ live tenants |
| Backtest / optimizer | คิวงานช่องเดียว, `nice -n 19`, systemd `CPUWeight=10`; free plan ไม่มี backtest | 1 vCPU ต้องให้ live tick มาก่อนงานวิจัย |
| Process model | process-per-**live**-tenant; shared process สำหรับ sim tenants เฉพาะเมื่อจำเป็นใน Phase 3.5 | live ต้อง isolate; sim สามารถรวมเพื่อลดต้นทุน RAM |

## Invariants ที่ทุก phase ต้องรักษา

- UI ไม่แตะ engine โดยตรง: ทุก write ไปผ่าน File-IPC หรือ config mutator
- Observability ไม่ import engine เพื่ออ่าน state
- Strategy plugin เป็น pure analyst และไม่ขยายสิทธิ์เข้าถึง system โดยไม่จำเป็น
- Default-to-sim, router live-gate และ account lock ข้าม instance ต้องคงอยู่
- API keys เป็น trade-only; ปฏิเสธ key ที่มี permission ถอนเงิน
- ทุก action ที่เปลี่ยน state ต้องมี audit trail และ command result ที่ตรวจสอบได้

---

## Phase 0 — Hardening & Preparation

**ระยะเวลาโดยประมาณ: 1–2 สัปดาห์**

เป้าหมายคือสร้างขอบเขต instance, command contract และ baseline ของ resource ก่อนให้ webapp ส่งคำสั่งได้

### งานหลัก

1. ทำ `xauby/launcher/config_io.py` และ `xauby/runtime/pair_config.py` ให้ใช้ `xauby/runtime/paths.py` แทน cwd-relative path
2. เพิ่ม `schema_version` ใน state JSON และ endpoint `/api/meta` เพื่อ freeze data contract ก่อนสร้าง frontend เพิ่ม
3. รวม `manual_orders.py` และ `telegram_control.py` เป็น `xauby/runtime/command_queue.py`
   - ใช้ JSONL inbox พร้อม `request_id`, `expiry`, HMAC signature และ claim state
   - ให้ `tick()` เป็นผู้ drain queue เพียงจุดเดียว
   - command v1: `pause`, `resume`, `manual_order`, `close_position`, `set_exec_mode`, `reload_config`
4. เพิ่ม systemd units ภายใต้ `deploy/systemd/`
   - `xauby-engine@<instance>.service`
   - `xauby-webui.service`
5. วัด RSS จริงของ engine และ WebUI เพื่อใช้ตัดสิน capacity แทนการประมาณ

### Exit criteria

- รันอย่างน้อย 2 instances พร้อมกันภายใต้ systemd ได้
- Config, database, logs, state และ locks ไม่ชนกัน
- Command queue มี expiry, claim และ audit ที่ตรวจสอบได้
- มี baseline RSS สำหรับ live engine, sim engine และ WebUI

---

## Phase 1 — 100% Webapp สำหรับผู้ดูแลคนเดียว

**ระยะเวลาโดยประมาณ: 2–4 สัปดาห์**

Phase นี้ตอบโจทย์ “webapp 100%” โดยให้ทุก operation หลักทำได้จาก WebUI แต่ยังคง TUI และ Telegram เป็น fallback ได้

### Write API ที่ปลอดภัย

เพิ่ม write API ใน `xauby/webui/server.py` โดย server เขียนได้เฉพาะ command queue หรือ config mutators — ห้ามเรียก engine โดยตรง

| Endpoint / capability | พฤติกรรมที่ต้องมี |
|---|---|
| `POST /api/control/pause` และ `resume` | เข้าคิว command, ส่งผล accepted/executed/rejected กลับมา |
| `POST /api/orders/manual` | confirmation ชัดเจน, expiry 120 วินาที, audit event |
| `POST /api/pairs/...` | ใช้ whitelist mutators และ hot-reload ภายใน ≤30 วินาที |
| `POST /api/pairs/{sym}/exec-mode` | sim ⇄ live พร้อม typed confirmation และตรวจ router live-gate |
| `POST /api/config` | allowlist เฉพาะ key ที่เปิดให้แก้ และใช้ `_edit_bot_yaml` |
| `GET /api/commands/{id}` | อ่าน command result จาก `command_results.jsonl` |
| `GET /api/stream` | SSE สำหรับ state/event update แทน polling scheduler |

### UI ที่เพิ่ม

- Pair management panel: pair, strategy, timeframe, execution mode และสถานะ hot-reload
- Config editor แบบมี allowlist, validation และ diff ก่อนยืนยัน
- Manual-order modal พร้อม confirmation และ feedback ของ command
- Sim/live toggle ที่ใช้ danger styling และ typed confirmation
- Toasts, command timeline และ audit event ที่อ่านง่าย

### Security และ deployment

- Write action ต้องใช้ session + CSRF header; bearer token เป็น read-only
- WebUI ทำงานเป็น systemd service หลัง Caddy/TLS
- Secret redaction และ CSP ที่มีอยู่ต้องครอบคลุม endpoint/หน้าใหม่

### Budget โดยประมาณบน 1c/2GB

| Component | เป้าหมาย RAM |
|---|---:|
| Live engine | 150–300 MB |
| WebUI | 40–60 MB |
| Caddy | ~30 MB |

### Exit criteria

- ใช้งานจริง 1 สัปดาห์โดยไม่ต้องใช้ Telegram สำหรับ normal operations
- ทุก action ปรากฏใน event log และมีผลลัพธ์ที่ตรวจสอบได้
- TUI/Telegram ยังทำงานได้โดยไม่แย่ง control path หรือทำ state เสียหาย

---

## Phase 2 — Multi-Instance บน host เดียว

**ระยะเวลาโดยประมาณ: 3–5 สัปดาห์**

เป้าหมายคือให้หลาย instances และหลาย exchange อยู่บน VPS เดียวได้อย่างควบคุมได้

### งานหลัก

1. ใช้ systemd เป็น process manager จริง และทำ orchestrator แบบบาง
   - instance registry: `~/.xauby/instances.json`
   - สั่งผ่าน `systemctl`
   - อ่าน heartbeat จาก state JSON
2. รองรับ route `/i/{instance}/api/...`, instance switcher และ `/api/instances` ใน WebUI
3. เพิ่ม shared market-data cache service ใน `xauby/marketdata/`
   - fetch candle/ticker ครั้งเดียวต่อ `(exchange, symbol, timeframe)`
   - เก็บ SQLite กลาง, engine อ่าน cache-first ผ่าน `market_data_source: shared|direct`
   - private endpoints แยกต่อ account เสมอ
4. แสดง account lock ใน WebUI และเพิ่ม tick-phase jitter เพื่อลด CPU spike พร้อมกัน

### Capacity ที่คาดหวัง (ต้องยืนยันด้วย Phase 0 measurement)

| VPS | Live engines | Sim engines (trimmed) |
|---|---:|---:|
| 1c / 2GB | 3–5 | 5–7 |
| 2c / 4GB | 8–12 | 12–16 |

### Exit criteria

- อย่างน้อย 3 instances และอย่างน้อย 2 exchanges ทำงานต่อเนื่อง 2 สัปดาห์
- การหยุด instance หนึ่งตัวไม่กระทบอีกตัว
- cache ลด duplicate market-data calls โดยไม่ลด freshness ของ live decisions

---

## Phase 3 — SaaS

**ระยะเวลาโดยประมาณ: 6–10 สัปดาห์ (แบ่ง 3a/3b)**

ก่อนเริ่ม Phase นี้แนะนำให้ขยายเป็น **2 vCPU / 4 GB RAM** เพื่อรองรับ control plane, tenants และงาน operational ที่เพิ่มขึ้น

### Phase 3a — Tenancy & Onboarding

- เพิ่ม control plane ด้วย FastAPI + uvicorn (1 worker) สำหรับ users, tenants, plans และ encrypted exchange keys
- เก็บ key ด้วย Fernet; decrypt เฉพาะตอน spawn engine และส่งผ่าน systemd `LoadCredential=`
- sandbox ต่อ tenant: `DynamicUser=`, `MemoryMax=350M` และ filesystem/service permissions ขั้นต่ำ
- Onboarding flow: signup → validate trade-only key → sim → live หลังผ่าน gates
- Plans:
  - **Free:** sim-only, ไม่เกิน 2 pairs, ไม่มี backtest
  - **Paid:** live ได้ และใช้ backtest ตาม quota
- การบังคับ plan ต้องอยู่ใน engine/exec-mode gate ไม่ใช่ UI อย่างเดียว

### Phase 3b — Billing & Operations

- Stripe Checkout + webhooks
- downgrade ต้อง force-sim พร้อม grace period และห้าม silent-close live position
- nightly `sqlite3 .backup` ต่อ tenant ไป object storage ผ่าน rclone
- metrics ส่งไป Grafana Cloud free tier แทนการรัน Prometheus ในเครื่อง
- ทำ security review ก่อนรับลูกค้า live รายแรก

### Phase 3.5 — ทางเลือกเมื่อ free tier โต

หาก simulation tenants ใช้ RAM มากเกิน budget ให้สร้าง multi-tenant sim engine โดยรวมหลาย portfolios ใน 1–2 processes แต่ไม่ใช้แนวทางนี้กับ live tenants

### Exit criteria

- ผู้ใช้ใหม่ signup → ใส่ key → simulation → upgrade → live ได้โดยไม่ต้อง SSH
- tenant crash ไม่กระทบ tenant อื่น และพิสูจน์ isolation ได้
- billing, quotas, audit และ backup มี runbook ที่ทดสอบแล้ว

---

## Phase 4 — Scale-out หลาย VPS

**เริ่มเมื่อ live tenants มากกว่าประมาณ 10 ราย**

- Control-plane node: Postgres เฉพาะ users, billing และ instance registry
- Worker nodes: orchestrator + engines + market cache เหมือนกันทุก node
- Trade data คงเป็น SQLite-per-tenant บน worker; ไม่ centralize trade database
- ย้าย tenant ด้วย stop → copy `XAUBY_HOME` → start; position อยู่ที่ exchange ไม่ใช่ในไฟล์ local
- เชื่อม nodes ด้วย WireGuard หรือ mTLS
- placement ใช้ RSS measurement เป็นหลัก (bin-packing) ไม่ใช้จำนวน tenants แบบตายตัว

## ตาราง capacity สำหรับการวางแผน

| VPS | Live engines | Sim engines (trimmed) | SaaS ที่ใช้งานจริง |
|---|---:|---:|---|
| 1c / 2GB | 3–4 | 5–7 | 2–3 paid live + sim เล็กน้อย |
| 2c / 4GB | 8–10 | 12–16 | ~8 paid live + free-tier sim |
| + Phase 3.5 shared sim | — | 30–100 portfolios / 1–2 processes | รองรับ free tier ในระดับจริง |

ลำดับคอขวดที่ต้องจัดการ: **RAM → exchange rate limit → CPU**

- RAM: limit ต่อ service และ shared sim เมื่อจำเป็น
- Exchange rate limit: shared market-data cache
- CPU: tick jitter และ backtest queue ที่ `nice` ต่ำ

## ลำดับการทำงานที่แนะนำ

1. ทำ Phase 0 ให้ complete และบันทึก baseline ก่อน
2. จำกัด Phase 1 ให้อยู่ใน scope single operator เพื่อพิสูจน์ write path และ audit
3. ทำ Phase 2 ก่อนเปิด tenant ภายนอก เพื่อให้ isolation และ capacity ผ่านการใช้งานจริง
4. เปิด SaaS แบบ sim-only ก่อน แล้วจึงเปิด paid live หลัง security review
5. Scale-out เมื่อ capacity measurement บอกว่าจำเป็น ไม่ใช่ก่อนเวลา

## ไฟล์อ้างอิงสำคัญ

| ไฟล์ | บทบาทใน roadmap |
|---|---|
| `xauby/webui/server.py` | ฐานของ write API, SSE และ tenant routing |
| `xauby/runtime/paths.py` | seam ของ multi-instance |
| `xauby/runtime/manual_orders.py` | pattern File-IPC ที่จะ generalize เป็น command queue |
| `xauby/launcher/config_io.py` | ต้องเลิก hardcode `bot_config.yaml` ใน Phase 0 |
| `xauby/runtime/pair_config.py` | whitelist mutators และ hot-reload สำหรับ pair management |
| `xauby/engine/loop.py` | จุดเดียวที่ drain command queue ใน `tick()` |

## เกณฑ์การตรวจรับเอกสารและงานต่อไป

- ชื่อ path และไฟล์อ้างอิงต้องมีอยู่จริงใน repository
- ทุก Phase มี scope, technical decision และ exit criteria ชัดเจน
- เอกสารนี้ไม่ใช่การอนุมัติให้เปิด live SaaS โดยอัตโนมัติ
- งาน implementation ของ Phase 0/1 ต้องทำในรอบถัดไปพร้อม test plan และ security review ตาม scope
