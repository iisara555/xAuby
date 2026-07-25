import type { NextConfig } from "next";

const appScriptSource = process.env.NODE_ENV === "development"
  ? "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:"
  : "script-src 'self' 'unsafe-inline' blob:";

const appCsp = [
  "default-src 'self'",
  appScriptSource,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self' blob:",
  "media-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const researchCsp = appCsp.replace(appScriptSource, "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:");

const nextConfig: NextConfig = {
  async headers() {
    const contentSecurityPolicy = (source: string, value: string) => ({
      source,
      headers: [{ key: "Content-Security-Policy", value }],
    });
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      contentSecurityPolicy("/research-platform.html", researchCsp),
      contentSecurityPolicy("/app/:path*", appCsp),
      contentSecurityPolicy("/login", appCsp),
      contentSecurityPolicy("/forgot-password", appCsp),
      contentSecurityPolicy("/reset-password", appCsp),
      contentSecurityPolicy("/invite/:path*", appCsp),
    ];
  },
};

export default nextConfig;
