"use client";

import { useState, useEffect } from "react";
import { submitOrder, placeOkxOrder, setOkxSlTp, getOkxTicker, OkxTickerData } from "@/lib/api";
import { useStore } from "@/store";

interface OrderBookProps {
  market?: "astock" | "crypto";
  pair?: string;
}

export default function OrderBook({ market = "astock", pair = "BTC-USDT" }: OrderBookProps) {
  const isCrypto = market === "crypto";

  if (isCrypto) {
    return <CryptoOrderForm pair={pair} />;
  }
  return <AstockOrderForm />;
}

// ===== Crypto 下单 =====
function CryptoOrderForm({ pair }: { pair: string }) {
  const [side, setSide] = useState<"long" | "short">("long");
  const [ordType, setOrdType] = useState<"market" | "limit">("market");
  const [size, setSize] = useState("");
  const [price, setPrice] = useState("");
  const [lever, setLever] = useState(20);
  const [slPrice, setSlPrice] = useState("");
  const [tpPrice, setTpPrice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [ticker, setTicker] = useState<OkxTickerData | null>(null);

  // 获取当前价格
  useEffect(() => {
    const fetch = async () => {
      try {
        const t = await getOkxTicker(pair);
        setTicker(t);
      } catch { /* ignore */ }
    };
    fetch();
    const interval = setInterval(fetch, 3000);
    return () => clearInterval(interval);
  }, [pair]);

  const handleSubmit = async () => {
    if (!size) return;
    setSubmitting(true);
    setMessage("");
    try {
      const swapId = pair.endsWith("-SWAP") ? pair : pair + "-SWAP";
      const result = await placeOkxOrder({
        pair,
        side: side === "long" ? "buy" : "sell",
        posSide: side,
        size,
        price: ordType === "limit" ? price : undefined,
        ordType,
        lever,
      });

      // 设置止损止盈
      if (slPrice || tpPrice) {
        await setOkxSlTp({
          instId: swapId,
          posSide: side,
          size,
          slPrice: slPrice || undefined,
          tpPrice: tpPrice || undefined,
        });
      }

      setMessage(`下单成功 #${result.orderId}`);
      setSize("");
      setSlPrice("");
      setTpPrice("");
    } catch (err) {
      setMessage(`失败: ${err}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
      <h3 className="text-sm font-bold text-white mb-3">
        {pair.split("-")[0]} 永续
        {ticker && (
          <span className="text-xs text-gray-400 ml-2 font-normal">
            {ticker.last.toLocaleString()}
          </span>
        )}
      </h3>

      {/* 多空切换 */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setSide("long")}
          className={`flex-1 py-2 rounded text-sm font-bold ${
            side === "long" ? "bg-green-600 text-white" : "bg-gray-800 text-gray-400"
          }`}
        >
          做多
        </button>
        <button
          onClick={() => setSide("short")}
          className={`flex-1 py-2 rounded text-sm font-bold ${
            side === "short" ? "bg-red-600 text-white" : "bg-gray-800 text-gray-400"
          }`}
        >
          做空
        </button>
      </div>

      {/* 订单类型 */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setOrdType("market")}
          className={`flex-1 py-1.5 rounded text-xs ${
            ordType === "market" ? "bg-gray-700 text-white" : "text-gray-400"
          }`}
        >
          市价
        </button>
        <button
          onClick={() => setOrdType("limit")}
          className={`flex-1 py-1.5 rounded text-xs ${
            ordType === "limit" ? "bg-gray-700 text-white" : "text-gray-400"
          }`}
        >
          限价
        </button>
      </div>

      {/* 限价价格 */}
      {ordType === "limit" && (
        <div className="mb-2">
          <label className="text-xs text-gray-400">价格 (USDT)</label>
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder={ticker?.last?.toString() || "0"}
            className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white text-sm"
          />
        </div>
      )}

      {/* 数量 */}
      <div className="mb-2">
        <label className="text-xs text-gray-400">数量 (张)</label>
        <input
          type="number"
          value={size}
          onChange={(e) => setSize(e.target.value)}
          placeholder="0"
          className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white text-sm"
        />
        <div className="flex gap-1 mt-1">
          {["1", "5", "10", "50"].map((v) => (
            <button
              key={v}
              onClick={() => setSize(v)}
              className="flex-1 text-xs py-1 bg-gray-800 text-gray-400 rounded hover:bg-gray-700"
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* 杠杆 */}
      <div className="mb-2">
        <label className="text-xs text-gray-400">杠杆 {lever}x</label>
        <input
          type="range"
          min={1}
          max={50}
          value={lever}
          onChange={(e) => setLever(parseInt(e.target.value))}
          className="w-full mt-1"
        />
        <div className="flex justify-between text-[10px] text-gray-500">
          <span>1x</span><span>10x</span><span>20x</span><span>50x</span>
        </div>
      </div>

      {/* 止损止盈 */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div>
          <label className="text-xs text-gray-400">止损</label>
          <input
            type="number"
            value={slPrice}
            onChange={(e) => setSlPrice(e.target.value)}
            placeholder="SL"
            className="w-full mt-1 px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-white text-xs"
          />
        </div>
        <div>
          <label className="text-xs text-gray-400">止盈</label>
          <input
            type="number"
            value={tpPrice}
            onChange={(e) => setTpPrice(e.target.value)}
            placeholder="TP"
            className="w-full mt-1 px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-white text-xs"
          />
        </div>
      </div>

      {/* 下单按钮 */}
      <button
        onClick={handleSubmit}
        disabled={submitting || !size}
        className={`w-full py-2 rounded font-bold text-sm text-white disabled:opacity-50 ${
          side === "long"
            ? "bg-green-600 hover:bg-green-700"
            : "bg-red-600 hover:bg-red-700"
        }`}
      >
        {submitting ? "提交中..." : side === "long" ? `做多 ${pair.split("-")[0]}` : `做空 ${pair.split("-")[0]}`}
      </button>

      {message && (
        <p className="mt-2 text-xs text-center text-yellow-400">{message}</p>
      )}
    </div>
  );
}

// ===== A股下单 (原逻辑) =====
function AstockOrderForm() {
  const { selectedCode, quotes } = useStore();
  const quote = quotes.get(selectedCode);
  const [direction, setDirection] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState("");
  const [volume, setVolume] = useState("10");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  const handleSubmit = async () => {
    if (!price || !volume) return;
    setSubmitting(true);
    setMessage("");
    try {
      await submitOrder({
        code: selectedCode,
        direction,
        price: parseFloat(price),
        volume: parseInt(volume),
      });
      setMessage(`${direction === "buy" ? "买入" : "卖出"}成功`);
    } catch (err) {
      setMessage(`失败: ${err}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
      <h3 className="text-sm font-bold text-white mb-3">快捷下单</h3>

      {quote && (
        <div className="mb-3 text-center">
          <div className="text-2xl font-bold text-red-400">{quote.price?.toFixed(3)}</div>
          <div className="text-xs text-gray-400">
            开 {quote.open?.toFixed(3)} | 高 {quote.high?.toFixed(3)} | 低 {quote.low?.toFixed(3)}
          </div>
        </div>
      )}

      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setDirection("buy")}
          className={`flex-1 py-2 rounded text-sm font-bold ${
            direction === "buy" ? "bg-red-600 text-white" : "bg-gray-800 text-gray-400"
          }`}
        >
          买入
        </button>
        <button
          onClick={() => setDirection("sell")}
          className={`flex-1 py-2 rounded text-sm font-bold ${
            direction === "sell" ? "bg-green-600 text-white" : "bg-gray-800 text-gray-400"
          }`}
        >
          卖出
        </button>
      </div>

      <div className="mb-2">
        <label className="text-xs text-gray-400">价格</label>
        <input
          type="number"
          step="0.001"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder={quote?.price?.toFixed(3) || "0.000"}
          className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white text-sm"
        />
      </div>

      <div className="mb-3">
        <label className="text-xs text-gray-400">数量(张)</label>
        <input
          type="number"
          value={volume}
          onChange={(e) => setVolume(e.target.value)}
          className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white text-sm"
        />
        <div className="flex gap-1 mt-1">
          {[10, 50, 100, 500].map((v) => (
            <button
              key={v}
              onClick={() => setVolume(String(v))}
              className="flex-1 text-xs py-1 bg-gray-800 text-gray-400 rounded hover:bg-gray-700"
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={handleSubmit}
        disabled={submitting}
        className={`w-full py-2 rounded font-bold text-sm ${
          direction === "buy" ? "bg-red-600 hover:bg-red-700" : "bg-green-600 hover:bg-green-700"
        } text-white disabled:opacity-50`}
      >
        {submitting ? "提交中..." : direction === "buy" ? "买入" : "卖出"}
      </button>

      {message && <p className="mt-2 text-xs text-center text-yellow-400">{message}</p>}
    </div>
  );
}
