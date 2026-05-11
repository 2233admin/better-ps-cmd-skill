"use client";

import { useState } from "react";
import { getPredict, Prediction } from "@/lib/api";
import { useStore } from "@/store";

export default function AIPanel() {
  const { selectedCode } = useStore();
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(false);

  const handlePredict = async () => {
    setLoading(true);
    try {
      const res = await getPredict(selectedCode);
      setPrediction(res);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const dirLabel = (d: string) =>
    d === "up" ? "看涨" : d === "down" ? "看跌" : "中性";
  const dirColor = (d: string) =>
    d === "up" ? "text-red-400" : d === "down" ? "text-green-400" : "text-gray-400";

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-white">AI 分析</h3>
        <button
          onClick={handlePredict}
          disabled={loading}
          className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded disabled:opacity-50"
        >
          {loading ? "分析中..." : "预测"}
        </button>
      </div>

      {prediction ? (
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-xs text-gray-400">预测方向</span>
            <span className={`text-lg font-bold ${dirColor(prediction.direction)}`}>
              {dirLabel(prediction.direction)}
            </span>
          </div>

          <div className="flex justify-between">
            <span className="text-xs text-gray-400">置信度</span>
            <span className="text-xs text-white">
              {(prediction.confidence * 100).toFixed(1)}%
            </span>
          </div>

          <div className="w-full bg-gray-800 rounded-full h-2">
            <div
              className={`h-2 rounded-full ${
                prediction.confidence > 0.7
                  ? "bg-green-500"
                  : prediction.confidence > 0.4
                  ? "bg-yellow-500"
                  : "bg-red-500"
              }`}
              style={{ width: `${prediction.confidence * 100}%` }}
            />
          </div>

          <div className="flex justify-between">
            <span className="text-xs text-gray-400">预测涨跌幅</span>
            <span
              className={`text-xs ${
                prediction.predicted_change >= 0 ? "text-red-400" : "text-green-400"
              }`}
            >
              {prediction.predicted_change >= 0 ? "+" : ""}
              {(prediction.predicted_change * 100).toFixed(2)}%
            </span>
          </div>

          <div className="flex justify-between">
            <span className="text-xs text-gray-400">模型</span>
            <span className="text-xs text-gray-300">{prediction.model}</span>
          </div>

          <div className="flex justify-between">
            <span className="text-xs text-gray-400">状态</span>
            <span className="text-xs text-gray-300">{prediction.status}</span>
          </div>
        </div>
      ) : (
        <p className="text-xs text-gray-500 text-center py-4">
          点击预测按钮获取 AI 分析结果
        </p>
      )}
    </div>
  );
}
