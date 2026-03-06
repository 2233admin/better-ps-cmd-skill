"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import BondList from "@/components/BondList";
import OrderBook from "@/components/OrderBook";
import Portfolio from "@/components/Portfolio";
import Strategy from "@/components/Strategy";
import AIPanel from "@/components/AIPanel";
import StatusBar from "@/components/StatusBar";
import { useStore } from "@/store";

const Chart = dynamic(() => import("@/components/Chart"), { ssr: false });
const Backtest = dynamic(() => import("@/components/Backtest"), { ssr: false });
const MacroBriefing = dynamic(() => import("@/components/MacroBriefing"), { ssr: false });
const OkxTrading = dynamic(() => import("@/components/OkxTrading"), { ssr: false });

type Tab = "market" | "strategy" | "backtest" | "macro" | "crypto";
type Market = "astock" | "crypto";

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("market");
  const { selectedCode, setSelectedCode } = useStore();
  const [codeInput, setCodeInput] = useState(selectedCode);
  const [market, setMarket] = useState<Market>("astock");
  const [selectedPair, setSelectedPair] = useState("BTC-USDT");

  // 从 localStorage 恢复市场状态
  useEffect(() => {
    const saved = localStorage.getItem("qt-market");
    if (saved === "crypto" || saved === "astock") setMarket(saved);
  }, []);

  const handleMarketChange = (m: Market) => {
    setMarket(m);
    localStorage.setItem("qt-market", m);
  };

  const handleCodeSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (codeInput.trim()) setSelectedCode(codeInput.trim());
  };

  return (
    <div className="h-screen flex flex-col bg-[#0a0a0a]">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700">
        <div className="flex items-center gap-4">
          <h1 className="text-base font-bold text-white">QuantTerminal</h1>

          {/* 市场切换 */}
          {activeTab === "market" && (
            <div className="flex bg-gray-800 rounded p-0.5">
              <button
                onClick={() => handleMarketChange("astock")}
                className={`px-2.5 py-1 text-xs rounded font-medium transition-colors ${
                  market === "astock"
                    ? "bg-blue-600 text-white"
                    : "text-gray-400 hover:text-white"
                }`}
              >
                A股
              </button>
              <button
                onClick={() => handleMarketChange("crypto")}
                className={`px-2.5 py-1 text-xs rounded font-medium transition-colors ${
                  market === "crypto"
                    ? "bg-yellow-600 text-white"
                    : "text-gray-400 hover:text-white"
                }`}
              >
                Crypto
              </button>
            </div>
          )}

          {/* 代码输入 (A股模式) */}
          {activeTab === "market" && market === "astock" && (
            <form onSubmit={handleCodeSubmit} className="flex items-center gap-1">
              <input
                type="text"
                value={codeInput}
                onChange={(e) => setCodeInput(e.target.value)}
                placeholder="输入代码..."
                className="w-28 px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
              />
              <button
                type="submit"
                className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                GO
              </button>
            </form>
          )}
        </div>

        {/* Tabs */}
        <div className="flex gap-1">
          {[
            { id: "market" as Tab, label: "行情看板" },
            { id: "strategy" as Tab, label: "策略中心" },
            { id: "backtest" as Tab, label: "回测报告" },
            { id: "macro" as Tab, label: "宏观分析" },
            { id: "crypto" as Tab, label: "Crypto交易" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1 text-xs rounded ${
                activeTab === tab.id
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:bg-gray-800"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 overflow-hidden">
        {activeTab === "market" && (
          <div className="h-full grid grid-cols-12 gap-1 p-1">
            {/* Left: List */}
            <div className="col-span-3 h-full overflow-hidden">
              <BondList market={market} onSelectPair={setSelectedPair} />
            </div>

            {/* Center: Chart + Portfolio */}
            <div className="col-span-6 flex flex-col gap-1 h-full">
              <div className="flex-1 min-h-0">
                <Chart market={market} pair={selectedPair} />
              </div>
              <div className="h-52 flex-shrink-0">
                <Portfolio market={market} />
              </div>
            </div>

            {/* Right: OrderBook + AI + Strategy */}
            <div className="col-span-3 flex flex-col gap-1 h-full overflow-y-auto">
              <OrderBook market={market} pair={selectedPair} />
              {market === "astock" && (
                <>
                  <AIPanel />
                  <Strategy />
                </>
              )}
            </div>
          </div>
        )}

        {activeTab === "strategy" && (
          <div className="h-full p-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2"><Strategy /></div>
            </div>
          </div>
        )}

        {activeTab === "backtest" && (
          <div className="h-full p-4"><Backtest /></div>
        )}

        {activeTab === "macro" && (
          <div className="h-full p-4"><MacroBriefing /></div>
        )}

        {activeTab === "crypto" && (
          <div className="h-full p-2 overflow-y-auto"><OkxTrading /></div>
        )}
      </main>

      <StatusBar />
    </div>
  );
}
