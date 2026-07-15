import type { NextConfig } from "next";

const backendOrigin = (
  process.env.XAUBY_BACKEND_ORIGIN ?? "https://xauby-vps.tailfcdd3a.ts.net"
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendOrigin}/api/:path*` },
      { source: "/auth/:path*", destination: `${backendOrigin}/auth/:path*` },
      { source: "/healthz", destination: `${backendOrigin}/healthz` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      {
        source: "/app/:path*",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      {
        source: "/login",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      {
        source: "/reset-password",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      {
        source: "/verify-email",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      {
        source: "/confirm-email",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      {
        source: "/api/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/auth/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
