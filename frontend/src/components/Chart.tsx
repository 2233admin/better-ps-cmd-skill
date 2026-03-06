"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries, HistogramSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, CandlestickData, HistogramData, Time } from "lightweight-charts";
import { getKline, getOkxKline, KlineBar } from "@/lib/api";
import { useStore } from "@/store";

interface ChartProps {
  market?: "astock" | "crypto";
  pair?: string;
}

export default function Chart({ market = "astock", pair = "BTC-USDT" }: ChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const candleSeries = useRef<ISeriesApi<any> | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const volumeSeries = useRef<ISeriesApi<any> | null>(null);
  const { selectedCode } = useStore();
  const [loading, setLoading] = useState(false);

  // A股周期
  const [category, setCategory] = useState(9);
  // Crypto周期
  const [cryptoBar, setCryptoBar] = useState("1m");

  const isCrypto = market === "crypto";

  // 红涨绿跌(A股) vs 绿涨红跌(Crypto)
  const upColor = isCrypto ? "#0ecb81" : "#ef4444";
  const downColor = isCrypto ? "#f6465d" : "#22c55e";

  useEffect(() => {
    if (!chartRef.current) return;

    const chart = createChart(chartRef.current, {
      layout: {
        background: { color: "#0f1117" },
        textColor: "#d1d5db",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: "#374151" },
      timeScale: { borderColor: "#374151", timeVisible: true },
    });

    candleSeries.current = chart.addSeries(CandlestickSeries, {
      upColor, downColor,
      borderUpColor: upColor, borderDownColor: downColor,
      wickUpColor: upColor, wickDownColor: downColor,
    });

    volumeSeries.current = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });

    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartApi.current = chart;

    const handleResize = () => {
      if (chartRef.current) {
        chart.applyOptions({
          width: chartRef.current.clientWidth,
          height: chartRef.current.clientHeight,
        });
      }
    };
    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 更新颜色
  useEffect(() => {
    if (candleSeries.current) {
      candleSeries.current.applyOptions({
        upColor, downColor,
        borderUpColor: upColor, borderDownColor: downColor,
        wickUpColor: upColor, wickDownColor: downColor,
      });
    }
  }, [upColor, downColor]);

  // 加载数据
  useEffect(() => {
    if (!candleSeries.current || !volumeSeries.current) return;

    const loadData = async () => {
      setLoading(true);
      try {
        let bars: KlineBar[];
        if (isCrypto) {
          const res = await getOkxKline(pair, cryptoBar, 300);
          bars = res.data;
        } else {
          if (!selectedCode) return;
          const res = await getKline(selectedCode, category, 800);
          bars = res.data;
        }

        const candles: CandlestickData<Time>[] = bars.map((bar) => ({
          time: (isCrypto
            ? Math.floor(new Date(bar.datetime + "Z").getTime() / 1000)
            : bar.datetime.slice(0, 10)) as Time,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        }));

        const volumes: HistogramData<Time>[] = bars.map((bar) => ({
          time: (isCrypto
            ? Math.floor(new Date(bar.datetime + "Z").getTime() / 1000)
            : bar.datetime.slice(0, 10)) as Time,
          value: bar.volume,
          color: bar.close >= bar.open ? upColor + "80" : downColor + "80",
        }));

        candleSeries.current!.setData(candles);
        volumeSeries.current!.setData(volumes);
        chartApi.current?.timeScale().fitContent();
      } catch (e) {
        console.error("Chart load error:", e);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [isCrypto, pair, cryptoBar, selectedCode, category, upColor, downColor]);

  const astockCategories = [
    { label: "1分", value: 7 },
    { label: "5分", value: 0 },
    { label: "15分", value: 1 },
    { label: "30分", value: 2 },
    { label: "日K", value: 9 },
    { label: "周K", value: 5 },
  ];

  const cryptoBars = [
    { label: "1m", value: "1m" },
    { label: "5m", value: "5m" },
    { label: "15m", value: "15m" },
    { label: "1H", value: "1H" },
    { label: "4H", value: "4H" },
    { label: "1D", value: "1D" },
  ];

  const title = isCrypto ? pair : selectedCode;

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-lg border border-gray-700">
      <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-700">
        <span className="text-sm font-bold text-white">{title}</span>
        <div className="flex gap-1 ml-4">
          {isCrypto
            ? cryptoBars.map((c) => (
                <button
                  key={c.value}
                  onClick={() => setCryptoBar(c.value)}
                  className={`px-2 py-1 text-xs rounded ${
                    cryptoBar === c.value
                      ? "bg-yellow-600 text-white"
                      : "text-gray-400 hover:bg-gray-700"
                  }`}
                >
                  {c.label}
                </button>
              ))
            : astockCategories.map((c) => (
                <button
                  key={c.value}
                  onClick={() => setCategory(c.value)}
                  className={`px-2 py-1 text-xs rounded ${
                    category === c.value
                      ? "bg-blue-600 text-white"
                      : "text-gray-400 hover:bg-gray-700"
                  }`}
                >
                  {c.label}
                </button>
              ))}
        </div>
        {loading && <span className="text-xs text-yellow-400 ml-2">Loading...</span>}
      </div>
      <div ref={chartRef} className="flex-1" />
    </div>
  );
}
