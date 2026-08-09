// Shapes returned by GET /api/symbols/{symbol}/options-chain

export interface OptionContract {
  strike: number | null;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

// Keyed by expiration (YYYYMMDD) → strike (str float) → contract.
// Legacy docs may store an array of contracts per expiration.
export type OptionBucket = Record<string, Record<string, OptionContract> | OptionContract[]>;

export interface OptionsChainResponse {
  symbol: string;
  timestamp: string;
  calls: OptionBucket;
  puts: OptionBucket;
  error?: string;
}
