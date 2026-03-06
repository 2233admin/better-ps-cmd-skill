"use client";

import { useState, useEffect } from "react";
import {
  getMacroIndicators,
  fetchMacroIndicators,
  generateBriefing,
  getBriefingHistory,
  getAllocation,
  getIndicatorRegistry,
  getIndicatorHistory,
  MacroIndicator,
  MacroBriefing as BriefingType,
  AllocationRecord,
} from "@/lib/api";

const CATEGORY_COLORS: Record<string, string> = {
  monetary: "text-blue-400",
  price: "text-yellow-400",
  growth: "text-green-400",
  trade: "text-purple-400",
  sentiment: "text-red-400",
};

const CATEGORY_NAMES: Record<string, string> = {
  monetary: "货币政策",
  price: "价格指标",
  growth: "经济增长",
  trade: "贸易",
  sentiment: "市场情绪",
};

export default function MacroBriefing() {
  const [indicators, setIndicators] = useState<MacroIndicator[]>([]);
  const [briefing, setBriefing] = useState<BriefingType | null>(null);
  const [allocation, setAllocation] = useState<AllocationRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetchingData, setFetchingData] = useState(false);
  const [activeSection, setActiveSection] = useState<"indicators" | "briefing" | "allocation">("indicators");
  const [selectedHistory, setSelectedHistory] = useState<{ code: string; data: { date: string; value: number }[] } | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      const [indRes, allocRes, briefRes] = await Promise.all([
        getMacroIndicators(),
        getAllocation(),
        getBriefingHistory(1),
      ]);
      setIndicators(indRes.data);
      setAllocation(allocRes.data);
      if (briefRes.data.length > 0) setBriefing(briefRes.data[0]);
    } catch {
      // 首次无数据是正常的
    }
  }

  async function handleFetchData() {
    setFetchingData(true);
    try {
      await fetchMacroIndicators();
      const res = await getMacroIndicators();
      setIndicators(res.data);
    } catch (e: any) {
      alert(`采集失败: ${e.message}`);
    }
    setFetchingData(false);
  }

  async function handleGenerateBriefing() {
    setLoading(true);
    try {
      const res = await generateBriefing();
      setBriefing(res);
    } catch (e: any) {
      alert(`生成失败: ${e.message}`);
    }
    setLoading(false);
  }

  async function handleShowHistory(code: string) {
    try {
      const res = await getIndicatorHistory(code);
      setSelectedHistory({ code, data: res.data });
    } catch {}
  }

  // 按分类分组指标
  const grouped: Record<string, MacroIndicator[]> = {};
  for (const ind of indicators) {
    (grouped[ind.category] ??= []).push(ind);
  }

  return (
    <div className="h-full flex flex-col bg-gray-900 rounded text-sm">
      {/* Tabs */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-gray-700">
        <h2 className="text-white font-bold text-xs mr-3">宏观分析</h2>
        {(["indicators", "briefing", "allocation"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setActiveSection(s)}
            className={`px-2 py-0.5 text-xs rounded ${
              activeSection === s ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-gray-800"
            }`}
          >
            {{ indicators: "指标", briefing: "快报", allocation: "配置" }[s]}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={handleFetchData}
          disabled={fetchingData}
          className="px-2 py-0.5 text-xs bg-green-700 text-white rounded hover:bg-green-600 disabled:opacity-50"
        >
          {fetchingData ? "采集中..." : "采集数据"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {/* 指标面板 */}
        {activeSection === "indicators" && (
          <div className="space-y-3">
            {indicators.length === 0 ? (
              <p className="text-gray-500 text-xs">暂无数据，请先点击"采集数据"</p>
            ) : (
              Object.entries(grouped).map(([cat, items]) => (
                <div key={cat}>
                  <h3 className={`text-xs font-bold mb-1 ${CATEGORY_COLORS[cat] || "text-gray-300"}`}>
                    {CATEGORY_NAMES[cat] || cat}
                  </h3>
                  <div className="grid grid-cols-2 gap-1">
                    {items.map((ind) => (
                      <button
                        key={ind.code}
                        onClick={() => handleShowHistory(ind.code)}
                        className="flex justify-between items-center px-2 py-1 bg-gray-800 rounded hover:bg-gray-750 text-left"
                      >
                        <span className="text-gray-300 text-xs truncate">{ind.name}</span>
                        <span className="text-white text-xs font-mono ml-1">
                          {ind.value}{ind.unit}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ))
            )}

            {/* 历史弹窗 */}
            {selectedHistory && (
              <div className="mt-2 p-2 bg-gray-800 rounded">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-xs text-white font-bold">{selectedHistory.code} 历史</span>
                  <button onClick={() => setSelectedHistory(null)} className="text-gray-500 text-xs">关闭</button>
                </div>
                <div className="space-y-0.5 max-h-40 overflow-y-auto">
                  {selectedHistory.data.map((d, i) => (
                    <div key={i} className="flex justify-between text-xs">
                      <span className="text-gray-400">{d.date}</span>
                      <span className="text-white font-mono">{d.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* 快报面板 */}
        {activeSection === "briefing" && (
          <div className="space-y-3">
            <button
              onClick={handleGenerateBriefing}
              disabled={loading}
              className="w-full px-3 py-1.5 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "生成中..." : "生成宏观快报"}
            </button>
            {briefing && (
              <div className="bg-gray-800 rounded p-3">
                <div className="flex justify-between mb-2">
                  <span className="text-xs text-gray-400">
                    {briefing.generated_at} | {briefing.model}
                  </span>
                  <span className="text-xs text-gray-500">
                    基于 {briefing.indicator_count} 个指标
                  </span>
                </div>
                <div className="text-xs text-gray-200 whitespace-pre-wrap leading-relaxed">
                  {briefing.content}
                </div>
              </div>
            )}
            {!briefing && !loading && (
              <p className="text-gray-500 text-xs">暂无快报，点击上方按钮生成</p>
            )}
          </div>
        )}

        {/* 资产配置面板 */}
        {activeSection === "allocation" && (
          <div className="space-y-2">
            {allocation.length === 0 ? (
              <p className="text-gray-500 text-xs">暂无资产配置数据</p>
            ) : (
              allocation.map((a) => (
                <div key={a.asset_class} className="bg-gray-800 rounded p-2">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs text-white font-bold">{a.asset_class}</span>
                    <span className="text-xs text-gray-400">
                      {a.value > 0 ? `¥${(a.value / 10000).toFixed(1)}万` : ""}
                    </span>
                  </div>
                  <div className="flex gap-2 text-xs">
                    <div className="flex-1">
                      <div className="flex justify-between text-gray-400 mb-0.5">
                        <span>目标</span>
                        <span>{a.target_pct}%</span>
                      </div>
                      <div className="w-full bg-gray-700 rounded-full h-1.5">
                        <div
                          className="bg-blue-500 h-1.5 rounded-full"
                          style={{ width: `${Math.min(a.target_pct, 100)}%` }}
                        />
                      </div>
                    </div>
                    <div className="flex-1">
                      <div className="flex justify-between text-gray-400 mb-0.5">
                        <span>实际</span>
                        <span>{a.actual_pct}%</span>
                      </div>
                      <div className="w-full bg-gray-700 rounded-full h-1.5">
                        <div
                          className={`h-1.5 rounded-full ${
                            Math.abs(a.actual_pct - a.target_pct) > 5 ? "bg-red-500" : "bg-green-500"
                          }`}
                          style={{ width: `${Math.min(a.actual_pct, 100)}%` }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
