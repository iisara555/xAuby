"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { api, formatNumber, RuntimePrice } from "@/lib/api";

const DASHBOARD_TIMEFRAMES = ["1h", "4h", "1d"] as const;
type Timeframe = (typeof DASHBOARD_TIMEFRAMES)[number];

type Candle = Record<string, unknown> & {
  timestamp?: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

type ChartLine = {
  key: string;
  label: string;
  color?: number[];
};

type ChartZone = {
  key: string;
  label: string;
  color?: number[];
};

type CandleResponse = {
  ok: boolean;
  symbol?: string;
  timeframe?: string;
  strategy_name?: string;
  chart?: {
    zone_column?: string;
    zones?: ChartZone[];
    lines?: ChartLine[];
  };
  candles?: Candle[];
};

type CandleChartProps = {
  symbol: string;
  currentPrice: unknown;
  strategyName: string;
  primaryTimeframe?: string;
  confirmTimeframe?: string;
  marketMarker?: string;
};

function number(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fallbackLines(strategyName: string): ChartLine[] {
  const strategy = strategyName.toLowerCase();
  if (strategy === "supertrend_ema200") {
    return [
      { key: "supertrend", label: "SuperTrend", color: [34, 197, 94] },
      { key: "ema200", label: "EMA200", color: [251, 191, 36] },
    ];
  }
  if (strategy === "xauby_actionzone" || strategy === "cdc_action_zone") {
    return [
      { key: "ema12", label: "EMA12 (AP)", color: [168, 85, 247] },
      { key: "ema26", label: "EMA26 (AP)", color: [56, 189, 248] },
    ];
  }
  return [];
}

function rgb(value: unknown, fallback = "#8a877f"): string {
  if (!Array.isArray(value) || value.length !== 3) return fallback;
  const channels = value.map((item) => Number(item));
  return channels.every(Number.isFinite) ? `rgb(${channels.join(", ")})` : fallback;
}

function formatCandleTime(timestamp: unknown, selectedTimeframe: Timeframe): string {
  const value = number(timestamp);
  if (value == null) return "—";
  const date = new Date(value < 1e12 ? value * 1000 : value);
  const day = new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(date);
  if (selectedTimeframe === "1d") return day;
  const hour = new Intl.DateTimeFormat("en", { hour: "2-digit", hour12: false }).format(date);
  return `${day} ${hour}h`;
}

export function CandleChart({
  symbol,
  currentPrice,
  strategyName,
  marketMarker = "—",
}: CandleChartProps) {
  const [selectedTimeframe, setSelectedTimeframe] = useState<Timeframe>("4h");
  const [containerWidth, setContainerWidth] = useState(760);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const normalizedSymbol = symbol.trim();
  const candleKey = normalizedSymbol
    ? `/api/v1/runtime/candles?symbol=${encodeURIComponent(normalizedSymbol)}&timeframe=${selectedTimeframe}&limit=120`
    : null;
  const priceKey = normalizedSymbol
    ? `/api/v1/runtime/price?symbol=${encodeURIComponent(normalizedSymbol)}`
    : null;
  const { data, error, isLoading } = useSWR<CandleResponse>(candleKey, api, {
    refreshInterval: () => (typeof document === "undefined" || document.visibilityState === "visible" ? 15000 : 0),
    revalidateOnFocus: false,
  });
  const { data: priceData } = useSWR<RuntimePrice>(priceKey, api, {
    refreshInterval: () => (typeof document === "undefined" || document.visibilityState === "visible" ? 1000 : 0),
    dedupingInterval: 750,
    revalidateOnFocus: true,
    revalidateOnReconnect: true,
  });

  useEffect(() => {
    const node = chartRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const allCandles = useMemo(() => Array.isArray(data?.candles) ? data.candles : [], [data?.candles]);
  const visibleCount = containerWidth < 360 ? 18 : containerWidth < 480 ? 24 : containerWidth < 760 ? 42 : 64;
  const visibleStart = Math.max(0, allCandles.length - visibleCount);
  const candles = useMemo(() => allCandles.slice(visibleStart), [allCandles, visibleStart]);
  const lines = useMemo(() => {
    const metadata = data?.chart?.lines;
    return Array.isArray(metadata) && metadata.length ? metadata : fallbackLines(data?.strategy_name || strategyName);
  }, [data?.chart?.lines, data?.strategy_name, strategyName]);
  const renderedLines = useMemo(() => lines.map((line) => ({
    ...line,
    values: allCandles.map((candle) => number(candle[line.key])).slice(visibleStart),
  })), [allCandles, lines, visibleStart]);
  const zoneColumn = String(data?.chart?.zone_column ?? "");
  const zoneColors = useMemo(() => new Map(
    (data?.chart?.zones ?? []).map((zone) => [String(zone.key).toUpperCase(), rgb(zone.color)]),
  ), [data?.chart?.zones]);
  const latestMarker = String(candles[candles.length - 1]?.[zoneColumn] ?? marketMarker ?? "—");
  const livePrice = number(priceData?.price) ?? number(currentPrice);

  const chart = useMemo(() => {
    const width = Math.max(240, Math.round(containerWidth));
    const height = width < 480 ? 280 : 290;
    const plot = { left: 14, right: 76, top: 18, bottom: 34 };
    const plotWidth = width - plot.left - plot.right;
    const plotHeight = height - plot.top - plot.bottom;
    const lows = candles.map((item) => number(item.low)).filter((value): value is number => value != null);
    const highs = candles.map((item) => number(item.high)).filter((value): value is number => value != null);
    const indicators = renderedLines.flatMap((line) => line.values).filter((value): value is number => value != null);
    if (!lows.length || !highs.length) return null;
    const min = Math.min(...lows, ...indicators, ...(livePrice == null ? [] : [livePrice]));
    const max = Math.max(...highs, ...indicators, ...(livePrice == null ? [] : [livePrice]));
    const padding = Math.max((max - min) * 0.08, max * 0.0008, 0.01);
    const floor = min - padding;
    const ceiling = max + padding;
    const y = (value: number) => plot.top + ((ceiling - value) / (ceiling - floor)) * plotHeight;
    const x = (index: number) => plot.left + (index + 0.5) * (plotWidth / candles.length);
    const labelCount = containerWidth < 480 ? 3 : 4;
    const labels = Array.from({ length: labelCount }, (_, index) => {
      const fraction = index / (labelCount - 1);
      const candle = candles[Math.min(candles.length - 1, Math.round((candles.length - 1) * fraction))];
      return { x: plot.left + plotWidth * fraction, label: formatCandleTime(candle?.timestamp, selectedTimeframe) };
    });
    const point = (value: number, index: number) => `${x(index)},${y(value)}`;
    const linePaths = renderedLines.map((line) => {
      const points = line.values
        .map((value, index) => value == null ? null : point(value, index))
        .filter((value): value is string => value != null)
        .join(" ");
      if (!zoneColumn || !line.key.toLowerCase().includes("supertrend")) {
        return { ...line, points, segments: [] as Array<{ points: string; color: string }> };
      }
      const segments: Array<{ points: string; color: string }> = [];
      let segmentZone = "";
      let segmentPoints: string[] = [];
      let previousPoint = "";
      line.values.forEach((value, index) => {
        if (value == null) return;
        const nextPoint = point(value, index);
        const nextZone = String(candles[index]?.[zoneColumn] ?? "").toUpperCase();
        if (segmentPoints.length && nextZone !== segmentZone) {
          segments.push({ points: segmentPoints.join(" "), color: zoneColors.get(segmentZone) ?? rgb(line.color) });
          segmentPoints = previousPoint ? [previousPoint, nextPoint] : [nextPoint];
        } else {
          segmentPoints.push(nextPoint);
        }
        segmentZone = nextZone;
        previousPoint = nextPoint;
      });
      if (segmentPoints.length) {
        segments.push({ points: segmentPoints.join(" "), color: zoneColors.get(segmentZone) ?? rgb(line.color) });
      }
      return { ...line, points, segments };
    });
    return { width, height, plot, plotWidth, plotHeight, y, x, labels, floor, ceiling, linePaths };
  }, [candles, containerWidth, livePrice, renderedLines, selectedTimeframe, zoneColors, zoneColumn]);

  const lastCandle = candles[candles.length - 1];
  const lastClose = number(lastCandle?.close);
  const previousClose = number(candles[candles.length - 2]?.close);
  const change = lastClose != null && previousClose ? ((lastClose - previousClose) / previousClose) * 100 : null;
  const indicatorLabel = lines.length ? lines.map((line) => line.label).join(" / ") : "Price";
  const selectedCandle = selectedIndex == null ? null : candles[selectedIndex];

  return (
    <article className="card candle-card">
      <div className="chart-toolbar">
        <div>
          <div className="card-kicker"><span>Strategy chart</span><span>{latestMarker || "—"}</span></div>
          <div className="chart-title-row"><h2>{symbol}</h2><span className="chart-price">{livePrice == null ? "—" : formatNumber(livePrice, 2)}</span></div>
          <p className="chart-subtitle">Candles · {indicatorLabel} · {selectedTimeframe.toUpperCase()}</p>
        </div>
        <div className="chart-timeframes" aria-label="Strategy timeframe">
          {DASHBOARD_TIMEFRAMES.map((item) => <button
            type="button"
            className={item === selectedTimeframe ? "selected" : ""}
            aria-pressed={item === selectedTimeframe}
            onClick={() => { setSelectedTimeframe(item); setSelectedIndex(null); }}
            key={item}
          >{item.toUpperCase()}</button>)}
        </div>
      </div>
      <div className="chart-meta-row">
        <span><i className="chart-dot candle-up" />Up candle</span>
        {lines.map((line) => <span key={line.key}><i className="chart-dot" style={{ backgroundColor: rgb(line.color) }} />{line.label}</span>)}
        <strong className={change != null && change >= 0 ? "positive" : "negative"}>{change == null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}</strong>
      </div>
      <div className="chart-stage" ref={chartRef}>
      {isLoading && !chart ? <div className="chart-empty">Loading strategy data…</div> : error || !chart ? <div className="chart-empty">Strategy chart is temporarily unavailable.</div> : (
        <svg
          className="candle-chart-svg"
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          data-strategy={data?.strategy_name || strategyName}
          role="img"
          aria-label={`${symbol} ${selectedTimeframe} candlestick chart with ${indicatorLabel}. Use arrow keys to inspect candles.`}
          tabIndex={0}
          onKeyDown={(event) => {
            if (!candles.length) return;
            const last = candles.length - 1;
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              const step = event.key === "ArrowLeft" ? -1 : 1;
              setSelectedIndex((current) => Math.min(last, Math.max(0, (current ?? last) + step)));
            } else if (event.key === "Home") {
              event.preventDefault(); setSelectedIndex(0);
            } else if (event.key === "End") {
              event.preventDefault(); setSelectedIndex(last);
            } else if (event.key === "Escape") {
              setSelectedIndex(null);
            }
          }}
        >
          {[0, 1, 2, 3, 4].map((step) => {
            const value = chart.ceiling - ((chart.ceiling - chart.floor) * step) / 4;
            return <g key={step}><line x1={chart.plot.left} x2={chart.width - chart.plot.right} y1={chart.y(value)} y2={chart.y(value)} className="chart-grid-line" /><text x={chart.width - chart.plot.right + 8} y={chart.y(value) + 4} className="chart-axis-label">{formatNumber(value, 0)}</text></g>;
          })}
          {chart.labels.map((item, index) => <text key={`${item.label}-${index}`} x={item.x} y={chart.height - 6} textAnchor={index === 0 ? "start" : index === chart.labels.length - 1 ? "end" : "middle"} className="chart-axis-label">{item.label}</text>)}
          {candles.map((candle, index) => {
            const open = number(candle.open) ?? candle.close;
            const close = number(candle.close) ?? open;
            const high = number(candle.high) ?? Math.max(open, close);
            const low = number(candle.low) ?? Math.min(open, close);
            const slot = chart.plotWidth / candles.length;
            const bodyWidth = Math.max(3, Math.min(12, slot * 0.55));
            const bodyTop = Math.min(chart.y(open), chart.y(close));
            const bodyHeight = Math.max(1.5, Math.abs(chart.y(open) - chart.y(close)));
            const positive = close >= open;
            return <g key={`${candle.timestamp ?? index}-${close}`} className={selectedIndex === index ? "selected-candle" : ""} onMouseEnter={() => setSelectedIndex(index)} onClick={() => setSelectedIndex(index)}>
              <line x1={chart.x(index)} x2={chart.x(index)} y1={chart.y(high)} y2={chart.y(low)} className={positive ? "candle-wick up" : "candle-wick down"} />
              <rect x={chart.x(index) - bodyWidth / 2} y={bodyTop} width={bodyWidth} height={bodyHeight} rx="1" className={positive ? "candle-body up" : "candle-body down"} />
            </g>;
          })}
          {chart.linePaths.map((line) => line.segments.length
            ? line.segments.map((segment, index) => <polyline key={`${line.key}-${index}`} points={segment.points} className="strategy-line" style={{ stroke: segment.color }} data-indicator-key={line.key} />)
            : <polyline key={line.key} points={line.points} className="strategy-line" style={{ stroke: rgb(line.color) }} data-indicator-key={line.key} />)}
          {livePrice != null && (() => {
            const lineY = chart.y(livePrice);
            const labelY = Math.max(chart.plot.top + 10, Math.min(chart.height - chart.plot.bottom - 10, lineY));
            const labelX = chart.width - chart.plot.right + 5;
            return <g className={priceData?.stale ? "current-price stale" : "current-price"}>
              <line x1={chart.plot.left} x2={chart.width - chart.plot.right} y1={lineY} y2={lineY} className="current-price-line" />
              <rect x={labelX} y={labelY - 10} width={chart.plot.right - 10} height="20" rx="4" className="current-price-badge" />
              <text x={labelX + (chart.plot.right - 10) / 2} y={labelY + 4} textAnchor="middle" className="current-price-label">{formatNumber(livePrice, 2)}</text>
            </g>;
          })()}
        </svg>
      )}
      </div>
      {selectedCandle && <div className="candle-readout" aria-live="polite">
        <strong>{formatCandleTime(selectedCandle.timestamp, selectedTimeframe)}</strong>
        <span>O {formatNumber(selectedCandle.open)}</span><span>H {formatNumber(selectedCandle.high)}</span><span>L {formatNumber(selectedCandle.low)}</span><span>C {formatNumber(selectedCandle.close)}</span>
        {lines.map((line) => number(selectedCandle[line.key]) == null ? null : <span key={line.key}>{line.label} {formatNumber(selectedCandle[line.key])}</span>)}
      </div>}
    </article>
  );
}
