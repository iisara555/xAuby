"use client";

export function PairSwitcher({
  symbols,
  selected,
  onSelect,
}: {
  symbols: string[];
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  if (symbols.length < 2) return null;
  return (
    <div className="pair-switcher" role="tablist" aria-label="Trading pairs">
      {symbols.map((symbol) => (
        <button
          type="button"
          key={symbol}
          role="tab"
          aria-selected={symbol === selected}
          className={symbol === selected ? "active" : ""}
          onClick={() => onSelect(symbol)}
        >
          {symbol}
        </button>
      ))}
    </div>
  );
}
