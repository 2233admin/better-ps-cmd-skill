"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getPositions, getTrades, getPnl,
  getOkxAccount, closeOkxPosition,
  Position, Trade, PnlSummary, OkxAccountData,
} from "@/lib/api";

interface PortfolioProps {
  market?: "astock" | "crypto";
}

export default function Portfolio({ market = "astock" }: PortfolioProps) {
  if (market === "crypto") return <CryptoPortfolio />;
  return <AstockPortfolio />;
}

// ===== Crypto 持仓 =====
function CryptoPortfolio() {
  const [account, setAccount] = useState<OkxAccountData | null>(null);
  const [closing, setClosing] = useState("");

  const fetchAccount = useCallback(async () => {
    try {
      const res = await getOkxAccount();
      setAccount(res);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchAccount();
    const interval = setInterval(fetchAccount, 30000);
    return () => clearInterval(interval);
  }, [fetchAccount]);

  const handleClose = async (instId: string, posSide: string, size: string) => {
    setClosing(instId + posSide);
    try {
      await closeOkxPosition(instId, posSide, size);
      await fetchAccount();
    } catch (e) {
      console.error("Close failed:", e);
    } finally {
      setClosing("");
    }
  };

  const acct = account?.account;
  const positions = account?.positions || [];

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 h-full flex flex-col">
      {/* 账户概览 */}
      {acct && (
        <div className="flex items-center gap-4 px-4 py-2 border-b border-gray-700">
          <span className="text-xs text-gray-400">
            权益: <span className="text-white font-mono">{acct.equity.toFixed(2)} USDT</span>
          </span>
          <span className="text-xs text-gray-400">
            可用: <span className="text-white font-mono">{acct.available.toFixed(2)}</span>
          </span>
          <span className="text-xs text-gray-400">
            浮盈:{" "}
            <span className={acct.upl >= 0 ? "text-green-400" : "text-red-400"}>
              {acct.upl >= 0 ? "+" : ""}{acct.upl.toFixed(2)}
            </span>
          </span>
          <span className="text-xs text-gray-400">
            持仓: <span className="text-yellow-400">{positions.length}</span>
          </span>
        </div>
      )}

      {/* 持仓表 */}
      <div className="flex-1 overflow-y-auto">
        {positions.length === 0 ? (
          <p className="text-xs text-gray-500 p-4 text-center">暂无持仓</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-400 border-b border-gray-800">
                <th className="px-3 py-1 text-left">品种</th>
                <th className="px-3 py-1 text-left">方向</th>
                <th className="px-3 py-1 text-right">数量</th>
                <th className="px-3 py-1 text-right">均价</th>
                <th className="px-3 py-1 text-right">现价</th>
                <th className="px-3 py-1 text-right">浮盈</th>
                <th className="px-3 py-1 text-right">杠杆</th>
                <th className="px-3 py-1 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const isLong = p.posSide === "long" || p.posSide === "net";
                return (
                  <tr key={p.instId + p.posSide} className="border-b border-gray-800 hover:bg-gray-800">
                    <td className="px-3 py-1 text-white">
                      {p.instId.replace("-SWAP", "").replace("-USDT", "")}
                    </td>
                    <td className={`px-3 py-1 ${isLong ? "text-green-400" : "text-red-400"}`}>
                      {isLong ? "多" : "空"}
                    </td>
                    <td className="px-3 py-1 text-right text-white">{p.pos}</td>
                    <td className="px-3 py-1 text-right text-gray-300">{p.avgPx}</td>
                    <td className="px-3 py-1 text-right text-gray-300">{p.markPx}</td>
                    <td className={`px-3 py-1 text-right ${p.upl >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {p.upl >= 0 ? "+" : ""}{p.upl.toFixed(2)}
                    </td>
                    <td className="px-3 py-1 text-right text-yellow-400">{p.lever}x</td>
                    <td className="px-3 py-1 text-right">
                      <button
                        onClick={() => handleClose(p.instId, p.posSide, p.pos)}
                        disabled={closing === p.instId + p.posSide}
                        className="px-2 py-0.5 text-[10px] bg-gray-700 text-white rounded hover:bg-red-600 disabled:opacity-50"
                      >
                        {closing === p.instId + p.posSide ? "..." : "平仓"}
                      </button>
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

// ===== A股持仓 (原逻辑) =====
function AstockPortfolio() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [tab, setTab] = useState<"positions" | "trades">("positions");

  useEffect(() => {
    const load = async () => {
      try {
        const [posRes, tradeRes, pnlRes] = await Promise.all([
          getPositions(), getTrades(20), getPnl(),
        ]);
        setPositions(posRes.data);
        setTrades(tradeRes.data);
        setPnl(pnlRes);
      } catch { /* ignore */ }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 h-full flex flex-col">
      {pnl && (
        <div className="flex items-center gap-4 px-4 py-2 border-b border-gray-700">
          <span className="text-xs text-gray-400">
            总盈亏:{" "}
            <span className={pnl.total_pnl >= 0 ? "text-red-400" : "text-green-400"}>
              {pnl.total_pnl >= 0 ? "+" : ""}{pnl.total_pnl.toFixed(2)}
            </span>
          </span>
          <span className="text-xs text-gray-400">持仓: {pnl.position_count}</span>
          <span className="text-xs text-gray-400">今日成交: {pnl.today_trades}</span>
        </div>
      )}

      <div className="flex border-b border-gray-700">
        <button
          onClick={() => setTab("positions")}
          className={`px-4 py-2 text-xs ${
            tab === "positions" ? "text-blue-400 border-b-2 border-blue-400" : "text-gray-400"
          }`}
        >
          持仓 ({positions.length})
        </button>
        <button
          onClick={() => setTab("trades")}
          className={`px-4 py-2 text-xs ${
            tab === "trades" ? "text-blue-400 border-b-2 border-blue-400" : "text-gray-400"
          }`}
        >
          成交 ({trades.length})
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {tab === "positions" ? (
          positions.length === 0 ? (
            <p className="text-xs text-gray-500 p-4 text-center">暂无持仓</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="px-3 py-1 text-left">代码</th>
                  <th className="px-3 py-1 text-right">数量</th>
                  <th className="px-3 py-1 text-right">成本</th>
                  <th className="px-3 py-1 text-right">现价</th>
                  <th className="px-3 py-1 text-right">盈亏</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.code} className="border-b border-gray-800 hover:bg-gray-800">
                    <td className="px-3 py-1 text-white">{p.code}</td>
                    <td className="px-3 py-1 text-right text-white">{p.volume}</td>
                    <td className="px-3 py-1 text-right text-gray-300">{p.avg_price.toFixed(3)}</td>
                    <td className="px-3 py-1 text-right text-gray-300">{p.current_price.toFixed(3)}</td>
                    <td className={`px-3 py-1 text-right ${p.pnl >= 0 ? "text-red-400" : "text-green-400"}`}>
                      {p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(2)} ({(p.pnl_pct * 100).toFixed(2)}%)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : trades.length === 0 ? (
          <p className="text-xs text-gray-500 p-4 text-center">暂无成交</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-400 border-b border-gray-800">
                <th className="px-3 py-1 text-left">代码</th>
                <th className="px-3 py-1 text-left">方向</th>
                <th className="px-3 py-1 text-right">价格</th>
                <th className="px-3 py-1 text-right">数量</th>
                <th className="px-3 py-1 text-left">策略</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} className="border-b border-gray-800 hover:bg-gray-800">
                  <td className="px-3 py-1 text-white">{t.code}</td>
                  <td className={`px-3 py-1 ${t.direction === "buy" ? "text-red-400" : "text-green-400"}`}>
                    {t.direction === "buy" ? "买" : "卖"}
                  </td>
                  <td className="px-3 py-1 text-right text-gray-300">{t.price.toFixed(3)}</td>
                  <td className="px-3 py-1 text-right text-white">{t.volume}</td>
                  <td className="px-3 py-1 text-gray-400">{t.strategy}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
