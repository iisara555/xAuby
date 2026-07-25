import { proxyApiRequest } from "@/lib/server/api-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request) {
  return proxyApiRequest(request, "/healthz");
}

export function HEAD(request: Request) {
  return proxyApiRequest(request, "/healthz");
}
