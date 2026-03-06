import { create } from "zustand";
import type { Quote, Position, Strategy } from "@/lib/api";

interface AppState {
  // 选中的证券
  selectedCode: string;
  setSelectedCode: (code: string) => void;

  // 实时行情
  quotes: Map<string, Quote>;
  updateQuotes: (quotes: Quote[]) => void;

  // 持仓
  positions: Position[];
  setPositions: (positions: Position[]) => void;

  // 策略
  strategies: Strategy[];
  setStrategies: (strategies: Strategy[]) => void;

  // 连接状态
  wsConnected: boolean;
  setWsConnected: (connected: boolean) => void;
  tdxConnected: boolean;
  setTdxConnected: (connected: boolean) => void;
}

export const useStore = create<AppState>((set) => ({
  selectedCode: "128025",
  setSelectedCode: (code) => set({ selectedCode: code }),

  quotes: new Map(),
  updateQuotes: (quotes) =>
    set((state) => {
      const map = new Map(state.quotes);
      for (const q of quotes) {
        map.set(q.code, q);
      }
      return { quotes: map };
    }),

  positions: [],
  setPositions: (positions) => set({ positions }),

  strategies: [],
  setStrategies: (strategies) => set({ strategies }),

  wsConnected: false,
  setWsConnected: (connected) => set({ wsConnected: connected }),
  tdxConnected: false,
  setTdxConnected: (connected) => set({ tdxConnected: connected }),
}));
