import { valueAt } from "@/lib/api";

export function normalizeWorkspaceSymbol(value: unknown): string {
  return String(value ?? "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function runtimePairState(
  state: Record<string, unknown>,
  requestedSymbol: string,
): Record<string, unknown> {
  const symbol = normalizeWorkspaceSymbol(requestedSymbol);
  if (!symbol) return {};
  const bySymbol = record(valueAt(state, "by_symbol"));
  const direct = record(bySymbol[symbol]);
  if (Object.keys(direct).length > 0) return direct;
  const rootSymbol = normalizeWorkspaceSymbol(valueAt(state, "symbol"))
    || normalizeWorkspaceSymbol(valueAt(state, "focus_symbol"));
  return rootSymbol === symbol ? state : {};
}
