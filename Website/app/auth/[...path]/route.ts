import { proxyApiRequest, scopedPath } from "@/lib/server/api-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };

async function handler(request: Request, context: RouteContext) {
  const { path } = await context.params;
  const pathname = scopedPath("/auth", path);
  if (!pathname) return Response.json({ detail: "invalid path" }, { status: 400 });
  return proxyApiRequest(request, pathname);
}

export {
  handler as DELETE,
  handler as GET,
  handler as HEAD,
  handler as OPTIONS,
  handler as PATCH,
  handler as POST,
  handler as PUT,
};
