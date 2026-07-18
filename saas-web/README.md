# xAuby Pilot (saas-web)

Vite + React SPA for the xAuby SaaS control plane (`xauby/saas`, port 8790).

## Develop

```bash
npm install
npm run dev     # http://localhost:5173, proxies /api and /auth to 127.0.0.1:8790
```

Start the backend first: `python -m xauby.saas` (see `docs/saas.md`).

## Build

```bash
npm run build   # tsc -b && vite build -> dist/
```

## Deploy note

`vercel.json` rewrites `/api/*`, `/auth/*`, and `/healthz` to a hardcoded
backend origin (currently the operator's Tailscale VPS hostname). Vercel cannot
read env vars in rewrite destinations, so **edit the destination URLs in
`vercel.json` for your own deployment** before running `vercel deploy`. The
target must be reachable from Vercel's edge (a bare `*.ts.net` hostname is only
resolvable when Tailscale Funnel is enabled for it).
