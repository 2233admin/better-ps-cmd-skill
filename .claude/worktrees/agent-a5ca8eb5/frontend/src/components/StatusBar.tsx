"use client";

import { useEffect, useState } from "react";

export default function StatusBar() {
  const [health, setHealth] = useState<{
    status: string;
    tdx_connected: boolean;
  } | null>(null);
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const res = await fetch("http://localhost:8000/health");
        const data = await res.json();
        setHealth(data);
      } catch {
        setHealth(null);
      }
    };
    checkHealth();
    const healthInterval = setInterval(checkHealth, 10000);

    const timeInterval = setInterval(() => setTime(new Date()), 1000);

    return () => {
      clearInterval(healthInterval);
      clearInterval(timeInterval);
    };
  }, []);

  const isTrading = () => {
    const h = time.getHours();
    const m = time.getMinutes();
    const t = h * 60 + m;
    return (t >= 570 && t <= 690) || (t >= 780 && t <= 900); // 9:30-11:30, 13:00-15:00
  };

  return (
    <div className="flex items-center justify-between px-4 py-1 bg-gray-900 border-t border-gray-700 text-xs">
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1">
          <span
            className={`w-2 h-2 rounded-full ${
              health?.status === "ok" ? "bg-green-400" : "bg-red-400"
            }`}
          />
          后端: {health?.status === "ok" ? "在线" : "离线"}
        </span>
        <span className="flex items-center gap-1">
          <span
            className={`w-2 h-2 rounded-full ${
              health?.tdx_connected ? "bg-green-400" : "bg-yellow-400"
            }`}
          />
          TDX: {health?.tdx_connected ? "已连接" : "未连接"}
        </span>
        <span className="text-gray-400">
          模式: <span className="text-yellow-400">模拟盘</span>
        </span>
      </div>
      <div className="flex items-center gap-4">
        <span className={isTrading() ? "text-green-400" : "text-gray-500"}>
          {isTrading() ? "交易中" : "已休市"}
        </span>
        <span className="text-gray-300 font-mono">
          {time.toLocaleTimeString("zh-CN", { hour12: false })}
        </span>
      </div>
    </div>
  );
}
