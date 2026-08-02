const HOP_BY_HOP_HEADERS = [
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

// The browser must never author its own forwarding chain. The control plane
// runs uvicorn with proxy_headers=True and trusts 127.0.0.1, which is exactly
// where this proxy connects from, so anything surviving here is written
// straight into request.client.host — the key every AttemptThrottle in
// xauby/saas/app.py rate-limits on. Forwarding the client's value made
// login, Trade PIN reset and email throttles bypassable by varying a header.
const CLIENT_SPOOFABLE_HEADERS = [
  "forwarded",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-port",
  "x-forwarded-proto",
  "x-real-ip",
];

// Set by the edge before our code runs, so they cannot be spoofed end-to-end.
const TRUSTED_CLIENT_IP_HEADERS = ["x-vercel-forwarded-for", "cf-connecting-ip"];

function trustedClientIp(request: Request): string {
  for (const header of TRUSTED_CLIENT_IP_HEADERS) {
    const value = request.headers.get(header)?.split(",")[0]?.trim();
    if (value) return value;
  }
  return "";
}

function apiOrigin(): URL {
  const configured = process.env.XAUBY_API_ORIGIN?.trim();
  if (!configured) {
    throw new Error("XAUBY_API_ORIGIN is not configured");
  }

  const origin = new URL(configured);
  if (origin.protocol !== "https:" && process.env.NODE_ENV === "production") {
    throw new Error("XAUBY_API_ORIGIN must use HTTPS in production");
  }
  return origin;
}

function upstreamUrl(request: Request, pathname: string): URL {
  const target = new URL(pathname, apiOrigin());
  target.search = new URL(request.url).search;
  return target;
}

export function scopedPath(prefix: string, segments: string[]): string | null {
  // Catch-all route params are user controlled. Reject traversal and encoded
  // separators before URL resolution can normalize them outside the route's
  // intended /auth or /api/v1 namespace.
  if (!Array.isArray(segments) || segments.length === 0) return null;
  if (segments.some((segment) => (
    !segment || segment === "." || segment === ".."
    || /[\\/?#%]/.test(segment)
  ))) {
    return null;
  }
  return `${prefix}/${segments.join("/")}`;
}

export async function proxyApiRequest(request: Request, pathname: string): Promise<Response> {
  const headers = new Headers(request.headers);
  for (const header of HOP_BY_HOP_HEADERS) headers.delete(header);
  for (const header of CLIENT_SPOOFABLE_HEADERS) headers.delete(header);
  const clientIp = trustedClientIp(request);
  if (clientIp) headers.set("x-forwarded-for", clientIp);
  headers.set("accept-encoding", "identity");

  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(15_000),
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }

  try {
    const upstream = await fetch(upstreamUrl(request, pathname), init);
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.delete("content-length");
    responseHeaders.set("cache-control", "no-store");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("xAuby API upstream request failed", {
      method: request.method,
      pathname,
      error: error instanceof Error ? error.message : "unknown error",
    });
    return Response.json({ detail: "API upstream unavailable" }, { status: 502 });
  }
}
