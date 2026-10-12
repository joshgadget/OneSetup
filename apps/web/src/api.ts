export interface SymbolInfo {
  canonical_id: string;
  display: string;
  market_class: string;
  venue: string;
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  closed: boolean;
}

export interface CandlesResponse {
  symbol: string;
  timeframe: string;
  count: number;
  stale: boolean;
  unexpected_gaps: string[];
  candles: Candle[];
}

export const API_BASE: string =
  (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function getSymbols(): Promise<SymbolInfo[]> {
  const res = await fetch(`${API_BASE}/symbols`);
  if (!res.ok) throw new Error(`symbols failed: ${res.status}`);
  const body = await res.json();
  return body.symbols as SymbolInfo[];
}

export async function getCandles(symbol: string, timeframe: string, limit = 500): Promise<CandlesResponse> {
  const url = `${API_BASE}/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`;
  const res = await fetch(url);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `candles failed: ${res.status}`);
  }
  return (await res.json()) as CandlesResponse;
}