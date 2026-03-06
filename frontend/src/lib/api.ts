const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// === Market API ===
export async function getQuotes(codes: string) {
  return fetchAPI<{ data: Quote[]; count: number }>(`/market/quotes?codes=${codes}`);
}

export async function getKline(code: string, category = 9, count = 800) {
  return fetchAPI<{ data: KlineBar[]; count: number }>(
    `/market/kline/${code}?category=${category}&count=${count}`
  );
}

export async function getTick(code: string) {
  return fetchAPI<{ data: TickData[]; count: number }>(`/market/tick/${code}`);
}

export async function getMinute(code: string) {
  return fetchAPI<{ data: MinuteData[]; count: number }>(`/market/minute/${code}`);
}

// === Bond API ===
export async function getBondList() {
  return fetchAPI<{ data: BondInfo[]; count: number }>("/bond/list");
}

export async function getBondQuotes() {
  return fetchAPI<{ data: Quote[]; count: number }>("/bond/quotes");
}

// === Portfolio API ===
export async function getPositions() {
  return fetchAPI<{ data: Position[]; count: number }>("/portfolio/positions");
}

export async function getTrades(limit = 100) {
  return fetchAPI<{ data: Trade[]; count: number }>(`/portfolio/trades?limit=${limit}`);
}

export async function getPnl() {
  return fetchAPI<PnlSummary>("/portfolio/pnl");
}

// === Strategy API ===
export async function getStrategies() {
  return fetchAPI<{ data: Strategy[] }>("/strategy/list");
}

export async function startStrategy(name: string, params: Record<string, unknown> = {}) {
  return fetchAPI("/strategy/start", {
    method: "POST",
    body: JSON.stringify({ name, params }),
  });
}

export async function stopStrategy(name: string) {
  return fetchAPI("/strategy/stop", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function runBacktest(params: BacktestRequest) {
  return fetchAPI<BacktestResponse>("/strategy/backtest", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

// === Order API ===
export async function submitOrder(order: OrderRequest) {
  return fetchAPI("/order/submit", {
    method: "POST",
    body: JSON.stringify(order),
  });
}

// === AI API ===
export async function getPredict(code: string) {
  return fetchAPI<Prediction>(`/ai/predict/${code}`);
}

export async function getFactors(code: string) {
  return fetchAPI<{ data: Record<string, number>; code: string }>(`/ai/factors/${code}`);
}

// === Macro API ===
export async function getMacroIndicators(category?: string) {
  const q = category ? `?category=${category}` : "";
  return fetchAPI<{ data: MacroIndicator[]; count: number }>(`/macro/indicators${q}`);
}

export async function fetchMacroIndicators(codes?: string[], category?: string) {
  return fetchAPI<{ indicators_fetched: number; total_records: number }>("/macro/indicators/fetch", {
    method: "POST",
    body: JSON.stringify({ codes, category }),
  });
}

export async function getIndicatorHistory(code: string, limit = 24) {
  return fetchAPI<{ code: string; data: { date: string; value: number }[]; count: number }>(
    `/macro/indicators/${code}/history?limit=${limit}`
  );
}

export async function getIndicatorRegistry() {
  return fetchAPI<{ indicators: IndicatorRegistryItem[]; categories: Record<string, string> }>(
    "/macro/indicators/registry"
  );
}

export async function generateBriefing(focusCategory?: string) {
  return fetchAPI<MacroBriefing>("/macro/briefing/generate", {
    method: "POST",
    body: JSON.stringify({ focus_category: focusCategory }),
  });
}

export async function getBriefingHistory(limit = 10) {
  return fetchAPI<{ data: MacroBriefing[]; count: number }>(`/macro/briefing/history?limit=${limit}`);
}

export async function getAllocation() {
  return fetchAPI<{ data: AllocationRecord[]; count: number }>("/macro/allocation");
}

export async function updateAllocation(record: AllocationUpdateRequest) {
  return fetchAPI<AllocationRecord>("/macro/allocation", {
    method: "POST",
    body: JSON.stringify(record),
  });
}

// === WebSocket ===
export function createWS(channel: string): WebSocket {
  return new WebSocket(`${WS_BASE}/ws/${channel}`);
}

// === Types ===
export interface Quote {
  code: string;
  name?: string;
  price: number;
  open: number;
  high: number;
  low: number;
  close: number;
  vol: number;
  amount: number;
  bid1: number;
  ask1: number;
  [key: string]: unknown;
}

export interface KlineBar {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
}

export interface TickData {
  time: string;
  price: number;
  vol: number;
  buyorsell: number;
}

export interface MinuteData {
  price: number;
  vol: number;
}

export interface BondInfo {
  [key: string]: unknown;
}

export interface Position {
  code: string;
  market: number;
  volume: number;
  avg_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
}

export interface Trade {
  id: number;
  code: string;
  direction: string;
  price: number;
  volume: number;
  amount: number;
  strategy: string;
  status: string;
  created_at: string;
}

export interface PnlSummary {
  total_pnl: number;
  position_count: number;
  today_trades: number;
}

export interface Strategy {
  name: string;
  description: string;
  state: string;
  pnl: number;
  trade_count: number;
  params: Record<string, unknown>;
}

export interface OrderRequest {
  code: string;
  direction: "buy" | "sell";
  price: number;
  volume: number;
  strategy?: string;
}

export interface BacktestRequest {
  code: string;
  strategy: string;
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  params?: Record<string, unknown>;
}

export interface BacktestResponse {
  code: string;
  strategy: string;
  result: {
    total_return: number;
    annual_return: number;
    sharpe_ratio: number;
    max_drawdown: number;
    win_rate: number;
    total_trades: number;
    profit_trades: number;
    loss_trades: number;
    avg_profit: number;
    avg_loss: number;
    profit_factor: number;
  };
  equity_curve: number[];
  trade_count: number;
}

export interface MacroIndicator {
  code: string;
  category: string;
  name: string;
  date: string;
  value: number;
  unit: string;
}

export interface IndicatorRegistryItem {
  code: string;
  name: string;
  category: string;
  frequency: string;
  unit: string;
  description: string;
}

export interface MacroBriefing {
  content: string;
  generated_at: string;
  indicator_count: number;
  model: string;
  focus: string;
  error?: string;
  message?: string;
}

export interface AllocationRecord {
  date: string;
  asset_class: string;
  target_pct: number;
  actual_pct: number;
  value: number;
  notes?: string;
}

export interface AllocationUpdateRequest {
  asset_class: string;
  target_pct: number;
  actual_pct: number;
  value?: number;
  notes?: string;
}

// === OKX API ===
export async function getOkxKline(pair: string, bar = "1m", limit = 200) {
  return fetchAPI<{ data: KlineBar[]; count: number }>(
    `/okx/kline/${pair}?bar=${bar}&limit=${limit}`
  );
}

export async function getOkxAccount() {
  return fetchAPI<OkxAccountData>("/okx/account");
}

export async function scanOkxMarket() {
  return fetchAPI<OkxScanData>("/okx/scan");
}

export async function getOkxSignals(limit = 20) {
  return fetchAPI<{ signals: OkxSignal[] }>(`/okx/signals?limit=${limit}`);
}

export async function quickTrade(planIndex: number) {
  return fetchAPI<OkxTradeResult>("/okx/quick-trade", {
    method: "POST",
    body: JSON.stringify({ planIndex }),
  });
}

export async function closeOkxPosition(instId: string, posSide: string, size: string) {
  return fetchAPI<{ status: string; orderId: string }>("/okx/close", {
    method: "POST",
    body: JSON.stringify({ instId, posSide, size }),
  });
}

export async function getOkxTickers() {
  return fetchAPI<{ tickers: OkxTickerData[]; ts: number }>("/okx/tickers");
}

export async function getOkxTicker(instId: string) {
  return fetchAPI<OkxTickerData>(`/okx/ticker/${instId}`);
}

export async function placeOkxOrder(order: OkxOrderRequest) {
  return fetchAPI<{ orderId: string; status: string }>("/okx/trade", {
    method: "POST",
    body: JSON.stringify(order),
  });
}

export async function setOkxSlTp(req: OkxSlTpRequest) {
  return fetchAPI<{ results: { type: string; code: string }[] }>("/okx/set-sl-tp", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export interface OkxAccountData {
  account: { equity: number; available: number; frozen: number; upl: number };
  positions: OkxPosition[];
  algos: { instId: string; sz: string; slTriggerPx: string; tpTriggerPx: string }[];
}

export interface OkxPosition {
  instId: string;
  posSide: string;
  pos: string;
  avgPx: string;
  markPx: string;
  upl: number;
  lever: string;
  notionalUsd: string;
}

export interface OkxMarketItem {
  pair: string;
  price: number;
  change: number;
  amplitude: number;
  vwapDev: number;
  fundingRate: number;
  takerRatio: number;
  lsRatio: number;
  basis: number;
  trend4h: string;
  volTrend: string;
  posInRange: number;
  signal: string;
  confidence: number;
  reason: string;
  contradictions: string[];
  support: number;
  resistance: number;
}

export interface OkxTradePlan {
  pair: string;
  signal: string;
  confidence: number;
  price: number;
  reason: string;
  sizing: { notional: number; margin: number; stop_loss_pct: number; take_profit_pct: number };
  levels: { support: number; resistance: number; stop_loss: number; take_profit: number };
  contradictions: string[];
  conditions: Record<string, string>;
}

export interface OkxScanData {
  market: OkxMarketItem[];
  plans: OkxTradePlan[];
}

export interface OkxSignal {
  time: string;
  pair: string;
  signal: string;
  confidence: number;
  price: number;
  reason: string;
  contradictions: string[];
}

export interface OkxTradeResult {
  orderId: string;
  signal: string;
  pair: string;
  price: number;
  size: string;
  stopLoss: number;
  takeProfit: number;
  margin: number;
}

export interface OkxTickerData {
  instId: string;
  pair?: string;
  last: number;
  askPx: number;
  bidPx: number;
  high24h: number;
  low24h: number;
  vol24h: number;
  change?: number;
}

export interface OkxOrderRequest {
  pair: string;
  side: "buy" | "sell";
  posSide: "long" | "short";
  size: string;
  price?: string;
  ordType: "limit" | "market";
  lever?: number;
}

export interface OkxSlTpRequest {
  instId: string;
  posSide: string;
  size: string;
  slPrice?: string;
  tpPrice?: string;
}

export interface Prediction {
  code: string;
  direction: string;
  confidence: number;
  predicted_change: number;
  model: string;
  status: string;
}
