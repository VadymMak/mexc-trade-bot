export interface WatchlistItem {
  symbol: string;
  running?: boolean;
}

export interface WatchlistBulkOut {
  items: WatchlistItem[];
  revision?: number;
}

export interface WatchlistBulkIn {
  symbols: string[];
}
