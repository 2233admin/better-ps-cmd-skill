"use client";

import { useEffect, useState } from "react";
import { getStrategies, startStrategy, stopStrategy, Strategy as StrategyType } from "@/lib/api";

export default function Strategy() {
  const [strategies, setStrategies] = useState<StrategyType[]>([]);

  const load = async () => {
    try {
      const res = await getStrategies();
      setStrategies(res.data);
    } catch {
      // API not available
    }
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const toggle = async (name: string, state: string) => {
    try {
      if (state === "running") {
        await stopStrategy(name);
      } else {
        await startStrategy(name);
      }
      load();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
      <h3 className="text-sm font-bold text-white mb-3">策略中心</h3>
      <div className="space-y-2">
        {strategies.map((s) => (
          <div
            key={s.name}
            className="flex items-center justify-between p-2 bg-gray-800 rounded"
          >
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    s.state === "running"
                      ? "bg-green-400"
                      : s.state === "error"
                      ? "bg-red-400"
                      : "bg-gray-500"
                  }`}
                />
                <span className="text-xs text-white font-medium">{s.name}</span>
              </div>
              <p className="text-xs text-gray-400 mt-0.5 ml-4">{s.description}</p>
              <div className="flex gap-3 ml-4 mt-1">
                <span className="text-xs text-gray-500">
                  PnL:{" "}
                  <span className={s.pnl >= 0 ? "text-red-400" : "text-green-400"}>
                    {s.pnl >= 0 ? "+" : ""}
                    {s.pnl.toFixed(2)}
                  </span>
                </span>
                <span className="text-xs text-gray-500">
                  成交: {s.trade_count}
                </span>
              </div>
            </div>
            <button
              onClick={() => toggle(s.name, s.state)}
              className={`px-3 py-1 rounded text-xs font-medium ${
                s.state === "running"
                  ? "bg-red-600/20 text-red-400 hover:bg-red-600/30"
                  : "bg-green-600/20 text-green-400 hover:bg-green-600/30"
              }`}
            >
              {s.state === "running" ? "停止" : "启动"}
            </button>
          </div>
        ))}
        {strategies.length === 0 && (
          <p className="text-xs text-gray-500 text-center py-4">
            启动后端后可查看策略
          </p>
        )}
      </div>
    </div>
  );
}
