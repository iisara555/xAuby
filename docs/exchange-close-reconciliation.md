# OKX TP reconciliation และ realized PnL

เอกสารนี้อธิบาย behavior ที่ต้องรักษาเมื่อ OKX ปิด position ด้วย TP/SL หรือการ
ปิดจากภายนอก engine

## Data contract

`get_position_history()` ของ CCXT adapter คืนข้อมูล normalized ได้แก่ exchange
position ID, close ID, side, entry/average exit, quantity, fee, funding และ
`realizedPnl` พร้อม timestamp

Closed trade ต้องเก็บ `pnl_source=okx_positions_history` และ
`pnl_confirmed=true` เมื่อ match สำเร็จ การใช้ close ID ที่ประกอบจาก exchange,
position ID และ close timestamp ทำให้ reconciliation ซ้ำได้โดยไม่สร้างรายการซ้ำ

## State machine

```text
bought + exchange position exists       -> strategy evaluation
bought + exchange position is flat      -> reconcile history
history matched                         -> idle + confirmed closed trade
history missing/ambiguous               -> idle + pending + WAIT + entry blocked
pending history later matched           -> confirmed trade + unblock after WAIT cycle
```

`last_closed_trade` ถูกใส่ใน snapshot เพื่อให้ dashboard แสดง realized PnL ล่าสุด
แม้ไม่มี open position ส่วน Activity แสดง source badge `OKX Verified` หรือ
`Pending reconciliation`

## Operator recovery

หยุดเฉพาะ tenant ที่กำลัง live, สำรอง control-plane และ tenant SQLite, รัน
recovery แบบ dry-run ตรวจ symbol/side/position ID/time/PnL แล้วจึง apply เฉพาะ
รายการที่ match แน่นอน หลัง apply ตรวจว่า OKX positions ว่าง, DB เป็น idle และ
ไม่มี pending entry block ค้างโดยไม่ตั้งใจ

ห้ามสร้าง PnL จาก ticker, mark price หรือประมาณการ fee/funding เมื่อ history ของ
OKX ยังไม่พร้อม

