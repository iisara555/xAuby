export type Me = {
  id: string; email: string; role: "platform_admin" | "user"; csrf_token: string
  account_status: string; email_verified: boolean; totp_enabled: boolean; mfa_verified: boolean
  trade_pin_configured: boolean; tenant: null | { id: string; slug: string; status: string; live_status: string }
}

let csrf = ""
export function setCsrf(value: string): void { csrf = value }

type ApiErrorIssue = {
  loc?: unknown[]
  msg?: unknown
  type?: unknown
  ctx?: Record<string, unknown>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function fieldLabel(issue: ApiErrorIssue): string {
  const field = issue.loc?.at(-1)
  if (field === "password" || field === "new_password" || field === "current_password") return "Password"
  if (field === "email") return "Email"
  if (field === "totp_code" || field === "code") return "TOTP"
  return typeof field === "string" ? field : "ข้อมูล"
}

function validationIssueMessage(value: unknown): string | null {
  if (!isRecord(value)) return typeof value === "string" ? value : null
  const issue = value as ApiErrorIssue
  const label = fieldLabel(issue)
  if (issue.type === "missing") return `กรุณากรอก ${label}`
  if (issue.type === "string_too_short") {
    const minimum = issue.ctx?.min_length
    return `${label} ต้องมีอย่างน้อย ${typeof minimum === "number" ? minimum : 1} ตัวอักษร`
  }
  if (issue.type === "string_too_long") return `${label} ยาวเกินกำหนด`
  if (issue.type === "string_pattern_mismatch") return `${label} มีรูปแบบไม่ถูกต้อง`
  return typeof issue.msg === "string" ? `${label}: ${issue.msg}` : null
}

export function apiErrorMessage(payload: unknown, status: number): string {
  if (typeof payload === "string" && payload.trim()) return payload
  if (isRecord(payload)) {
    const detail = payload.detail
    if (typeof detail === "string" && detail.trim()) return detail
    if (Array.isArray(detail)) {
      const messages = detail.map(validationIssueMessage).filter((item): item is string => Boolean(item))
      if (messages.length) return messages.join(" · ")
    }
    const nested = validationIssueMessage(detail)
    if (nested) return nested
    for (const key of ["message", "error"]) {
      if (typeof payload[key] === "string" && payload[key].trim()) return payload[key]
    }
  }
  return `ระบบตอบกลับผิดพลาด (HTTP ${status})`
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set("Content-Type", "application/json")
  if (csrf && !["GET", "HEAD"].includes((init.method ?? "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", csrf)
  }
  const response = await fetch(path, { ...init, headers, credentials: "include" })
  const payload = await response.json().catch(() => ({ detail: "ไม่สามารถอ่านคำตอบจากระบบ" }))
  if (!response.ok) throw new Error(apiErrorMessage(payload, response.status))
  return payload as T
}
