# Roadmap: xAuby → 100% Webapp → Full SaaS

> เอกสารแผนงาน (roadmap) สำหรับพา xAuby จาก trading bot ที่ควบคุมผ่าน
> CLI / Textual TUI / Telegram ไปสู่ **webapp เต็มรูปแบบ (100%)** และต่อยอดเป็น
> **SaaS สมบูรณ์** — โดยคงคอนเซ็ปต์หลัก **multi-strategy / multi-exchange /
> multi-pair** ภายใต้ข้อจำกัดทรัพยากร VPS **1 vCPU / 2 GB RAM**
> (ขยายได้เป็น 2 vCPU / 4 GB)
>
> สถานะ: แผนงาน (ยังไม่เริ่ม implement) — อัปเดตล่าสุด 2026-07-12

---

## 1. จุดตั้งต้น: โค้ดปัจจุบันพร้อมแค่ไหน

จากการสำรวจโค้ดเบสทั้งฝั่ง WebUI และฝั่ง engine สรุปได้ว่าสถาปัตยกรรมปัจจุบันคือ
**"single-tenant process ที่ multi-instance ได้"** — ยังไม่ใช่ multi-tenant
โดยกำเนิด แต่มี "ตะเข็บ" (seams) ที่ออกแบบเผื่อไว้แล้วหลายจุด

### สิ่งที่มีอยู่แล้ว (ต้นทุนที่ไม่ต้องสร้างใหม่)

| สิ่งที่มี | อยู่ที่ไหน | ทำไมสำคัญ |
|---|---|---|
| WebUI แบบ read-only พร้อม auth จริง | `xauby/webui/server.py` (stdlib `ThreadingHTTPServer`, ~1,175 บรรทัด) | มี password + HMAC session cookie, bearer token, Google OAuth + email/domain allowlist, CSP/security headers, secret redaction, bind-security guard — เป็นฐานที่แข็งแรงสำหรับต่อ write API |
| Multi-instance path isolation | `xauby/runtime/paths.py` (`XAUBY_HOME` / `XAUBY_INSTANCE_ID` / `XAUBY_CONFIG_DIR`) | DB, logs, state JSON, sim balance, locks แยกต่อ instance ได้ **โดยไม่ต้องแก้โค้ด** — นี่คือกลไก tenancy ระดับไฟล์ที่มีอยู่แล้ว |
| Account lock ข้าม instance | `~/.xauby/account_locks/` (flock ตาม fingerprint ของ API key, `xauby/engine/base.py`) | บังคับ invariant สำคัญของ SaaS อยู่แล้ว: ห้ามสอง live engine ใช้ exchange account เดียวกัน |
| File-IPC ที่พิสูจน์แล้ว | `core/manual_order_request.json` (drain ใน `tick()`, มี claim + expiry 120s, `xauby/runtime/manual_orders.py`) และ `core/telegram_control.json` (pause flag) | เป็น pattern สำเร็จรูปสำหรับ "web สั่งงาน engine" โดยไม่ต้องเปิด socket เข้า engine loop |
| Config writers แบบปลอดภัย | `xauby/launcher/config_io.py` (`_edit_bot_yaml`: ruamel round-trip + atomic `os.replace`), `xauby/runtime/pair_config.py` (whitelist mutators) | Whitelist edits ถูก engine **hot-reload เองภายใน ~30 วินาที** (`PairRegistry.maybe_reload`) — จัดการ pair/strategy ได้โดยไม่ restart |
| Multi-exchange เป็น plugin | `xauby/api/exchange_registry.py` + `xauby/api/exchanges/` (CCXT adapter ครอบคลุมแทบทุก venue), credential env ตั้งชื่อตาม provider | "tenant เลือก exchange เอง" เป็นเรื่อง config ไม่ใช่เรื่องเขียนโค้ดใหม่ |
| Multi-pair / multi-strategy ใน process เดียว | `xauby/runtime/pair_registry.py`, per-symbol sim/live execution mode, strategy plugin registry (~17 plugins), regime router | คอนเซ็ปต์หลักที่ต้องคงไว้ — ทำงานได้แล้ววันนี้ |

### ตัวขวาง (blockers) ที่ roadmap นี้ต้องแก้

1. **Control plane เป็น Telegram-only และ in-process** —
   `TelegramCommandPoller` ถือ reference ของ engine ตรง ๆ ยังไม่มี HTTP command
   API (event bus ที่มีเป็น observability-only ตามสัญญา
   "subscribers must not import engine")
2. **โมเดล 1 process ต่อ 1 tenant/exchange** — ยังไม่มี supervisor/orchestrator
   สำหรับรัน N engines
3. **สมมติฐาน single-user ต่อ engine** — `.env` ชุดเดียว, Telegram chat เดียว,
   control file ช่องเดียว
4. **Config writers ยังไม่ instance-aware** — `config_io.py` hardcode
   `bot_config.yaml` แบบ cwd-relative (ขณะที่ engine อ่านผ่าน `config_root()`)
5. **RAM คือคอขวดหลัก** — engine 1 ตัว ≈ 150–300 MB RSS →
   VPS 2 GB รองรับได้ราว 3–5 engines เท่านั้น

---

## 2. การตัดสินใจเชิงเทคนิค (ตัดสินใจครั้งเดียว ใช้ทุกเฟส)

| เรื่อง | เลือก | เหตุผล |
|---|---|---|
| Web server | คง stdlib `ThreadingHTTPServer` ถึง Phase 2; ใช้ FastAPI เฉพาะ control plane ใน Phase 3 | โค้ด auth/hardening มีอยู่แล้ว; FastAPI + uvicorn กิน RAM เพิ่ม ~60–80 MB ซึ่งบน 2 GB ยังไม่ควรจ่าย |
| Live update สู่ browser | **SSE (Server-Sent Events)** ไม่ใช่ WebSocket | ข้อมูลไหลทางเดียว (state → browser) ก็พอ คำสั่งใช้ POST ธรรมดา; SSE ทำงานบน stdlib server ได้, ผ่าน proxy ง่าย, auto-reconnect ฟรี |
| Command transport | **File-IPC command queue** (ขยาย pattern `manual_order_request.json`) drain ใน `tick()` | คง invariant "ไม่มีใครเรียก method เข้า engine loop จากภายนอก", crash-safe, มี claim/expiry semantics พิสูจน์แล้ว |
| Database | **SQLite-per-tenant (WAL)** ถึง Phase 3; Postgres เฉพาะข้อมูล control plane ใน Phase 4 | 1 writer ต่อ tenant เหมาะกับ SQLite ที่สุดบน VPS เล็ก; backup / export / ลบข้อมูลลูกค้า = จัดการไฟล์เดียว |
| Secrets (exchange API keys) | **Fernet** (`cryptography`) + master key ใน `/etc/xauby/master.key` (0400, root-only); decrypt เฉพาะตอน spawn engine แล้วส่งผ่าน systemd `LoadCredential=` | Vault จริงกิน ~200 MB RSS + ภาระ ops เกินขนาดทีม; Fernet + file perms + redaction (ที่ webui มีอยู่แล้ว) คือขนาดที่พอดี |
| Frontend | คง **vanilla JS dashboard** (`xauby/webui/static/`, ไม่มี build step); `Website/` (Next.js) ใช้เป็น marketing/signup แบบ **static export** เท่านั้น | Node runtime บน VPS = RAM เท่ากับ tenant หนึ่งราย — ไม่คุ้ม |
| Backtest/optimizer | คิวงานช่องเดียว (single-slot queue ใน SQLite), รันด้วย `nice -n 19` + systemd `CPUWeight=10`; free tier ไม่มี backtest | บน 1 vCPU งาน backtest ห้ามแย่ง CPU กับ live tick เด็ดขาด |
| Process model | **process-per-live-tenant**; sim หลาย tenant รวม process เดียว (Phase 3.5) เมื่อจำเป็นเท่านั้น | live ต้อง isolate (จำกัด blast radius + account lock ทำงานตรงไปตรงมา); sim ไม่ต้องการ isolation แต่ต้องการความถูก |

---

## 3. เฟสของ Roadmap

```
Phase 0   Hardening & Prep     → instance-aware writers, command queue, systemd, versioned state
Phase 1   100% Webapp          → write API + SSE + pair/strategy management UI   ← เป้า "webapp 100%"
Phase 2   Multi-instance       → systemd-as-supervisor + tenant routing + shared market cache
Phase 3a  Tenancy & Onboarding → FastAPI control plane, Fernet creds, plans/quotas, isolation
Phase 3b  Billing & Ops        → Stripe, backups, metrics, security review       ← เป้า "SaaS สมบูรณ์"
Phase 3.5 (ตามความจำเป็น)      → multi-tenant sim engine สำหรับ free tier
Phase 4   Multi-node           → control-plane Postgres + worker bin-packing
```

ทุกเฟส ship ได้อิสระ และไม่มีงานเฟสไหนถูกทิ้ง — write API, command queue,
SSE stream ของ Phase 1 คือ surface ต่อ-tenant ที่ Phase 2–4 เอาไป multiplex ต่อ

### Phase 0 — Hardening & Prep (~1–2 สัปดาห์)

**เป้าหมาย:** ถอดสลักระเบิดที่จะทำให้เฟสหลังเสี่ยง โดยผู้ใช้ไม่เห็นความเปลี่ยนแปลง

1. **Config writers ให้ instance-aware** — `xauby/launcher/config_io.py`
   (`_edit_bot_yaml`, `_load_bot_yaml`) เลิก hardcode `"bot_config.yaml"`
   แบบ cwd-relative แล้ว resolve ผ่าน `xauby/runtime/paths.py`
   (`config_root()`); ตรวจ `xauby/runtime/pair_config.py` ให้รับ
   `project_root`/`config_dir` ครบทุกฟังก์ชัน
2. **State contract versioning** — เพิ่ม `schema_version` ใน state JSON ที่
   export ทุก tick (`core/logs/xauby_bot_state.json`) และใน `/api/meta`;
   webui degrade อย่างสุภาพเมื่อ version ไม่ตรง (นี่คือ API contract
   ที่ทุกอย่างหลังจากนี้เกาะ — freeze ตอนนี้)
3. **Generalize command file** — รวม `xauby/runtime/manual_orders.py` +
   `xauby/runtime/telegram_control.py` เป็น `xauby/runtime/command_queue.py`:
   JSONL inbox ที่มี `request_id`, `created_at`, `expires_at`, `source`,
   HMAC `signature` (secret จาก `.env`) และถูก drain แบบ atomic ใน `tick()`
   (`xauby/engine/loop.py`); path เดิมคงไว้เป็น thin wrapper ให้ Telegram/TUI
   ไม่พัง; คำสั่งชุดแรก: `pause`, `resume`, `manual_order`, `close_position`,
   `set_exec_mode(symbol, sim|live)`, `reload_config`
4. **systemd units** — เพิ่ม `deploy/systemd/`:
   `xauby-engine@<instance>.service` (ตั้ง `XAUBY_INSTANCE_ID=%i`,
   `Restart=on-failure`, `MemoryMax=` ตาม tier) และ `xauby-webui.service`
5. **วัด RSS จริง** — บันทึก baseline ของ engine (sim, 3 pairs), webui, TUI —
   ตัวเลขความจุทุกตารางในเอกสารนี้ต้องเทียบกับของจริง

**Exit criteria:** 2 instances (`XAUBY_INSTANCE_ID` ต่างกัน) รันคู่กันใต้
systemd และแก้ config โดยไม่ชนกัน; command queue ผ่านเทสต์ replay/expiry/signature;
state JSON มี version

### Phase 1 — 100% Webapp สำหรับ single operator (~2–4 สัปดาห์)

**เป้าหมาย:** ทุกอย่างที่วันนี้ทำผ่าน Telegram/TUI/SSH ทำได้จาก browser —
Telegram ลดบทบาทเหลือ notification เสริม ไม่ใช่ control plane

1. **Write API บน webui เดิม** (`xauby/webui/server.py`) — webui ยังคง
   **ไม่แตะ engine ตรง ๆ**: ทุก write ลง command queue หรือ config mutators
   เท่านั้น
   - `POST /api/control/pause` / `resume` → command queue
   - `POST /api/orders/manual` → command queue (คง semantics เดิม:
     expiry 120s + ขั้น confirm ใน UI)
   - `POST /api/pairs` (add/remove/update), `POST /api/pairs/{sym}/strategy`,
     `POST /api/pairs/{sym}/timeframes` → mutators ใน
     `xauby/runtime/pair_config.py` → engine hot-reload ≤30s
     (**จุดขายหลัก: จัดการ pair/strategy โดยไม่ restart**)
   - `POST /api/pairs/{sym}/exec-mode` (sim⇄live) → command queue;
     เปิด live ต้องมี **typed confirmation** + เคารพ router live-gate
     (`regime_router_live_confirmed`) เสมอ
   - `POST /api/config` (เฉพาะ key ที่ whitelist ไว้) → `_edit_bot_yaml`;
     response บอกชัดว่า "hot" หรือ "restart required"
   - ความปลอดภัย: write ต้องใช้ session (password/OAuth) เท่านั้น —
     bearer token คงเป็น read-only; เพิ่ม CSRF ด้วย same-site cookie +
     custom header check
2. **Command feedback loop** — engine เขียนผลของทุกคำสั่ง
   (`accepted/executed/rejected` + เหตุผล) ลง `core/logs/command_results.jsonl`;
   webui เปิด `GET /api/commands/{id}`; UI แสดง pending → confirmed
   ภายใน 1 tick (≤60s) พร้อมสื่อสารความคาดหวัง ("มีผล tick ถัดไป")
3. **SSE** — `GET /api/stream`: webui เฝ้า mtime ของ state JSON + events
   แล้ว push delta; ฝั่ง frontend เปลี่ยน `static/refresh-scheduler.js`
   เป็น `EventSource` (คง polling เป็น fallback)
4. **Frontend เพิ่มใน vanilla JS เดิม** — pair management panel,
   config editor (เฉพาะ key ที่อนุญาต + schema hints), manual-order modal
   พร้อม confirm, sim/live toggle (danger styling), command status toasts
5. **Deploy** — webui เป็น systemd service อยู่หลัง Caddy (หรือ nginx)
   สำหรับ TLS; bind guard เดิมกันการเปิด 0.0.0.0 โดยไม่มี auth อยู่แล้ว

**งบ RAM (1c/2GB):** engine 150–300 MB + webui 40–60 MB + Caddy ~30 MB → สบาย

**ความเสี่ยงหลัก:** write endpoints = attack surface ใหม่ → ป้องกันด้วย
session-only writes, CSRF, command signing + expiry, และ **engine ยังเป็น
authority** (re-validate ทุกคำสั่งเหมือนที่ validate Telegram confirm วันนี้);
ปุ่ม sim→live คือปุ่มอันตรายสุด → two-step confirm + audit ลง event log

**Exit criteria:** ใช้งานจริง 1 สัปดาห์โดยไม่ต้องแตะ Telegram;
ทุก control action มี audit trail ใน event log; TUI เดิมใช้ได้โดยไม่แก้

### Phase 2 — Multi-Instance บนโฮสต์เดียว (~3–5 สัปดาห์)

**เป้าหมาย:** รัน N engines (หลาย exchange / หลาย config) บน VPS เดียว
ด้วย webui เดียว — คือ "กลไก SaaS โดยยังไม่มีลูกค้า" (เราเป็น tenant #1..N เอง)

1. **systemd คือ process manager ตัวจริง** — ไม่เขียน process babysitter เอง
   (systemd มี restart/backoff/cgroups `MemoryMax=`/`CPUWeight=` ครบ);
   เขียนแค่ orchestrator บาง ๆ (`xauby/supervisor/`) ที่:
   (a) ถือ instance registry (`~/.xauby/instances.json`: instance_id →
   exchange, config dir, tier, desired-state), (b) สั่งผ่าน `systemctl`,
   (c) อ่าน health จาก heartbeat/state JSON ของแต่ละ instance
   แล้ว reconcile กับความจริงจาก `systemctl show`
2. **Webui รองรับหลาย instance** — route เป็น `/i/{instance}/api/...` +
   instance switcher ใน UI + `GET /api/instances` (list + health);
   ฝั่งอ่านชี้ state JSON + SQLite (ro) ต่อ instance,
   ฝั่งเขียนชี้ command queue dir ต่อ instance (ได้ฟรีจาก `paths.py`)
3. **Shared market-data cache service** — ตัวกด RAM/rate-limit
   ที่สำคัญที่สุดของทั้ง roadmap: process เล็กใหม่ (`xauby/marketdata/`)
   fetch candles/tickers **ครั้งเดียวต่อ (exchange, symbol, timeframe)**
   เขียนลง SQLite กลาง (single-writer, readers เปิด `mode=ro`, WAL);
   engine เพิ่ม config `market_data_source: shared|direct` โดยอ่าน
   cache-first + staleness guard แล้ว fallback เป็น direct fetch;
   **private endpoints (balance/orders) แยกต่อ account เสมอ** —
   5 tenants เทรด BTC บน OKX ไม่ต้องยิง public API 5 เท่าอีกต่อไป
4. **Account-lock UX** — โชว์สถานะ `~/.xauby/account_locks/` ใน webui
   ให้การชนกันของ live engine บน account เดียว fail แบบมองเห็น ไม่เงียบ
5. **Tick-phase jitter + watchdog** — offset เวลา tick ต่อ instance
   กัน CPU spike พร้อมกันบน 1 vCPU; orchestrator flag instance ที่
   heartbeat ค้าง >3 ticks (ต่อยอดเป็น systemd `WatchdogSec` ได้)

**ความจุ:** 1c/2GB — base stack (OS + Caddy + webui + orchestrator +
marketdata) ≈ 350–450 MB → รับได้ **~3–5 engines**; 2c/4GB → **~8–12 engines**

**Exit criteria:** ≥3 instances (≥2 exchanges) รันต่อเนื่อง 2 สัปดาห์
โดยไม่ต้องดูแล; webui เดียวคุมทั้งหมด; market cache ลด RSS/API calls
ต่อ engine ได้วัดผลจริง; kill engine ตัวหนึ่งไม่กระทบตัวอื่นเลย

### Phase 3 — SaaS (~6–10 สัปดาห์ แบ่ง 3a/3b) — แนะนำอัปเกรด VPS เป็น 2c/4GB ที่จุดนี้

**เป้าหมาย:** ผู้ใช้จริง, onboarding แบบ self-service, billing,
tenant isolation แบบแข็ง — FastAPI เข้ามาที่เฟสนี้

#### 3a — Tenancy & Onboarding

1. **Control-plane app ใหม่** (`xauby/saas/` หรือแยก repo) —
   FastAPI + uvicorn (1 worker) ถือ: users, sessions, tenants→instances
   mapping, encrypted exchange credentials, plan/quota enforcement;
   DB ของ control plane ยังเป็น **SQLite** (writer เดียว, write น้อย);
   webui เดิมกลายเป็น per-tenant dashboard อยู่หลัง control plane
   โดยบังคับ tenant scoping **สองชั้น** (proxy path rewrite + ownership
   check กับ instance registry)
2. **Credential handling** — ผู้ใช้วาง exchange API key/secret →
   เข้ารหัส Fernet ด้วย master key → เก็บใน control DB; decrypt เฉพาะใน
   orchestrator ตอน spawn engine แล้วส่งผ่าน systemd `LoadCredential=`
   (ดีกว่า env); บังคับแนวทาง "trade-only key, no-withdrawal" +
   startup probe ที่ **ปฏิเสธ key ที่มีสิทธิ์ถอนเงิน**; ใช้ redaction list
   ของ webui ทุกจุดที่ log
3. **Plans/quotas** (บังคับที่ orchestrator + **ใน engine** ไม่ใช่แค่ UI):
   - **Free:** sim-only (ล็อค execution mode ผ่าน per-symbol exec-mode +
     router live-gate ที่มีอยู่แล้ว), ≤2 pairs, 1 exchange, ไม่มี backtest —
     ถูกเพราะ sim engine หรี่ได้ (tick ยาวขึ้น, ไม่มี private WS)
   - **Paid:** live ได้, pairs มากขึ้น, backtest quota (แบบ batched +
     nice'd, N ครั้ง/เดือน)
4. **Isolation hardening** — systemd sandboxing ต่อ tenant:
   `DynamicUser=`, `ProtectHome=`, `ReadWritePaths=` เฉพาะ `XAUBY_HOME`
   ของตัวเอง, `MemoryMax=350M` ให้ tenant ที่ OOM ตายคนเดียว

#### 3b — Billing & Ops

5. **Stripe** — Checkout + customer portal + webhooks → plan state ใน
   control DB → orchestrator reconcile (idempotent handlers + reconcile
   loop กัน state drift); **downgrade = force-sim + grace period —
   ห้าม silent-close live position เด็ดขาด** (แจ้งเตือนก่อนเสมอ)
6. **Ops** — nightly `sqlite3 .backup` ต่อ tenant → object storage
   (rclone ไป S3/B2) + ซ้อม restore ให้เป็น runbook; metrics แบบเบา
   ส่งออกไป Grafana Cloud free tier (**ไม่รัน Prometheus บนเครื่อง 2–4 GB**);
   logrotate ครอบ JSONL
7. **Security review checkpoint** — ให้คนนอกรีวิว write API,
   credential path, tenant isolation **ก่อนรับลูกค้า live รายแรก**
   (จุดที่เราเริ่มถือ trading keys ของคนอื่น = liability ก้อนใหญ่สุดของ
   ทั้ง roadmap)

#### 3.5 — Multi-tenant sim engine (ทางหนี ทำเมื่อจำเป็นเท่านั้น)

process เดียวรัน sim portfolio ของหลาย tenant (strategy เป็น pure analyst
อยู่แล้ว และ sim ไม่ต้องมี account lock) → ปลดล็อก free tier ขนาดใหญ่
(30–100 portfolios ใน 1–2 processes) — เป็น refactor ใหญ่ของ engine
**ทำเมื่อ free signups เกินความจุแบบ process-per-tenant แล้วเท่านั้น**

**งบ RAM (2c/4GB):** base stack + control plane (~90–120 MB) ≈ 600–700 MB
→ **~8–10 live tenant engines**; ถ้ายังอยู่ 1c/2GB = control plane +
live tenants ได้แค่ 2–3 ราย (ต้องซื่อสัตย์กับ pricing เรื่องนี้)

**Exit criteria:** คนแปลกหน้า signup → วาง key → รัน sim → upgrade →
เปิด live ได้โดยเราไม่ต้อง SSH เลย; backup/restore drill ผ่าน;
พิสูจน์ได้ว่า tenant OOM/crash ไม่กระทบ tenant อื่น

### Phase 4 — Scale-out เกิน 1 VPS (เมื่อ live tenants >~10)

**เป้าหมาย:** N VPS ราคาถูก + control plane เดียว — **ไม่ใช่ Kubernetes**

1. **Control-plane node** (VPS เล็กสุด): FastAPI control plane +
   marketing site + **Postgres เฉพาะข้อมูล control plane**
   (users/billing/registry — multi-node ต้องการ shared DB จริงแล้ว)
2. **Worker nodes** image เหมือนกันทุกตัว (orchestrator + engines +
   marketdata cache ต่อ node); placement = bin-packing ตาม RSS budget;
   tenant ปักหมุดกับ node; migration = stop → copy `XAUBY_HOME` → start
   (downtime หลักนาที ยอมรับได้ — **position อยู่ที่ exchange
   ไม่ได้อยู่ในไฟล์**)
3. **Trade data คง SQLite-per-tenant บน worker** — ไม่ centralize
   (append-heavy, private ต่อ tenant, มี backup ไป object storage อยู่แล้ว)
4. เชื่อม node ด้วย WireGuard หรือ mTLS; webui proxy `/i/{instance}`
   ไปยัง node เจ้าของ

**Exit criteria:** เพิ่ม worker node ได้ใน <30 นาทีตาม runbook;
ซ้อม migrate tenant ผ่าน

---

## 4. ตารางความจุ (ประมาณการ — ต้องวัดจริงใน Phase 0)

สมมติฐาน: live engine 150–300 MB RSS, sim engine แบบหรี่แล้ว ~120–180 MB

| VPS | Live engines | Sim engines (trimmed) | SaaS tenants ที่สมจริง |
|---|---|---|---|
| 1 vCPU / 2 GB | **3–4** | **5–7** | 2–3 paid live + sim เล็กน้อย |
| 2 vCPU / 4 GB | **8–10** | **12–16** | ~8 paid live + free-tier sim |
| + Phase 3.5 shared-sim | — | 30–100 portfolios / 1–2 processes | ปลดล็อก free tier จริง |

ลำดับคอขวด: **RAM** > exchange rate limits (แก้ด้วย shared market cache)
> CPU (แก้ด้วย tick jitter + nice'd single-slot backtest queue —
tick 60 วินาทีเป็น burst สั้น ๆ อยู่แล้ว)

---

## 5. Invariants ที่ทุกเฟสต้องรักษา

- UI ทุกชั้น (webui/TUI) **ไม่เรียกเข้า engine ตรง ๆ** — ทุก write ผ่าน
  file-IPC command queue ที่ engine drain เองใน `tick()`
- `observability` ไม่ import `engine` (replay/health เป็น engine-agnostic)
- Strategy plugin เป็น pure analyst — ไม่สั่ง order, ไม่แตะ DB
- **Default to simulation** — live ต้องผ่านทุกด่านเหมือนเดิม
  (`--live` + `LIVE_TRADING=true` + `simulate_only: false` + per-pair
  `mode: live`) และ router live-gate
- Account lock ข้าม instance ห้ามอ่อนลง — ยิ่ง multi-tenant
  ยิ่งเป็นเส้นชีวิต
- Engine ยังเป็น authority ของทุกคำสั่ง: web ส่ง "คำขอ" engine เป็นผู้
  validate และตัดสิน

## 6. ไฟล์หลักที่แต่ละเฟสจะแตะ

| ไฟล์ | บทบาทใน roadmap |
|---|---|
| `xauby/webui/server.py` | ฐานของ write API (Ph1), SSE (Ph1), tenant routing (Ph2) |
| `xauby/runtime/paths.py` | seam ของ multi-instance ทั้งหมด — แทบไม่ต้องแก้ แค่ใช้ให้ครบ |
| `xauby/runtime/manual_orders.py` + `telegram_control.py` | pattern ที่จะ generalize เป็น `command_queue.py` (Ph0) |
| `xauby/launcher/config_io.py` | เลิก hardcode `bot_config.yaml` (Ph0) |
| `xauby/runtime/pair_config.py` | whitelist mutators (hot-reload) — หัวใจของ web pair management (Ph1) |
| `xauby/engine/loop.py` | จุด drain command queue ใน `tick()` (Ph0/Ph1) |
| `xauby/api/exchanges/` | จุดต่อ cache-first market data (Ph2) |
| `deploy/systemd/` (ใหม่) | engine@instance / webui / orchestrator units (Ph0/Ph2) |
| `xauby/marketdata/` (ใหม่) | shared market-data cache service (Ph2) |
| `xauby/saas/` (ใหม่ หรือแยก repo) | FastAPI control plane (Ph3) |
