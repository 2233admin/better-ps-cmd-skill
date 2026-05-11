"use client";

import { useState } from "react";
import { runBacktest, type BacktestResponse } from "@/lib/api";
import { useStore } from "@/store";

const STRATEGIES = [
  { id: "momentum_breakout", name: "动量突破" },
  { id: "mean_reversion", name: "均值回归" },
  { id: "premium_arbitrage", name: "溢价套利" },
  { id: "pair_trading", name: "配对交易" },
];

export default function Backtest() {
  const { selectedCode } = useStore();
  const [strategy, setStrategy] = useState(STRATEGIES[0].id);
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-12-31");
  const [capital, setCapital] = useState("1000000");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [error, setError] = useState("");

  const handleRun = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await runBacktest({
        code: selectedCode,
        strategy,
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(capital),
      });
      setResult(res);
    } catch (e: any) {
      setError(e.message || "回测失败");
    } finally {
      setLoading(false);
    }
  };

  const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

  return (
    <div className="h-full flex flex-col gap-4 overflow-y-auto">
      {/* Controls */}
      <div className="bg-gray-900 rounded p-4 border border-gray-700">
        <h2 className="text-sm font-bold text-white mb-3">回测参数</h2>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">代码</label>
            <input
              value={selectedCode}
              readOnly
              className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">策略</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
            >
              {STRATEGIES.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">开始</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">结束</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleRun}
              disabled={loading}
              className="w-full px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "运行中..." : "运行回测"}
            </button>
          </div>
        </div>
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      </div>

      {/* Results */}
      {result && (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="总收益" value={pct(result.result.total_return)}
              color={result.result.total_return >= 0 ? "text-green-400" : "text-red-400"} />
            <StatCard label="年化收益" value={pct(result.result.annual_return)}
              color={result.result.annual_return >= 0 ? "text-green-400" : "text-red-400"} />
            <StatCard label="夏普比率" value={result.result.sharpe_ratio.toFixed(2)} color="text-blue-400" />
            <StatCard label="最大回撤" value={pct(result.result.max_drawdown)} color="text-yellow-400" />
            <StatCard label="胜率" value={pct(result.result.win_rate)} color="text-white" />
            <StatCard label="总交易" value={`${result.result.total_trades}`} color="text-white" />
            <StatCard label="盈利因子" value={result.result.profit_factor === Infinity ? "Inf" : result.result.profit_factor.toFixed(2)} color="text-green-400" />
            <StatCard label="策略" value={STRATEGIES.find(s => s.id === result.strategy)?.name || result.strategy} color="text-cyan-400" />
          </div>

          {/* Equity Curve (simple text-based) */}
          <div className="bg-gray-900 rounded p-4 border border-gray-700 flex-1">
            <h3 className="text-xs font-bold text-gray-400 mb-2">净值曲线 (最近100点)</h3>
            <div className="h-40 flex items-end gap-px">
              {result.equity_curve.length > 0 && (() => {
                const min = Math.min(...result.equity_curve);
                const max = Math.max(...result.equity_curve);
                const range = max - min || 1;
                return result.equity_curve.map((v, i) => (
                  <div
                    key={i}
                    className={`flex-1 min-w-[2px] ${v >= result.equity_curve[0] ? "bg-green-500" : "bg-red-500"}`}
                    style={{ height: `${((v - min) / range) * 100}%` }}
                  />
                ));
              })()}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-gray-900 rounded p-3 border border-gray-700">
      <p className="text-xs text-gray-400">{label}</p>
      <p className={`text-lg font-bold ${color}`}>{value}</p>
    </div>
  );
}
