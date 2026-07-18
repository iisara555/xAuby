import type { ButtonHTMLAttributes, InputHTMLAttributes, PropsWithChildren, ReactNode } from "react"

export function Button({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>): ReactNode {
  return <button className={`button ${className}`} {...props} />
}

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>): ReactNode {
  return <input className={`input ${className}`} {...props} />
}

export function Card({ children, className = "" }: PropsWithChildren<{ className?: string }>): ReactNode {
  return <section className={`card ${className}`}>{children}</section>
}

export function Badge({ children, tone = "neutral" }: PropsWithChildren<{ tone?: "neutral" | "good" | "warn" | "bad" }>): ReactNode {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function Field({ label, children, hint }: PropsWithChildren<{ label: string; hint?: string }>): ReactNode {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}

export function Alert({ children, tone = "warn" }: PropsWithChildren<{ tone?: "warn" | "bad" | "good" }>): ReactNode {
  return <div className={`alert alert-${tone}`} role="status">{children}</div>
}
