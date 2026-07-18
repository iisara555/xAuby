"use client";

import useSWR from "swr";
import { api, formatNumber, RuntimePrice } from "@/lib/api";
import { useEffect, useMemo, useRef, useState } from "react";

type Candle = {
  timestamp?: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  ema12?: number | null;
  ema26?: number | null;
};

type CandleResponse = {
  ok: boolean;
  symbol?: string;
  timeframe?: string;
  candles?: Candle[];
};

const TIMEFRAMES = ["1h", "4h", "1d"] as const;
const EMA_WARMUP_CANDLES = 120;

function number(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function ema(values: Array<number | null>, period: number): Array<number | null> {
  const series: Array<number | null> = Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return series;
  const seed = values.slice(0, period).filter((value): value is number => value != null);
  if (!seed.length) return series;
  let previous = seed.reduce((sum, value) => sum + value, 0) / seed.length;
  series[period - 1] = previous;
  const multiplier = 2 / (period + 1);
  for (let index = period; index < values.length; index += 1) {
    const value = values[index];
    if (value == null) continue;
    previous = (value - previous) * multiplier + previous;
    series[index] = previous;
  }
  return series;
}

function indicatorSeries(
  candles: Candle[],
  key: "ema12" | "ema26",
  period: number,
  apSmoothing: number,
): Array<number | null> {
  const closes = candles.map((item) => number(item.close));
  // CDC Action Zone V3 calculates both EMAs from AP = EMA(close, apSmoothing).
  // Use the live strategy setting so 1D and mobile render the same values as the engine.
  const source = apSmoothing >= 2 ? ema(closes, apSmoothing) : closes;
  const fallback = ema(source, period);
  return candles.map((item, index) => number(item[key]) ?? fallback[index]);
}

function formatCandleTime(timestamp: unknown, timeframe: (typeof TIMEFRAMES)[number]): string {
  const value = number(timestamp);
  if (value == null) return "—";
  const date = new Date(value < 1e12 ? value * 1000 : value);
  return new Intl.DateTimeFormat("en", timeframe === "1d"
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", hour: "2-digit" }).format(date);
}

export function CandleChart({ symbol, currentPrice, zone, apSmoothing }: { symbol: string; currentPrice: unknown; zone: string; apSmoothing?: unknown }) {
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("4h");
  const [containerWidth, setContainerWidth] = useState(760);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const indicatorSmoothing = Math.max(1, Math.min(50, Math.trunc(number(apSmoothing) ?? 1)));
  const key = `/api/v1/runtime/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${EMA_WARMUP_CANDLES}`;
  const { data, error, isLoading } = useSWR<CandleResponse>(key, api, {
    refreshInterval: () => (typeof document === "undefined" || document.visibilityState === "visible" ? 15000 : 0),
    revalidateOnFocus: false,
  });
  const { data: priceData } = useSWR<RuntimePrice>(
    `/api/v1/runtime/price?symbol=${encodeURIComponent(symbol)}`,
    api,
    {
      refreshInterval: () => (typeof document === "undefined" || document.visibilityState === "visible" ? 1000 : 0),
      dedupingInterval: 750,
      revalidateOnFocus: true,
      revalidateOnReconnect: true,
    },
  );

  useEffect(() => {
    const node = chartRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const allCandles = useMemo(() => Array.isArray(data?.candles) ? data.candles : [], [data?.candles]);
  const visibleCount = containerWidth < 360 ? 18 : containerWidth < 480 ? 24 : containerWidth < 760 ? 42 : 64;
  const { candles, fast, slow } = useMemo(() => {
    const visibleStart = Math.max(0, allCandles.length - visibleCount);
    return {
      candles: allCandles.slice(visibleStart),
      // Keep the warm-up history out of view, but use it for stable indicators.
      // Otherwise mobile widths re-seed both EMAs from a different first candle.
      fast: indicatorSeries(allCandles, "ema12", 12, indicatorSmoothing).slice(visibleStart),
      slow: indicatorSeries(allCandles, "ema26", 26, indicatorSmoothing).slice(visibleStart),
    };
  }, [allCandles, indicatorSmoothing, visibleCount]);
  const latestFast = fast[fast.length - 1];
  const latestSlow = slow[slow.length - 1];
  const livePrice = number(priceData?.price) ?? number(currentPrice);
  const chart = useMemo(() => {
    const width = Math.max(240, Math.round(containerWidth));
    const height = width < 480 ? 280 : 290;
    const plot = { left: 14, right: 76, top: 18, bottom: 34 };
    const plotWidth = width - plot.left - plot.right;
    const plotHeight = height - plot.top - plot.bottom;
    const lows = candles.map((item) => number(item.low)).filter((value): value is number => value != null);
    const highs = candles.map((item) => number(item.high)).filter((value): value is number => value != null);
    const indicators = [...fast, ...slow].filter((value): value is number => value != null);
    if (!lows.length || !highs.length) return null;
    const last = livePrice;
    const min = Math.min(...lows, ...indicators, ...(last == null ? [] : [last]));
    const max = Math.max(...highs, ...indicators, ...(last == null ? [] : [last]));
    const padding = Math.max((max - min) * 0.08, max * 0.0008);
    const floor = min - padding;
    const ceiling = max + padding;
    const y = (value: number) => plot.top + ((ceiling - value) / (ceiling - floor)) * plotHeight;
    const x = (index: number) => plot.left + (index + 0.5) * (plotWidth / candles.length);
    const labels = [0, 1, 2, 3].map((index) => {
      const candle = candles[Math.min(candles.length - 1, Math.round((candles.length - 1) * (index / 3)))];
      return { x: plot.left + plotWidth * (index / 3), label: formatCandleTime(candle?.timestamp, timeframe) };
    });
    const points = (series: Array<number | null>) => series
      .map((value, index) => value == null ? null : `${x(index)},${y(value)}`)
      .filter((value): value is string => value != null)
      .join(" ");
    return {
      width, height, plot, plotWidth, plotHeight, y, x,
      fastPoints: points(fast), slowPoints: points(slow), labels, floor, ceiling,
    };
  }, [candles, containerWidth, fast, livePrice, slow, timeframe]);

  const lastCandle = candles[candles.length - 1];
  const lastClose = number(lastCandle?.close);
  const previousClose = number(candles[candles.length - 2]?.close);
  const change = lastClose != null && previousClose ? ((lastClose - previousClose) / previousClose) * 100 : null;

  return (
    <article className="card candle-card">
      <div className="chart-toolbar">
        <div>
          <div className="card-kicker"><span>Market structure</span><span>{zone || "—"}</span></div>
          <div className="chart-title-row"><h2>{symbol}</h2><span className="chart-price">{livePrice == null ? "—" : formatNumber(livePrice, 2)}</span></div>
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
        <svg
          className="candle-chart-svg"
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          data-ema12={latestFast ?? undefined}
          data-ema26={latestSlow ?? undefined}
          data-ap-smoothing={indicatorSmoothing}
          role="img"
          aria-label={`${symbol} ${timeframe} candlestick chart. Use arrow keys to inspect candles.`}
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
          <polyline points={chart.fastPoints} className="ema-line fast" />
          <polyline points={chart.slowPoints} className="ema-line slow" />
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
      {selectedIndex != null && candles[selectedIndex] && <div className="candle-readout" aria-live="polite">
        <strong>{formatCandleTime(candles[selectedIndex].timestamp, timeframe)}</strong>
        <span>O {formatNumber(candles[selectedIndex].open)}</span><span>H {formatNumber(candles[selectedIndex].high)}</span><span>L {formatNumber(candles[selectedIndex].low)}</span><span>C {formatNumber(candles[selectedIndex].close)}</span>
      </div>}
    </article>
  );
}
