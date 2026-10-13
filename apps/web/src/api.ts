export interface SymbolInfo {
  canonical_id: string;
  display: string;
  market_class: string;
  venue: string;
  candles?: number;
  has_data?: boolean;
  last_candle?: string | null;
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
  source: string;
  refreshed: boolean;
  refresh_skipped: string | null;
  stale: boolean;
  unexpected_gaps: string[];
  candles: Candle[];
}

export interface LevelRow {
  kind: string;
  lower: number;
  upper: number;
  centre: number;
  strength: number;
  bounces: number;
  penetrations: number;
  formed_index: number;
  known_index: number;
  time: string;
}

export interface ZoneRow {
  kind: string;
  formation: string;
  lower: number;
  upper: number;
  proximal: number;
  distal: number;
  score: number;
  fresh: boolean;
  touches: number;
  departure_atr: number;
  known_index: number;
  time: string;
}

export interface PoolRow {
  side: string;
  level: number;
  state: string;
  size: number;
  known_index: number;
  swept_index: number | null;
  time: string;
}

export interface GrabRow {
  side: string;
  pool_level: number;
  variant: string;
  depth_atr: number;
  rejection_ratio: number;
  known_index: number;
  pierce_index: number;
  extreme: number;
  time: string;
}

export interface FvgRow {
  direction: string;
  lower: number;
  upper: number;
  width_atr: number;
  variant: string;
  index: number;
  mitigated: boolean;
  time: string;
}

export interface OrderBlockRow {
  direction: string;
  lower: number;
  upper: number;
  variant: string;
  candle_index: number;
  mitigated: boolean;
  time: string;
}

export interface StructureEventRow {
  kind: string;
  direction: string;
  level: number;
  index: number;
  time: string;
}

export interface MssRow {
  direction: string;
  level: number;
  index: number;
  displacement_body_atr: number;
  time: string;
}

export interface MsnrLevelRow {
  kind: string;
  price: number;
  lower: number;
  upper: number;
  index: number;
  known_index: number;
  bounces: number;
  penetrations: number;
  bounce_probability: number;
  fresh: boolean;
  time: string;
}

export interface CrtRow {
  direction: string;
  variant: string;
  reference_high: number;
  reference_low: number;
  reference_mid: number;
  width_atr: number;
  pierce_index: number;
  known_index: number;
  entry: number;
  stop: number;
  target_1: number;
  reward_risk: number;
  time: string;
}

export interface OverlaysResponse {
  symbol: string;
  display?: string;
  timeframe: string;
  as_of: string;
  atr_period: number;
  atr: Array<{ time: string; value: number }>;
  market_class?: string;
  venue?: string;
  source?: string;
  counts: Record<string, number>;
  levels: LevelRow[];
  zones: ZoneRow[];
  pools: PoolRow[];
  grabs: GrabRow[];
  fvgs: FvgRow[];
  order_blocks: OrderBlockRow[];
  structure_events: StructureEventRow[];
  mss: MssRow[];
  msnr_levels: MsnrLevelRow[];
  crt: CrtRow[];
}

export interface SimilarCases {
  cases: number;
  hits: number;
  hit_rate: number | null;
  interval: [number, number] | null;
  radius?: number;
}

export interface SetupCard {
  module: string;
  module_version: string;
  direction: string;
  entry: number;
  stop: number;
  target: number;
  formation_time: string;
  known_time: string;
  reward_risk: number;
  baseline_p0: number;
  probability: number | null;
  score: number | null;
  expected_value: number | null;
  similar: SimilarCases;
  cost_r: number;
  reasons: string[];
  invalidation: string[];
  notes: string[];
}

export interface GateModuleRow {
  module: string;
  resolved: number;
  hits?: number;
  win_rate: number | null;
  interval: [number, number] | null;
  baseline_p0: number | null;
  enabled: boolean;
  verdict: string;
}

export interface BounceBlock {
  bounces: number;
  penetrations: number;
  resolved: number;
  probability: number | null;
  interval: [number, number] | null;
  informative: boolean | null;
  verdict: string;
}

export interface VarianceRatioBlock {
  test: string;
  primary_q: number;
  q_values: Array<{
    q: number;
    vr: number | null;
    z: number | null;
    p: number | null;
    random_walk: boolean | null;
  }>;
  primary: {
    q: number;
    vr: number | null;
    z: number | null;
    p: number | null;
    random_walk: boolean | null;
  } | null;
  rejects_random_walk: boolean;
  verdict: string;
}

export interface GateBlock {
  passed: boolean;
  verdict: string;
  reasons: string[];
  enabled_modules: string[];
  variance_ratio: VarianceRatioBlock;
  bounce: BounceBlock;
  modules: Record<string, GateModuleRow>;
}

export interface AnalysisResponse {
  analysis_version: string;
  symbol: string;
  display?: string;
  timeframe: string;
  as_of: string;
  candles: number;
  status: "setup" | "no_setup";
  reason: string | null;
  statement: string | null;
  setup: SetupCard | null;
  gate: GateBlock;
  modules: Array<Record<string, unknown> & { module: string; signals: number }>;
  model: { calibrated: boolean; [key: string]: unknown };
  candidates_considered: number;
  notes: string[];
  disclaimer: string;
  stale: boolean;
  unexpected_gaps: string[];
  costs: {
    spread_price: number;
    slippage_price: number;
    commission_price: number;
    notes: string[];
  };
  generated_at: string;
  cached: boolean;
}

export const API_BASE: string =
  (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export function getSymbols(): Promise<SymbolInfo[]> {
  return getJson<{ symbols: SymbolInfo[] }>("/symbols").then((body) => body.symbols);
}

export function getCandles(
  symbol: string,
  timeframe: string,
  limit = 500,
  refresh = false
): Promise<CandlesResponse> {
  const q = `symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}${
    refresh ? "&refresh=true" : ""
  }`;
  return getJson<CandlesResponse>(`/candles?${q}`);
}

export function getOverlays(
  symbol: string,
  timeframe: string,
  bars = 700,
  refresh = false
): Promise<OverlaysResponse> {
  const q = `timeframe=${timeframe}&bars=${bars}${refresh ? "&refresh=true" : ""}`;
  return getJson<OverlaysResponse>(`/overlays/${encodeURIComponent(symbol)}?${q}`);
}

export function getAnalysis(
  symbol: string,
  timeframe: string,
  options: { bars?: number; refresh?: boolean } = {}
): Promise<AnalysisResponse> {
  const q = `timeframe=${timeframe}&bars=${options.bars ?? 1200}${options.refresh ? "&refresh=true" : ""}`;
  return getJson<AnalysisResponse>(`/analysis/${encodeURIComponent(symbol)}?${q}`);
}
