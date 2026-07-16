# xAuby documentation

เอกสารนี้เป็นจุดเริ่มต้นสำหรับผู้ดูแลระบบและผู้พัฒนา xAuby ระบบปัจจุบันใช้
OKX USDT-settled perpetual ผ่าน CCXT โดย exchange เป็น source of truth สำหรับ
สถานะ position และ realized PnL

## เอกสารหลัก

| เอกสาร | ใช้สำหรับ |
| --- | --- |
| [architecture.md](architecture.md) | โครงสร้างระบบ, tenant isolation และ data flow |
| [trading-flow.md](trading-flow.md) | ลำดับการทำงานต่อ engine tick และ short/long |
| [exchange-close-reconciliation.md](exchange-close-reconciliation.md) | การตรวจพบ TP/SL ที่ปิดบน OKX และการยืนยัน PnL |
| [configuration.md](configuration.md) | YAML, whitelist และ environment variables |
| [multi-exchange-ccxt.md](multi-exchange-ccxt.md) | adapter และข้อจำกัดของ CCXT |
| [webui.md](webui.md) | dashboard/activity แบบ read-only |
| [tui.md](tui.md) | Textual TUI |
| [telegram.md](telegram.md) | alert และคำสั่ง operator |
| [security-saas-audit.md](security-saas-audit.md) | security controls และ deployment hardening |

## สถานะที่ผู้ใช้ควรเห็น

- มี position: แสดง side, entry, quantity และ leverage จาก state ที่ตรวจสอบแล้ว
- ไม่มี position: แสดง `FLAT`, Current action เป็น `WAIT` และค่าของ position เป็น `—`
- ปิดโดย OKX แล้ว: แสดง `Last realized PnL — OKX verified` และ source badge ใน Activity
- ประวัติ OKX ดึงไม่ได้หรือจับคู่ไม่ได้: คง `FLAT/WAIT`, block entry และ retry ต่อไป

เริ่มจาก [README.md](../README.md) สำหรับ quick start และ safety checklist

