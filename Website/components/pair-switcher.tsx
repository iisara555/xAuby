"use client";

import { Layers3, Pin } from "lucide-react";
import { usePathname } from "next/navigation";
import { useWorkspacePair } from "@/components/workspace-pair";

function strategyLabel(value: string): string {
  return value.replace(/^xauby_/, "").replaceAll("_", " ");
}

export function PairSwitcher() {
  const pathname = usePathname();
  const { pairs, selectedPair, selectedSymbol, selectPair } = useWorkspacePair();
  const supportsPairView = pathname === "/app" || pathname.startsWith("/app/activity");

  if (!supportsPairView || pairs.length < 2) return null;

  return (
    <section className="workspace-pair-dock" aria-label="Workspace market view">
      <div className="workspace-pair-intro">
        <span className="workspace-pair-icon" aria-hidden="true"><Layers3 size={17} /></span>
        <span><small>Workspace view</small><strong>{pairs.length} markets configured</strong></span>
      </div>
      <div className={`pair-switcher pair-count-${pairs.length}`} role="group" aria-label="Choose a trading pair">
        {pairs.map((pair) => {
          const active = pair.symbol === selectedSymbol;
          return (
            <button
              type="button"
              key={pair.id}
              aria-pressed={active}
              aria-controls="workspace-pair-view"
              className={active ? "active" : ""}
              onClick={() => selectPair(pair.symbol)}
              title={`${pair.symbol} · ${pair.label}`}
            >
              <i className={`pair-status-dot pair-status-${pair.tone}`} aria-hidden="true" />
              <span className="pair-button-copy">
                <strong>{pair.symbol}</strong>
                <small>{strategyLabel(pair.strategy)}</small>
              </span>
              <span className={`pair-button-state pair-state-${pair.tone}`}>{pair.status}</span>
              {pair.isFocus && <><Pin className="pair-focus-pin" size={11} aria-hidden="true" /><span className="sr-only">Default pair</span></>}
            </button>
          );
        })}
      </div>
      <div className="workspace-pair-context" aria-live="polite">
        <span>Viewing</span>
        <strong>{selectedPair?.symbol ?? selectedSymbol}</strong>
        <small>Each bot keeps its own state</small>
      </div>
    </section>
  );
}
