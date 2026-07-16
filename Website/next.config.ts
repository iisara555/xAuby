import type { NextConfig } from "next";

const apiOrigin = (process.env.XAUBY_API_ORIGIN ?? "http://127.0.0.1:8790").replace(/\/$/, "");

const nextConfig: NextConfig = {
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        {
          key: "Content-Security-Policy",
          // 'unsafe-inline' stays because Next hydration and the bundled
          // research-platform.html rely on inline script/style blocks; the
          // policy still pins every source to this origin.
          value: [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "media-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
          ].join("; "),
        },
      ],
    }];
  },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` },
      { source: "/auth/:path*", destination: `${apiOrigin}/auth/:path*` },
      { source: "/healthz", destination: `${apiOrigin}/healthz` },
    ];
  },
};

export default nextConfig;
