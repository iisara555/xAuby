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

export async function proxyApiRequest(request: Request, pathname: string): Promise<Response> {
  const headers = new Headers(request.headers);
  for (const header of HOP_BY_HOP_HEADERS) headers.delete(header);
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
