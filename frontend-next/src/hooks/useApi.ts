// src/hooks/useApi.ts
export {
  // strategy control
  apiStartSymbols,
  apiStopSymbols,
  apiStopAll,
  
  // strategy params
  getStrategyParams,
  setStrategyParams,
  
  // watchlist
  apiWatchlistBulk,
  
  // positions / metrics
  apiGetExecPositions,
  apiEnsurePositions,
  apiGetExecPosition,
  apiGetMetrics,
  
  // UI snapshot
  apiGetUISnapshot,
  
  // execution
  apiPlaceOrder,
  apiFlatten,
  apiCancel,
  
  // types
  type StrategyParams,
  type StrategyStartResponse,
  type StrategyStopResponse,
  type StopAllResponse,
} from "@/api/api";

// ✅ Aliases for backward compatibility
export {
  apiGetExecPositions as apiGetPositions,
  apiEnsurePositions as apiGetAllPositions,
  apiGetExecPosition as apiGetPosition,
} from "@/api/api";