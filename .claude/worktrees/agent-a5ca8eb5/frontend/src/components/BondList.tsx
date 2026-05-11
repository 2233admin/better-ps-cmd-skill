"use client";

import { useEffect, useState, useCallback } from "react";
import { getBondQuotes, getOkxTickers, getOkxAccount, Quote, OkxTickerData } from "@/lib/api";
import { useStore } from "@/store";
import { useWebSocket } from "@/hooks/useWebSocket";

interface BondListProps {
  market?: "astock" | "crypto";
  onSelectPair?: (pair: string) => void;
}

export default function BondList({ market = "astock", onSelectPair }: BondListProps) {
  // A股状态
  const [bonds, setBonds] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const { setSelectedCode, updateQuotes } = useStore();
  const { lastMessage, connected } = useWebSocket("quotes");
  const [sortBy, setSortBy] = useState<"change" | "volume" | "amount">("amount");
  const [search, setSearch] = useState("");

  // Crypto状态
  const [tickers, setTickers] = useState<OkxTickerData[]>([]);
  const [heldPairs, setHeldPairs] = useState<Set<string>>(new Set());
  const [selectedTicker, setSelectedTicker] = useState("");

  const isCrypto = market === "crypto";

  // A股初始加载
  useEffect(() => {
    if (isCrypto) return;
    setLoading(true);
    getBondQuotes()
      .then((res) => { setBonds(res.data); updateQuotes(res.data); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [isCrypto, updateQuotes]);

  // A股 WebSocket 更新
  useEffect(() => {
    if (isCrypto) return;
    if (lastMessage && typeof lastMessage === "object") {
      const msg = lastMessage as { type?: string; data?: Quote[] };
      if (msg.type === "quotes" && msg.data) {
        setBonds(msg.data);
        updateQuotes(msg.data);
      }
    }
  }, [lastMessage, updateQuotes, isCrypto]);

  // Crypto 轮询 tickers (3秒)
  const fetchTickers = useCallback(async () => {
    try {
      const res = await getOkxTickers();
      setTickers(res.tickers);
    } catch { /* ignore */ }
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      const res = await getOkxAccount();
      const pairs = new Set(res.positions.map((p) => p.instId.replace("-SWAP", "")));
      setHeldPairs(pairs);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!isCrypto) return;
    fetchTickers();
    fetchPositions();
    const tickerInterval = setInterval(fetchTickers, 3000);
    const posInterval = setInterval(fetchPositions, 30000);
    return () => { clearInterval(tickerInterval); clearInterval(posInterval); };
  }, [isCrypto, fetchTickers, fetchPositions]);

  const handleSelectTicker = (pair: string) => {
    setSelectedTicker(pair);
    onSelectPair?.(pair);
  };

  // A股过滤排序
  const filtered = bonds
    .filter((b) => {
      if (!search) return true;
      const code = String(b.code || "");
      const name = String(b.name || "");
      return code.includes(search) || name.includes(search);
    })
    .sort((a, b) => {
      if (sortBy === "amount") return (b.amount || 0) - (a.amount || 0);
      if (sortBy === "volume") return (b.vol || 0) - (a.vol || 0);
      const changeA = a.open ? ((a.price - a.open) / a.open) : 0;
      const changeB = b.open ? ((b.price - b.open) / b.open) : 0;
      return changeB - changeA;
    });

  // Crypto 排序：持仓置顶，然后按涨跌幅绝对值
  const sortedTickers = [...tickers].sort((a, b) => {
    const aHeld = heldPairs.has(a.pair || a.instId.replace("-SWAP", "")) ? 1 : 0;
    const bHeld = heldPairs.has(b.pair || b.instId.replace("-SWAP", "")) ? 1 : 0;
    if (aHeld !== bHeld) return bHeld - aHeld;
    return Math.abs(b.change || 0) - Math.abs(a.change || 0);
  });

  // ===== Crypto 渲染 =====
  if (isCrypto) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-700 flex flex-col h-full">
        <div className="px-3 py-2 border-b border-gray-700">
          <h3 className="text-sm font-bold text-white">
            永续合约
            <span className="text-xs text-yellow-400 ml-2">LIVE</span>
          </h3>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-900">
              <tr className="text-gray-400">
                <th className="px-2 py-1 text-left">币种</th>
                <th className="px-2 py-1 text-right">价格</th>
                <th className="px-2 py-1 text-right">涨跌%</th>
                <th className="px-2 py-1 text-right">24h额</th>
              </tr>
            </thead>
            <tbody>
              {sortedTickers.map((t) => {
                const pairName = t.pair || t.instId.replace("-SWAP", "");
                const isHeld = heldPairs.has(pairName);
                const isSelected = selectedTicker === pairName;
                const change = t.change || 0;
                return (
                  <tr
                    key={t.instId}
                    onClick={() => handleSelectTicker(pairName)}
                    className={`cursor-pointer border-b border-gray-800/50 ${
                      isSelected ? "bg-gray-800" : "hover:bg-gray-800/50"
                    } ${isHeld ? "border-l-2 border-l-yellow-500" : ""}`}
                  >
                    <td className="px-2 py-1.5">
                      <div className="text-white font-medium">
                        {pairName.split("-")[0]}
                        {isHeld && <span className="text-yellow-400 ml-1 text-[10px]">持仓</span>}
                      </div>
                      <div className="text-gray-500 text-[10px]">{pairName}</div>
                    </td>
                    <td className="px-2 py-1.5 text-right text-white font-mono">
                      {t.last.toLocaleString()}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono ${
                      change > 0 ? "text-green-400" : change < 0 ? "text-red-400" : "text-gray-400"
                    }`}>
                      {change > 0 ? "+" : ""}{change.toFixed(2)}%
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-300">
                      {t.vol24h > 1e8
                        ? (t.vol24h / 1e8).toFixed(1) + "亿"
                        : t.vol24h > 1e4
                        ? (t.vol24h / 1e4).toFixed(0) + "万"
                        : t.vol24h.toFixed(0)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // ===== A股渲染 =====
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 flex flex-col h-full">
      <div className="px-3 py-2 border-b border-gray-700">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-bold text-white">
            可转债行情
            <span className="text-xs text-gray-400 ml-2">
              {connected ? "LIVE" : "OFFLINE"}
            </span>
          </h3>
          <span className="text-xs text-gray-500">{filtered.length}只</span>
        </div>
        <input
          type="text"
          placeholder="搜索代码/名称..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
        />
        <div className="flex gap-1 mt-1">
          {(["amount", "volume", "change"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSortBy(s)}
              className={`text-xs px-2 py-0.5 rounded ${
                sortBy === s ? "bg-blue-600 text-white" : "text-gray-400"
              }`}
            >
              {s === "amount" ? "金额" : s === "volume" ? "成交量" : "涨跌"}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <p className="text-xs text-gray-500 text-center py-4">加载中...</p>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-900">
              <tr className="text-gray-400">
                <th className="px-2 py-1 text-left">代码</th>
                <th className="px-2 py-1 text-right">现价</th>
                <th className="px-2 py-1 text-right">涨跌%</th>
                <th className="px-2 py-1 text-right">成交额</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 100).map((b) => {
                const change = b.open ? ((b.price - b.open) / b.open) * 100 : 0;
                return (
                  <tr
                    key={b.code}
                    onClick={() => setSelectedCode(b.code)}
                    className="hover:bg-gray-800 cursor-pointer border-b border-gray-800/50"
                  >
                    <td className="px-2 py-1">
                      <div className="text-white">{b.code}</div>
                      <div className="text-gray-500 text-[10px]">{b.name}</div>
                    </td>
                    <td className="px-2 py-1 text-right text-white">{b.price?.toFixed(3)}</td>
                    <td className={`px-2 py-1 text-right ${
                      change > 0 ? "text-red-400" : change < 0 ? "text-green-400" : "text-gray-400"
                    }`}>
                      {change > 0 ? "+" : ""}{change.toFixed(2)}%
                    </td>
                    <td className="px-2 py-1 text-right text-gray-300">
                      {b.amount
                        ? b.amount > 100000000
                          ? (b.amount / 100000000).toFixed(1) + "亿"
                          : (b.amount / 10000).toFixed(0) + "万"
                        : "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
