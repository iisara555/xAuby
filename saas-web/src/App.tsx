import { useEffect, useState, type ReactNode } from "react"
import { api, type Me, setCsrf } from "./api"
import { AuthPage } from "./components/AuthPage"
import { Dashboard } from "./components/Dashboard"

export function App(): ReactNode {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  async function load(): Promise<void> {
    setLoading(true)
    try { const result = await api<Me>("/api/v1/me"); setCsrf(result.csrf_token); setMe(result) }
    catch { setMe(null) } finally { setLoading(false) }
  }
  useEffect(() => {
    let active = true
    api<Me>("/api/v1/me")
      .then((result) => { if (active) { setCsrf(result.csrf_token); setMe(result) } })
      .catch(() => { if (active) setMe(null) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  if (loading) return <main className="loading"><span className="brand-mark">xA</span><p>กำลังเปิด secure workspace…</p></main>
  if (!me) return <AuthPage onAuthenticated={() => void load()} />
  return <Dashboard me={me} onLogout={() => api("/auth/logout", { method: "POST" }).finally(() => { setCsrf(""); setMe(null) })} />
}
