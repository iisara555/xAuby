"use client";

import useSWR from "swr";
import { api, formatNumber } from "@/lib/api";
import { useEffect, useMemo, useRef, useState } from "react";

type Candle = {
  timestamp?: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

type CandleResponse = {
  ok: boolean;
  symbol?: string;
  timeframe?: string;
  candles?: Candle[];
};

const TIMEFRAMES = ["1h", "4h", "1d"] as const;

function number(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function ema(values: number[], period: number): number[] {
  if (!values.length) return [];
  const multiplier = 2 / (period + 1);
  let previous = values[0];
  return values.map((value, index) => {
    if (index === 0) return previous;
    previous = (value - previous) * multiplier + previous;
    return previous;
  });
}

function formatCandleTime(timestamp: unknown): string {
  const value = number(timestamp);
  if (value == null) return "—";
  const date = new Date(value < 1e12 ? value * 1000 : value);
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(date);
}

export function CandleChart({ symbol, currentPrice, zone }: { symbol: string; currentPrice: unknown; zone: string }) {
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("4h");
  const [containerWidth, setContainerWidth] = useState(760);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const key = `/api/v1/runtime/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=64`;
  const { data, error, isLoading } = useSWR<CandleResponse>(key, api, {
    refreshInterval: () => (typeof document === "undefined" || document.visibilityState === "visible" ? 15000 : 0),
    revalidateOnFocus: false,
  });

  useEffect(() => {
    const node = chartRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const allCandles = Array.isArray(data?.candles) ? data.candles : [];
  const visibleCount = containerWidth < 480 ? 28 : containerWidth < 760 ? 42 : 64;
  const candles = allCandles.slice(-visibleCount);
  const chart = useMemo(() => {
    const width = Math.max(320, Math.round(containerWidth));
    const height = width < 480 ? 230 : 290;
    const plot = { left: 14, right: 62, top: 18, bottom: 30 };
    const plotWidth = width - plot.left - plot.right;
    const plotHeight = height - plot.top - plot.bottom;
    const lows = candles.map((item) => number(item.low)).filter((value): value is number => value != null);
    const highs = candles.map((item) => number(item.high)).filter((value): value is number => value != null);
    if (!lows.length || !highs.length) return null;
    const last = number(currentPrice);
    const min = Math.min(...lows, ...(last == null ? [] : [last]));
    const max = Math.max(...highs, ...(last == null ? [] : [last]));
    const padding = Math.max((max - min) * 0.08, max * 0.0008);
    const floor = min - padding;
    const ceiling = max + padding;
    const y = (value: number) => plot.top + ((ceiling - value) / (ceiling - floor)) * plotHeight;
    const x = (index: number) => plot.left + (index + 0.5) * (plotWidth / candles.length);
    const closes = candles.map((item) => number(item.close) ?? 0);
    const fast = ema(closes, 12);
    const slow = ema(closes, 26);
    const labels = [0, 1, 2, 3].map((index) => {
      const candle = candles[Math.min(candles.length - 1, Math.round((candles.length - 1) * (index / 3)))];
      return { x: plot.left + plotWidth * (index / 3), label: formatCandleTime(candle?.timestamp) };
    });
    return { width, height, plot, plotWidth, plotHeight, y, x, fast, slow, labels, floor, ceiling };
  }, [candles, currentPrice]);

  const lastCandle = candles[candles.length - 1];
  const lastClose = number(lastCandle?.close);
  const previousClose = number(candles[candles.length - 2]?.close);
  const change = lastClose != null && previousClose ? ((lastClose - previousClose) / previousClose) * 100 : null;

  return (
    <article className="card candle-card">
      <div className="chart-toolbar">
        <div>
          <div className="card-kicker"><span>Market structure</span><span>{zone || "—"}</span></div>
          <div className="chart-title-row"><h2>{symbol}</h2><span className="chart-price">{lastClose == null ? "—" : formatNumber(lastClose, 2)}</span></div>
          <p className="chart-subtitle">Candles · EMA12 / EMA26 · {timeframe.toUpperCase()}</p>
        </div>
        <div className="chart-timeframes" aria-label="Candle timeframe">
          {TIMEFRAMES.map((item) => <button type="button" className={item === timeframe ? "selected" : ""} onClick={() => setTimeframe(item)} key={item}>{item.toUpperCase()}</button>)}
        </div>
      </div>
      <div className="chart-meta-row">
        <span><i className="chart-dot candle-up" />Up candle</span>
        <span><i className="chart-dot ema-fast-dot" />EMA12</span>
        <span><i className="chart-dot ema-slow-dot" />EMA26</span>
        <strong className={change != null && change >= 0 ? "positive" : "negative"}>{change == null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}</strong>
      </div>
      <div className="chart-stage" ref={chartRef}>
      {isLoading && !chart ? <div className="chart-empty">Loading market data…</div> : error || !chart ? <div className="chart-empty">Market data is temporarily unavailable.</div> : (
        <svg className="candle-chart-svg" viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={`${symbol} ${timeframe} candlestick chart`}>
          {[0, 1, 2, 3, 4].map((step) => {
            const value = chart.ceiling - ((chart.ceiling - chart.floor) * step) / 4;
            return <g key={step}><line x1={chart.plot.left} x2={chart.width - chart.plot.right} y1={chart.y(value)} y2={chart.y(value)} className="chart-grid-line" /><text x={chart.width - chart.plot.right + 8} y={chart.y(value) + 4} className="chart-axis-label">{formatNumber(value, 0)}</text></g>;
          })}
          {chart.labels.map((item, index) => <text key={index} x={item.x} y={chart.height - 6} textAnchor={index === 0 ? "start" : index === 3 ? "end" : "middle"} className="chart-axis-label">{item.label}</text>)}
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
          <polyline points={chart.fast.map((value, index) => `${chart.x(index)},${chart.y(value)}`).join(" ")} className="ema-line fast" />
          <polyline points={chart.slow.map((value, index) => `${chart.x(index)},${chart.y(value)}`).join(" ")} className="ema-line slow" />
          {number(currentPrice) != null && <g><line x1={chart.plot.left} x2={chart.width - chart.plot.right} y1={chart.y(number(currentPrice) as number)} y2={chart.y(number(currentPrice) as number)} className="current-price-line" /><text x={chart.width - chart.plot.right + 8} y={chart.y(number(currentPrice) as number) - 5} className="current-price-label">LIVE</text></g>}
        </svg>
      )}
      </div>
      {selectedIndex != null && candles[selectedIndex] && <div className="candle-readout" aria-live="polite">
        <strong>{formatCandleTime(candles[selectedIndex].timestamp)}</strong>
        <span>O {formatNumber(candles[selectedIndex].open)}</span><span>H {formatNumber(candles[selectedIndex].high)}</span><span>L {formatNumber(candles[selectedIndex].low)}</span><span>C {formatNumber(candles[selectedIndex].close)}</span>
      </div>}
    </article>
  );
}
