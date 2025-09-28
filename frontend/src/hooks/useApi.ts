// src/hooks/useApi.ts

export {
  // strategy control
  apiStartSymbols,
  apiStopSymbols,
  apiStopAll,

  // strategy params
  getStrategyParams,
  setStrategyParams,
  type StrategyParams,

  // watchlist
  apiWatchlistBulk,

  // positions / metrics
  apiGetPositions,
  apiGetAllPositions,
  apiGetExecPositions,
  apiGetPosition,
  apiGetMetrics,

  // UI snapshot
  apiGetUISnapshot,

  // execution
  apiPlaceOrder,
  apiFlatten,
  apiCancel,

  // response types
  type StrategyStartResponse,
  type StrategyStopResponse,
  type StopAllResponse,
} from "@/api/api";
