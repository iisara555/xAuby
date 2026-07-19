"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { Preset } from "@/lib/api";
import { valueAt } from "@/lib/api";
import { useProfile, useSnapshot } from "@/lib/hooks";
import { normalizeWorkspaceSymbol, runtimePairState } from "@/lib/runtime-pair-state";

export type WorkspacePair = {
  id: string;
  symbol: string;
  label: string;
  strategy: string;
  isFocus: boolean;
  status: string;
  tone: "position" | "signal" | "stale" | "idle";
};

type WorkspacePairContextValue = {
  pairs: WorkspacePair[];
  selectedPair: WorkspacePair | null;
  selectedSymbol: string;
  selectPair: (symbol: string) => void;
};

const WorkspacePairContext = createContext<WorkspacePairContextValue | null>(null);
const EMPTY_PRESETS: Preset[] = [];
const EMPTY_STATE: Record<string, unknown> = {};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function pairState(preset: Preset, state: Record<string, unknown>, stale: boolean): Omit<WorkspacePair, "isFocus"> {
  const symbol = normalizeWorkspaceSymbol(preset.symbol);
  const snapshot = runtimePairState(state, symbol);
  const snapshotReady = Object.keys(snapshot).length > 0;
  const position = record(valueAt(snapshot, "position"));
  const positionState = String(valueAt(position, "state") ?? "").toLowerCase();
  const positionSide = String(valueAt(position, "position_side") ?? "").toUpperCase();
  const positionQuantity = Number(valueAt(position, "quantity") ?? 0);
  const hasPosition = positionState === "bought" || positionState === "open"
    || (!positionState && positionQuantity > 0 && positionSide !== "" && positionSide !== "FLAT");
  const signal = String(valueAt(snapshot, "signal_meta", "action") ?? "WAIT").toUpperCase();
  const actionableSignal = !["", "HOLD", "WAIT", "NONE"].includes(signal);

  return {
    id: preset.id,
    symbol,
    label: preset.label,
    strategy: preset.strategy,
    status: !snapshotReady ? "SYNCING" : hasPosition ? positionSide || "OPEN" : stale ? "DELAYED" : signal === "HOLD" ? "WAIT" : signal,
    tone: !snapshotReady ? "stale" : hasPosition ? "position" : stale ? "stale" : actionableSignal ? "signal" : "idle",
  };
}

export function WorkspacePairProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: profile } = useProfile();
  const { data: snapshot } = useSnapshot();
  const [requestedSymbol, setRequestedSymbol] = useState<string | null>(null);
  const [locationReady, setLocationReady] = useState(false);
  const presets = profile?.profile?.presets ?? EMPTY_PRESETS;
  const configuredSymbols = useMemo(
    () => presets.map((preset) => normalizeWorkspaceSymbol(preset.symbol)),
    [presets],
  );
  const runtimeState = snapshot?.state ?? EMPTY_STATE;
  const snapshotFocus = normalizeWorkspaceSymbol(valueAt(runtimeState, "focus_symbol") ?? valueAt(runtimeState, "symbol"));
  const profileFocus = normalizeWorkspaceSymbol(
    presets.find((preset) => preset.id === profile?.profile?.active_preset_id)?.symbol ?? presets[0]?.symbol,
  );
  const focusSymbol = configuredSymbols.includes(snapshotFocus)
    ? snapshotFocus
    : profileFocus || snapshotFocus || configuredSymbols[0] || "";
  const normalizedRequest = normalizeWorkspaceSymbol(requestedSymbol);
  const selectedSymbol = configuredSymbols.length === 0
    ? focusSymbol
    : configuredSymbols.includes(normalizedRequest)
      ? normalizedRequest
      : focusSymbol || configuredSymbols[0];

  const pairs = useMemo(
    () => presets.map((preset) => ({
      ...pairState(preset, runtimeState, Boolean(snapshot?.stale)),
      isFocus: normalizeWorkspaceSymbol(preset.symbol) === focusSymbol,
    })),
    [focusSymbol, presets, runtimeState, snapshot?.stale],
  );
  const selectedPair = pairs.find((pair) => pair.symbol === selectedSymbol) ?? null;

  const writePairToUrl = useCallback((symbol: string) => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("pair") === symbol) return;
    url.searchParams.set("pair", symbol);
    window.history.replaceState(window.history.state, "", url);
  }, []);

  useEffect(() => {
    const readPairFromUrl = () => {
      const value = new URL(window.location.href).searchParams.get("pair");
      setRequestedSymbol(value ? normalizeWorkspaceSymbol(value) : null);
      setLocationReady(true);
    };
    readPairFromUrl();
    window.addEventListener("popstate", readPairFromUrl);
    return () => window.removeEventListener("popstate", readPairFromUrl);
  }, []);

  useEffect(() => {
    if (!locationReady || !selectedSymbol) return;
    writePairToUrl(selectedSymbol);
  }, [locationReady, pathname, selectedSymbol, writePairToUrl]);

  const selectPair = useCallback((symbol: string) => {
    const normalized = normalizeWorkspaceSymbol(symbol);
    if (!configuredSymbols.includes(normalized)) return;
    setRequestedSymbol(normalized);
    writePairToUrl(normalized);
  }, [configuredSymbols, writePairToUrl]);

  const value = useMemo<WorkspacePairContextValue>(() => ({
    pairs,
    selectedPair,
    selectedSymbol,
    selectPair,
  }), [pairs, selectPair, selectedPair, selectedSymbol]);

  return <WorkspacePairContext.Provider value={value}>{children}</WorkspacePairContext.Provider>;
}

export function useWorkspacePair(): WorkspacePairContextValue {
  const context = useContext(WorkspacePairContext);
  if (!context) throw new Error("useWorkspacePair must be used inside WorkspacePairProvider");
  return context;
}
