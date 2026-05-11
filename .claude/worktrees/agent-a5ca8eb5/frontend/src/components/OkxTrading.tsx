"use client";

import { useState, useEffect, useCallback } from "react";
import {
  getOkxAccount,
  scanOkxMarket,
  getOkxTickers,
  quickTrade,
  closeOkxPosition,
  placeOkxOrder,
  setOkxSlTp,
  type OkxAccountData,
  type OkxScanData,
  type OkxTickerData,
} from "@/lib/api";

function cn(...c: (string | false | undefined | null)[]) {
  return c.filter(Boolean).join(" ");
}

function fmtPx(p: number) {
  if (p < 0.001) return p.toFixed(8);
  if (p < 1) return p.toFixed(5);
  if (p < 100) return p.toFixed(2);
  return p.toFixed(1);
}

const PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "SUI-USDT", "PEPE-USDT", "AVAX-USDT", "LINK-USDT"];

// ============================================================
// 顶部: 币种切换 + 实时价格
// ============================================================
function PriceBar({ pair, ticker, onChangePair }: {
  pair: string; ticker: OkxTickerData | null; onChangePair: (p: string) => void;
}) {
  const chg = ticker?.change ?? 0;
  const cc = chg >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]";
  return (
    <div className="flex items-center border-b border-[#2b2b2b] bg-[#161a1e]">
      <div className="flex items-center border-r border-[#2b2b2b] overflow-x-auto">
        {PAIRS.map(p => (
          <button key={p} onClick={() => onChangePair(p)}
            className={cn("px-2.5 py-2 text-[11px] font-medium whitespace-nowrap border-b-2 transition-colors",
              p === pair ? "text-[#f0b90b] border-b-[#f0b90b] bg-[#1e2329]" : "text-[#848e9c] border-b-transparent hover:text-[#d1d4dc]"
            )}>{p.replace("-USDT", "")}</button>
        ))}
      </div>
      {ticker && (
        <div className="flex items-center gap-5 px-4 flex-shrink-0">
          <div className={cn("text-xl font-semibold font-mono", cc)}>{fmtPx(ticker.last)}</div>
          <div className="text-[11px]">
            <span className="text-[#474d57]">24h </span>
            <span className={cn("font-mono", cc)}>{chg >= 0 ? "+" : ""}{chg}%</span>
          </div>
          <div className="text-[11px] text-[#848e9c] font-mono">
            H {fmtPx(ticker.high24h)} / L {fmtPx(ticker.low24h)}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// 盯盘模式: 持仓监控 + 实时行情
// ============================================================
function WatchPanel({ tickers, account, pair, onSelect, scanData }: {
  tickers: OkxTickerData[]; account: OkxAccountData | null;
  pair: string; onSelect: (p: string) => void;
  scanData: OkxScanData | null;
}) {
  const positions = account?.positions || [];
  const posInstIds = new Set(positions.map(p => p.instId.replace("-SWAP", "")));

  // 信号映射
  const sigMap = new Map<string, { signal: string; confidence: number }>();
  if (scanData) {
    for (const m of scanData.market) {
      if (m.signal !== "WAIT") sigMap.set(m.pair, { signal: m.signal, confidence: m.confidence });
    }
  }

  // 把持仓品种排在最前面
  const sorted = [...tickers].sort((a, b) => {
    const ap = a.pair || a.instId.replace("-SWAP", "");
    const bp = b.pair || b.instId.replace("-SWAP", "");
    const aHas = posInstIds.has(ap) ? 1 : 0;
    const bHas = posInstIds.has(bp) ? 1 : 0;
    return bHas - aHas;
  });

  return (
    <div className="h-full flex flex-col">
      {/* 持仓概览 */}
      {positions.length > 0 && (
        <div className="border-b border-[#2b2b2b] bg-[#1e2329]">
          <div className="px-3 py-1 text-[10px] text-[#474d57] font-medium">WATCHING {positions.length} POSITION{positions.length > 1 ? "S" : ""}</div>
          {positions.map(p => {
            const isL = p.posSide === "long";
            const sym = p.instId.replace("-USDT-SWAP", "");
            const pairKey = p.instId.replace("-SWAP", "");
            const tk = tickers.find(t => (t.pair || t.instId.replace("-SWAP", "")) === pairKey);
            const livePrice = tk ? tk.last : parseFloat(p.markPx);
            const entry = parseFloat(p.avgPx);
            const pnlPct = entry > 0 ? ((livePrice - entry) / entry * 100 * (isL ? 1 : -1)) : 0;
            return (
              <div key={p.instId + p.posSide}
                className={cn("flex items-center justify-between px-3 py-1.5 cursor-pointer hover:bg-[#2b3139]",
                  pairKey === pair && "bg-[#2b3139]"
                )}
                onClick={() => onSelect(pairKey)}
              >
                <div className="flex items-center gap-2">
                  <span className={cn("text-[10px] font-bold w-5",
                    isL ? "text-[#0ecb81]" : "text-[#f6465d]"
                  )}>{isL ? "L" : "S"}</span>
                  <span className="text-[12px] text-[#d1d4dc] font-medium">{sym}</span>
                  <span className="text-[10px] text-[#474d57]">x{p.pos} {p.lever}x</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[11px] font-mono text-[#848e9c]">{fmtPx(livePrice)}</span>
                  <span className={cn("text-[11px] font-mono font-medium min-w-[60px] text-right",
                    p.upl >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]"
                  )}>{p.upl >= 0 ? "+" : ""}{p.upl.toFixed(2)}U</span>
                  <span className={cn("text-[10px] font-mono",
                    pnlPct >= 0 ? "text-[#0ecb81]/60" : "text-[#f6465d]/60"
                  )}>({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%)</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 行情表 */}
      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-[10px] text-[#474d57] border-b border-[#2b2b2b] sticky top-0 bg-[#181a20]">
              <th className="text-left py-1 px-3 font-normal">Pair</th>
              <th className="text-right px-2 font-normal">Price</th>
              <th className="text-right px-2 font-normal">Chg%</th>
              <th className="text-right px-2 font-normal">Bid</th>
              <th className="text-right px-2 font-normal">Ask</th>
              <th className="text-center px-2 font-normal">Signal</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(t => {
              const p = t.pair || t.instId.replace("-SWAP", "");
              const sym = p.replace("-USDT", "");
              const chg = t.change ?? 0;
              const active = p === pair;
              const hasPos = posInstIds.has(p);
              const sig = sigMap.get(p);
              return (
                <tr key={t.instId}
                  className={cn(
                    "border-b border-[#2b2b2b]/40 cursor-pointer transition-colors",
                    active && "bg-[#2b3139]",
                    hasPos && !active && "bg-[#1e2329]",
                    !active && "hover:bg-[#1e2329]"
                  )}
                  onClick={() => onSelect(p)}
                >
                  <td className="py-1.5 px-3">
                    <span className={cn("font-medium", hasPos ? "text-[#f0b90b]" : "text-[#d1d4dc]")}>{sym}</span>
                    {hasPos && <span className="text-[9px] text-[#f0b90b]/40 ml-1">POS</span>}
                  </td>
                  <td className={cn("text-right px-2 font-mono", chg >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]")}>{fmtPx(t.last)}</td>
                  <td className={cn("text-right px-2 font-mono font-medium", chg >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]")}>
                    {chg >= 0 ? "+" : ""}{chg}%
                  </td>
                  <td className="text-right px-2 font-mono text-[#0ecb81]/40">{fmtPx(t.bidPx)}</td>
                  <td className="text-right px-2 font-mono text-[#f6465d]/40">{fmtPx(t.askPx)}</td>
                  <td className="text-center px-2">
                    {sig ? (
                      <span className={cn("text-[9px] font-medium px-1 py-px rounded-sm",
                        sig.signal === "LONG" ? "bg-[#0ecb81]/12 text-[#0ecb81]" : "bg-[#f6465d]/12 text-[#f6465d]"
                      )}>{sig.signal}</span>
                    ) : <span className="text-[#2b2b2b]">--</span>}
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

// ============================================================
// 下单面板
// ============================================================
function OrderForm({ pair, ticker, onDone }: {
  pair: string; ticker: OkxTickerData | null; onDone: () => void;
}) {
  const [side, setSide] = useState<"long" | "short">("long");
  const [ordType, setOrdType] = useState<"limit" | "market">("market");
  const [price, setPrice] = useState("");
  const [size, setSize] = useState("1");
  const [lever, setLever] = useState("20");
  const [slPrice, setSlPrice] = useState("");
  const [tpPrice, setTpPrice] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const isLong = side === "long";
  const sym = pair.replace("-USDT", "");

  const submit = async () => {
    setLoading(true); setMsg("");
    try {
      const r = await placeOkxOrder({
        pair, side: isLong ? "buy" : "sell", posSide: side,
        size, ordType, lever: parseInt(lever),
        ...(ordType === "limit" && price ? { price } : {}),
      });
      let m = `Order ${r.orderId}`;
      if (slPrice || tpPrice) {
        await setOkxSlTp({
          instId: pair + "-SWAP", posSide: side, size,
          ...(slPrice ? { slPrice } : {}), ...(tpPrice ? { tpPrice } : {}),
        });
        m += " + SL/TP";
      }
      setMsg(m); onDone();
    } catch (e) { setMsg(`Error: ${e}`); }
    finally { setLoading(false); }
  };

  return (
    <div className="flex-1 flex flex-col p-3 space-y-2.5 overflow-y-auto">
      {/* 价格显示 */}
      {ticker && (
        <div className="text-center">
          <div className="text-lg font-mono font-bold text-white">{fmtPx(ticker.last)}</div>
          <div className="text-[9px] text-[#474d57] font-mono">
            B {fmtPx(ticker.bidPx)} / A {fmtPx(ticker.askPx)}
          </div>
        </div>
      )}

      {/* 买卖 */}
      <div className="flex bg-[#2b3139] rounded-sm overflow-hidden">
        <button onClick={() => setSide("long")}
          className={cn("flex-1 py-2 text-[12px] font-semibold",
            isLong ? "bg-[#0ecb81] text-white" : "text-[#848e9c]"
          )}>Buy/Long</button>
        <button onClick={() => setSide("short")}
          className={cn("flex-1 py-2 text-[12px] font-semibold",
            !isLong ? "bg-[#f6465d] text-white" : "text-[#848e9c]"
          )}>Sell/Short</button>
      </div>

      {/* 类型 */}
      <div className="flex gap-4">
        {(["limit", "market"] as const).map(t => (
          <button key={t} onClick={() => setOrdType(t)}
            className={cn("text-[11px] pb-0.5 border-b-2 capitalize",
              ordType === t ? "text-[#d1d4dc] border-b-[#f0b90b]" : "text-[#474d57] border-b-transparent"
            )}>{t}</button>
        ))}
      </div>

      {/* 输入 */}
      {ordType === "limit" && (
        <div>
          <label className="text-[10px] text-[#474d57] mb-0.5 block">Price</label>
          <div className="flex items-center bg-[#2b3139] rounded-sm border border-[#2b2b2b] focus-within:border-[#f0b90b]">
            <input value={price} onChange={e => setPrice(e.target.value)}
              placeholder={ticker ? fmtPx(ticker.last) : ""}
              className="flex-1 bg-transparent px-2 py-1.5 text-[12px] font-mono text-white outline-none placeholder-[#474d57]" />
            <span className="pr-2 text-[10px] text-[#474d57]">USDT</span>
          </div>
        </div>
      )}

      <div>
        <label className="text-[10px] text-[#474d57] mb-0.5 block">Amount</label>
        <div className="flex items-center bg-[#2b3139] rounded-sm border border-[#2b2b2b] focus-within:border-[#f0b90b]">
          <input value={size} onChange={e => setSize(e.target.value)}
            className="flex-1 bg-transparent px-2 py-1.5 text-[12px] font-mono text-white outline-none" />
          <span className="pr-2 text-[10px] text-[#474d57]">Cont</span>
        </div>
      </div>

      {/* 杠杆 */}
      <div>
        <div className="flex justify-between mb-0.5">
          <label className="text-[10px] text-[#474d57]">Leverage</label>
          <span className="text-[11px] text-[#f0b90b] font-mono">{lever}x</span>
        </div>
        <input type="range" min="1" max="50" value={lever} onChange={e => setLever(e.target.value)}
          className="w-full h-1 bg-[#2b3139] rounded-lg appearance-none cursor-pointer accent-[#f0b90b]" />
      </div>

      {/* TP/SL */}
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[9px] text-[#0ecb81]/50 mb-0.5 block">Take Profit</label>
          <input value={tpPrice} onChange={e => setTpPrice(e.target.value)} placeholder="--"
            className="w-full bg-[#2b3139] border border-[#2b2b2b] px-2 py-1 text-[11px] font-mono text-white outline-none focus:border-[#0ecb81]/30 placeholder-[#333] rounded-sm" />
        </div>
        <div className="flex-1">
          <label className="text-[9px] text-[#f6465d]/50 mb-0.5 block">Stop Loss</label>
          <input value={slPrice} onChange={e => setSlPrice(e.target.value)} placeholder="--"
            className="w-full bg-[#2b3139] border border-[#2b2b2b] px-2 py-1 text-[11px] font-mono text-white outline-none focus:border-[#f6465d]/30 placeholder-[#333] rounded-sm" />
        </div>
      </div>

      {/* 预估 */}
      {ticker && (
        <div className="text-[9px] text-[#474d57] flex justify-between">
          <span>Est. Margin</span>
          <span className="text-[#848e9c] font-mono">
            ~{(parseFloat(size || "0") * ticker.last / parseFloat(lever || "20")).toFixed(2)} U
          </span>
        </div>
      )}

      {/* 下单 */}
      <button onClick={submit} disabled={loading}
        className={cn("w-full py-2 rounded-sm text-[12px] font-semibold",
          loading && "opacity-50",
          isLong ? "bg-[#0ecb81] text-white hover:bg-[#0ecb81]/90" : "bg-[#f6465d] text-white hover:bg-[#f6465d]/90"
        )}>{loading ? "..." : `${isLong ? "Buy/Long" : "Sell/Short"} ${sym}`}</button>

      {msg && <div className={cn("text-[9px] font-mono",
        msg.startsWith("Error") ? "text-[#f6465d]" : "text-[#0ecb81]/70"
      )}>{msg}</div>}
    </div>
  );
}

// ============================================================
// 底部: 持仓管理 + 分析结果
// ============================================================
function BottomBar({ account, onClose, scanData, onTrade, scanning, onScan }: {
  account: OkxAccountData | null;
  onClose: (instId: string, posSide: string, size: string) => void;
  scanData: OkxScanData | null;
  onTrade: (i: number) => void;
  scanning: boolean;
  onScan: () => void;
}) {
  const [tab, setTab] = useState<"positions" | "plans" | "analysis">("positions");
  const positions = account?.positions || [];
  const algos = account?.algos || [];

  return (
    <div className="h-full flex flex-col bg-[#1e2329]">
      <div className="flex items-center border-b border-[#2b2b2b] px-3">
        {([
          { id: "positions" as const, label: `Positions(${positions.length})` },
          { id: "plans" as const, label: `Plans(${scanData?.plans.length || 0})` },
          { id: "analysis" as const, label: "Analysis" },
        ]).map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={cn("px-3 py-1.5 text-[11px] border-b-2",
              tab === t.id ? "text-[#d1d4dc] border-b-[#f0b90b]" : "text-[#474d57] border-b-transparent hover:text-[#848e9c]"
            )}>{t.label}</button>
        ))}
        <div className="ml-auto flex items-center gap-3">
          {account && (
            <span className="text-[10px] text-[#474d57]">
              Equity <span className="text-[#d1d4dc] font-mono">{account.account.equity.toFixed(2)}</span>
              <span className="mx-2">|</span>
              UPL <span className={cn("font-mono", account.account.upl >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]")}>
                {account.account.upl >= 0 ? "+" : ""}{account.account.upl.toFixed(2)}
              </span>
            </span>
          )}
          {scanData && (
            <span className="text-[9px] text-[#474d57]">
              Scanned {new Date(((scanData as unknown as Record<string, unknown>)._ts as number) * 1000 || Date.now()).toLocaleTimeString("en-GB")}
            </span>
          )}
          <button onClick={onScan} disabled={scanning}
            className={cn("px-2 py-0.5 text-[10px] font-medium rounded-sm border transition-colors",
              scanning ? "border-[#2b2b2b] text-[#474d57]" : "border-[#f0b90b]/30 text-[#f0b90b] hover:bg-[#f0b90b]/10"
            )}>{scanning ? "Scanning..." : "Run Analysis"}</button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {tab === "positions" && (
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-[10px] text-[#474d57] border-b border-[#2b2b2b] sticky top-0 bg-[#1e2329]">
                <th className="text-left py-1 px-3 font-normal">Symbol</th>
                <th className="text-left px-2 font-normal">Side</th>
                <th className="text-right px-2 font-normal">Size</th>
                <th className="text-right px-2 font-normal">Entry</th>
                <th className="text-right px-2 font-normal">Mark</th>
                <th className="text-right px-2 font-normal">PNL</th>
                <th className="text-right px-2 font-normal">Lever</th>
                <th className="text-center px-2 font-normal">SL/TP</th>
                <th className="text-center px-2 font-normal">Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map(p => {
                const isL = p.posSide === "long";
                const algo = algos.find(a => a.instId === p.instId);
                return (
                  <tr key={p.instId + p.posSide} className="border-b border-[#2b2b2b]/40 hover:bg-[#2b3139]/40">
                    <td className="py-1.5 px-3 text-[#d1d4dc] font-medium">{p.instId.replace("-USDT-SWAP","")}</td>
                    <td className={cn("px-2", isL ? "text-[#0ecb81]" : "text-[#f6465d]")}>{isL ? "Long" : "Short"}</td>
                    <td className="text-right px-2 font-mono text-[#d1d4dc]">{p.pos}</td>
                    <td className="text-right px-2 font-mono text-[#848e9c]">{parseFloat(p.avgPx).toFixed(2)}</td>
                    <td className="text-right px-2 font-mono text-[#d1d4dc]">{parseFloat(p.markPx).toFixed(2)}</td>
                    <td className={cn("text-right px-2 font-mono font-medium",
                      p.upl >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]"
                    )}>{p.upl >= 0 ? "+" : ""}{p.upl.toFixed(2)}</td>
                    <td className="text-right px-2 text-[#848e9c]">{p.lever}x</td>
                    <td className="text-center px-2 text-[10px] text-[#474d57]">
                      {algo ? (<>
                        {algo.slTriggerPx && <span className="text-[#f6465d]/50">SL {algo.slTriggerPx}</span>}
                        {algo.slTriggerPx && algo.tpTriggerPx && " / "}
                        {algo.tpTriggerPx && <span className="text-[#0ecb81]/50">TP {algo.tpTriggerPx}</span>}
                      </>) : "--"}
                    </td>
                    <td className="text-center px-2">
                      <button onClick={() => onClose(p.instId, p.posSide, p.pos)}
                        className="text-[10px] text-[#f6465d] px-2 py-px border border-[#f6465d]/20 rounded-sm hover:bg-[#f6465d]/10">
                        Close
                      </button>
                    </td>
                  </tr>
                );
              })}
              {positions.length === 0 && (
                <tr><td colSpan={9} className="text-center py-4 text-[#474d57] text-[12px]">No open positions</td></tr>
              )}
            </tbody>
          </table>
        )}

        {tab === "plans" && (
          <div className="p-2 space-y-1.5">
            {scanData?.plans.map((plan, i) => {
              const isL = plan.signal === "LONG";
              return (
                <div key={i} className="flex items-center justify-between p-2 bg-[#2b3139] rounded-sm">
                  <div className="flex items-center gap-2">
                    <span className={cn("text-[10px] font-semibold px-1.5 py-px rounded-sm",
                      isL ? "bg-[#0ecb81]/15 text-[#0ecb81]" : "bg-[#f6465d]/15 text-[#f6465d]"
                    )}>{plan.signal}</span>
                    <span className="text-[11px] text-[#d1d4dc] font-medium">{plan.pair}</span>
                    <span className="text-[10px] text-[#474d57] font-mono">@ {plan.price}</span>
                    <span className="text-[9px] text-[#474d57] max-w-[300px] truncate">{plan.reason}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] text-[#474d57] font-mono">
                      SL {plan.levels.stop_loss} / TP {plan.levels.take_profit} / MGN {plan.sizing.margin}U
                    </span>
                    <button onClick={() => onTrade(i)}
                      className={cn("text-[10px] font-medium px-2.5 py-0.5 rounded-sm",
                        isL ? "bg-[#0ecb81] text-white" : "bg-[#f6465d] text-white"
                      )}>Execute</button>
                  </div>
                </div>
              );
            })}
            {(!scanData?.plans || scanData.plans.length === 0) && (
              <div className="text-center py-4 text-[#474d57] text-[12px]">No plans. Click &quot;Run Analysis&quot; to scan.</div>
            )}
          </div>
        )}

        {tab === "analysis" && scanData && (
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="text-[9px] text-[#474d57] border-b border-[#2b2b2b] sticky top-0 bg-[#1e2329]">
                <th className="text-left py-1 px-2 font-normal">Pair</th>
                <th className="text-right px-1 font-normal">Chg%</th>
                <th className="text-right px-1 font-normal">VWAP</th>
                <th className="text-right px-1 font-normal">Fund</th>
                <th className="text-right px-1 font-normal">Taker</th>
                <th className="text-right px-1 font-normal">L/S</th>
                <th className="text-center px-1 font-normal">4H</th>
                <th className="text-center px-1 font-normal">Vol</th>
                <th className="text-right px-1 font-normal">Pos%</th>
                <th className="text-center px-1 font-normal">Signal</th>
                <th className="text-left px-1 font-normal">Contradictions</th>
              </tr>
            </thead>
            <tbody>
              {scanData.market.map(m => (
                <tr key={m.pair} className="border-b border-[#2b2b2b]/30">
                  <td className="py-1 px-2 text-[#d1d4dc] font-bold">{m.pair.replace("-USDT","")}</td>
                  <td className={cn("text-right px-1", m.change >= 0 ? "text-[#0ecb81]" : "text-[#f6465d]")}>
                    {m.change >= 0 ? "+" : ""}{m.change}%
                  </td>
                  <td className={cn("text-right px-1", m.vwapDev > 0 ? "text-[#0ecb81]/50" : "text-[#f6465d]/50")}>
                    {m.vwapDev > 0 ? "+" : ""}{m.vwapDev}%
                  </td>
                  <td className={cn("text-right px-1",
                    m.fundingRate < 0 ? "text-[#0ecb81]/50" : m.fundingRate > 0.02 ? "text-[#f6465d]/50" : "text-[#474d57]"
                  )}>{m.fundingRate}%</td>
                  <td className={cn("text-right px-1",
                    m.takerRatio > 1.3 ? "text-[#0ecb81]" : m.takerRatio < 0.7 ? "text-[#f6465d]" : "text-[#848e9c]"
                  )}>{m.takerRatio.toFixed(2)}</td>
                  <td className="text-right px-1 text-[#848e9c]">{m.lsRatio.toFixed(2)}</td>
                  <td className={cn("text-center px-1",
                    m.trend4h === "UPTREND" ? "text-[#0ecb81]" : m.trend4h === "DOWNTREND" ? "text-[#f6465d]" : "text-[#474d57]"
                  )}>{m.trend4h === "UPTREND" ? "UP" : m.trend4h === "DOWNTREND" ? "DN" : "RG"}</td>
                  <td className={cn("text-center px-1",
                    m.volTrend === "INCREASING" ? "text-[#f0b90b]" : "text-[#474d57]"
                  )}>{m.volTrend === "INCREASING" ? "INC" : m.volTrend === "DECREASING" ? "DEC" : "STB"}</td>
                  <td className="text-right px-1 text-[#848e9c]">{m.posInRange}%</td>
                  <td className="text-center px-1">
                    {m.signal !== "WAIT" ? (
                      <span className={cn("text-[9px] px-1 py-px rounded-sm",
                        m.signal === "LONG" ? "bg-[#0ecb81]/12 text-[#0ecb81]" : "bg-[#f6465d]/12 text-[#f6465d]"
                      )}>{m.signal}</span>
                    ) : <span className="text-[#333]">--</span>}
                  </td>
                  <td className="text-left px-1 text-[#f0b90b]/50 max-w-[200px] truncate">
                    {m.contradictions.length > 0 ? m.contradictions.join(", ") : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "analysis" && !scanData && (
          <div className="text-center py-4 text-[#474d57] text-[12px]">Click &quot;Run Analysis&quot; to scan market conditions.</div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 主组件
// ============================================================
export default function OkxTrading() {
  const [account, setAccount] = useState<OkxAccountData | null>(null);
  const [tickers, setTickers] = useState<OkxTickerData[]>([]);
  const [scan, setScan] = useState<OkxScanData | null>(null);
  const [scanning, setScanning] = useState(false);
  const [pair, setPair] = useState("ETH-USDT");
  const [error, setError] = useState("");

  const selectedTicker = tickers.find(t => (t.pair || t.instId.replace("-SWAP","")) === pair) || null;

  const fetchAccount = useCallback(async () => {
    try { setAccount(await getOkxAccount()); } catch { /* */ }
  }, []);
  const fetchTickers = useCallback(async () => {
    try { const d = await getOkxTickers(); setTickers(d.tickers); } catch { /* */ }
  }, []);
  // 静默加载上次分析缓存（不触发新分析）
  const loadScan = useCallback(async () => {
    try { setScan(await scanOkxMarket()); } catch { /* 缓存没有就算了 */ }
  }, []);
  const doScan = useCallback(async () => {
    setScanning(true); setError("");
    try {
      // force=true 强制新分析
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/okx/scan?force=true`
      );
      if (!res.ok) throw new Error(await res.text());
      setScan(await res.json());
    } catch (e) { setError(String(e)); }
    finally { setScanning(false); }
  }, []);
  const doTrade = useCallback(async (i: number) => {
    if (!scan?.plans[i]) return;
    const p = scan.plans[i];
    if (!confirm(`${p.signal} ${p.pair} @ ${p.price}\nMargin: ${p.sizing.margin}U`)) return;
    try { await quickTrade(i); await fetchAccount(); } catch (e) { setError(String(e)); }
  }, [scan, fetchAccount]);
  const doClose = useCallback(async (instId: string, posSide: string, size: string) => {
    if (!confirm(`Close ${posSide} ${instId} x${size}?`)) return;
    try { await closeOkxPosition(instId, posSide, size); await fetchAccount(); }
    catch (e) { setError(String(e)); }
  }, [fetchAccount]);

  useEffect(() => {
    fetchAccount();
    fetchTickers();
    loadScan(); // 读缓存，不触发新扫描
    const t1 = setInterval(fetchTickers, 3000);
    const t2 = setInterval(fetchAccount, 30000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [fetchAccount, fetchTickers, loadScan]);

  return (
    <div className="h-full flex flex-col bg-[#181a20] text-[#d1d4dc] overflow-hidden">
      {/* 顶部 */}
      <PriceBar pair={pair} ticker={selectedTicker} onChangePair={setPair} />

      {error && (
        <div className="px-3 py-1 bg-[#f6465d]/10 text-[#f6465d] text-[10px] flex justify-between">
          <span>{error}</span>
          <button onClick={() => setError("")} className="text-[#f6465d]/50 hover:text-[#f6465d]">[x]</button>
        </div>
      )}

      {/* 主体: 左盯盘 + 右下单 */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 border-r border-[#2b2b2b] overflow-hidden">
          <WatchPanel tickers={tickers} account={account} pair={pair} onSelect={setPair} scanData={scan} />
        </div>
        <div className="w-[300px] flex-shrink-0 flex flex-col bg-[#1e2329] overflow-hidden">
          <div className="px-3 py-1.5 text-[11px] font-medium text-[#848e9c] border-b border-[#2b2b2b]">
            {pair.replace("-USDT","")} / USDT Perpetual
          </div>
          <OrderForm pair={pair} ticker={selectedTicker} onDone={fetchAccount} />
        </div>
      </div>

      {/* 底部 */}
      <div className="h-[180px] flex-shrink-0 border-t border-[#2b2b2b] overflow-hidden">
        <BottomBar account={account} onClose={doClose} scanData={scan} onTrade={doTrade} scanning={scanning} onScan={doScan} />
      </div>
    </div>
  );
}
