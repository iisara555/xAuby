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
