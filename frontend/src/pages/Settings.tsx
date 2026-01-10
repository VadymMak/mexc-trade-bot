// src/pages/Settings.tsx
import { useState, useEffect } from "react";
import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";
import PageToolbar from "@/components/layout/PageToolbar";
import { useProvider } from "@/store/provider";
import ProviderSwitch from "@/components/settings/ProviderSwitch";

interface RiskLimits {
  account_balance_usd: number;
  max_position_size_usd: number;
  max_positions: number;
  max_exposure_per_position_pct: number;
}

interface StrategyParams {
  order_size_usd: number;
  take_profit_bps: number;
  stop_loss_bps: number;
  timeout_exit_sec: number;
  max_concurrent_symbols: number;
  enable_trailing_stop: boolean;
  trailing_activation_bps: number;
  trailing_stop_bps: number;
}

interface AllocationData {
  mode: string;
  total_capital: number;
  position_size_usd: number;
  max_positions: number;
  active_symbols: string[];
  allocations: Record<string, {
    allocated_usd: number;
    allocation_pct: number;
    max_positions: number;
    depth_5bps: number | null;
    smart_score?: number;  // NEW: only present in smart mode
  }>;
}

interface OpenPosition {
  symbol: string;
  qty: number;
  avg_price: number;
}

interface ScheduleSettings {
  trading_schedule_enabled: boolean;
  trading_start_time: string;
  trading_end_time: string;
  trading_timezone: string;
  trade_on_weekends: boolean;
  close_before_end_minutes: number;
}

export default function Settings() {
  const toast = useToast();
  
  // State
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // Risk Limits
  const [riskLimits, setRiskLimits] = useState<RiskLimits>({
    account_balance_usd: 1000,
    max_position_size_usd: 200,
    max_positions: 5,
    max_exposure_per_position_pct: 20,
  });
  
  const [strategyParams, setStrategyParams] = useState<StrategyParams>({
    order_size_usd: 50,
    take_profit_bps: 2.0,
    stop_loss_bps: -3.0,
    timeout_exit_sec: 30,
    max_concurrent_symbols: 10,
    // Trailing Stop
    enable_trailing_stop: false,
    trailing_activation_bps: 1.5,
    trailing_stop_bps: 0.5,
  });

  const [scheduleSettings, setScheduleSettings] = useState<ScheduleSettings>({
    trading_schedule_enabled: false,
    trading_start_time: "10:00",
    trading_end_time: "20:00",
    trading_timezone: "Europe/Istanbul",
    trade_on_weekends: true,
    close_before_end_minutes: 10,
  });
  
  // Allocation
  const [allocationMode, setAllocationMode] = useState<"equal" | "dynamic" | "smart">("equal");
  const [allocationData, setAllocationData] = useState<AllocationData | null>(null);
  const currentMode = useProvider((s) => s.mode);
  
  // Load data on mount
  useEffect(() => {
    void loadAllData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  
  const loadAllData = async () => {
    setLoading(true);
    try {
      // Load risk limits
      const riskRes = await fetch("/api/risk/limits");
      if (riskRes.ok) {
        const data = await riskRes.json();
        setRiskLimits({
          account_balance_usd: data.account_balance_usd,
          max_position_size_usd: data.max_position_size_usd,
          max_positions: data.max_positions,
          max_exposure_per_position_pct: data.max_exposure_per_position_pct,
        });
      }
      
      // Load strategy params
      const paramsRes = await fetch("/api/strategy/params");
      if (paramsRes.ok) {
        const data = await paramsRes.json();
        setStrategyParams({
          order_size_usd: data.order_size_usd,
          take_profit_bps: data.take_profit_bps,
          stop_loss_bps: data.stop_loss_bps,
          timeout_exit_sec: data.timeout_exit_sec,
          max_concurrent_symbols: data.max_concurrent_symbols,
          // Trailing Stop
          enable_trailing_stop: data.enable_trailing_stop ?? false,
          trailing_activation_bps: data.trailing_activation_bps ?? 1.5,
          trailing_stop_bps: data.trailing_stop_bps ?? 0.5,
        });
        
        // Load schedule settings from same response
        setScheduleSettings({
          trading_schedule_enabled: data.trading_schedule_enabled ?? false,
          trading_start_time: data.trading_start_time ?? "10:00",
          trading_end_time: data.trading_end_time ?? "20:00",
          trading_timezone: data.trading_timezone ?? "Europe/Istanbul",
          trade_on_weekends: data.trade_on_weekends ?? true,
          close_before_end_minutes: data.close_before_end_minutes ?? 10,
        });
      }
      
      // Load allocation mode
      const modeRes = await fetch("/api/allocation/mode");
      if (modeRes.ok) {
        const data = await modeRes.json();
        setAllocationMode(data.mode);
      }
      
      // Load allocation data
      await loadAllocationData();
      
    } catch (e) {
      toast.error(getErrorMessage(e), "Settings");
    } finally {
      setLoading(false);
    }
  };
  
  const loadAllocationData = async () => {
    try {
      const res = await fetch("/api/allocation/calculate");
      if (res.ok) {
        const data = await res.json();
        setAllocationData(data);
      }
    } catch (e) {
      console.error("Failed to load allocation:", e);
    }
  };
  
  const saveRiskLimits = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/risk/limits", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(riskLimits),
      });
      
      if (!res.ok) throw new Error("Failed to save risk limits");
      
      toast.success("Risk limits saved");
      await loadAllocationData(); // Recalculate allocation
    } catch (e) {
      toast.error(getErrorMessage(e), "Save Error");
    } finally {
      setSaving(false);
    }
  };
  
  const saveStrategyParams = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/strategy/params", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...strategyParams, ...scheduleSettings }),
      });
      
      if (!res.ok) {
        // Handle 409 (positions open) specially
        if (res.status === 409) {
          const errorData = await res.json();
          const detail = errorData.detail || {};
          const openPositions = detail.open_positions || [];
          const posCount = detail.count || openPositions.length;
          
          // Build informative error message
          let message = detail.message || "Cannot update parameters while positions are open";
          if (posCount > 0) {
            const symbols = openPositions.map((p: OpenPosition) => p.symbol).join(", ");
            message += `\n\nOpen positions (${posCount}): ${symbols}`;
            message += "\n\nPlease close all positions first (use Stop Strategy with flatten).";
          }
          
          toast.error(message, "Positions Open");
          return;
        }
        
        throw new Error("Failed to save strategy params");
      }
      
      const data = await res.json();
      
      // Success message
      if (data.applied_immediately) {
        toast.success(
          "Parameters updated and applied immediately (no restart needed)",
          "Strategy Parameters"
        );
      } else {
        toast.success("Strategy parameters saved");
      }
      
      await loadAllocationData(); // Recalculate allocation
    } catch (e) {
      toast.error(getErrorMessage(e), "Save Error");
    } finally {
      setSaving(false);
    }
  };
  
  const saveAllocationMode = async (mode: "equal" | "dynamic" | "smart") => {
    setSaving(true);
    try {
      const res = await fetch("/api/allocation/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      
      if (!res.ok) throw new Error("Failed to save allocation mode");
      
      setAllocationMode(mode);
      toast.success(`Allocation mode: ${mode}`);
      await loadAllocationData(); // Recalculate
    } catch (e) {
      toast.error(getErrorMessage(e), "Save Error");
    } finally {
      setSaving(false);
    }
  };
  
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-zinc-400">Loading settings...</div>
      </div>
    );
  }
  
  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Simple Navigation */}
      <div className="border-b border-zinc-800 bg-zinc-900 px-6 py-3">
        <div className="flex justify-end">
          <PageToolbar />
        </div>
      </div>
      
      <div className="px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
        
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Settings</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Configure capital allocation, risk limits, and strategy parameters
          </p>
        </div>
        
        {/* Capital Management */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
          <h2 className="mb-4 text-xl font-semibold text-zinc-100">💰 Capital Management</h2>
          
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Total Capital (USD)</label>
                <input
                  type="number"
                  value={riskLimits.account_balance_usd}
                  onChange={(e) => setRiskLimits(prev => ({ ...prev, account_balance_usd: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Max Positions</label>
                <input
                  type="number"
                  value={riskLimits.max_positions}
                  onChange={(e) => setRiskLimits(prev => ({ ...prev, max_positions: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Max Per Symbol (USD)</label>
                <input
                  type="number"
                  value={riskLimits.max_position_size_usd}
                  onChange={(e) => setRiskLimits(prev => ({ ...prev, max_position_size_usd: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Max Exposure Per Position (%)</label>
                <input
                  type="number"
                  value={riskLimits.max_exposure_per_position_pct}
                  onChange={(e) => setRiskLimits(prev => ({ ...prev, max_exposure_per_position_pct: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
            </div>
            
            <button
              onClick={saveRiskLimits}
              disabled={saving}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save Capital Settings"}
            </button>
          </div>
        </div>

        {/* Execution Mode */}
        <div className={`rounded-xl border p-6 ${
          currentMode === "LIVE" 
            ? "border-rose-500 bg-rose-950/30" 
            : "border-zinc-800 bg-zinc-900"
        }`}>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-zinc-100">
              🎮 Execution Mode
            </h2>
            {currentMode === "LIVE" && (
              <div className="flex items-center gap-2 rounded-lg bg-rose-500/20 px-3 py-1.5 text-rose-400">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75"></span>
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-rose-500"></span>
                </span>
                <span className="text-sm font-semibold">LIVE TRADING ACTIVE</span>
              </div>
            )}
          </div>
          
          <div className="space-y-4">
            {/* Provider Switch Component */}
            <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <ProviderSwitch />
            </div>
            
            {/* Mode Explanation */}
            <div className="space-y-2 text-sm">
              <div className="flex items-start gap-2">
                <span className="text-zinc-500">📄</span>
                <div>
                  <span className="font-medium text-zinc-300">PAPER:</span>
                  <span className="text-zinc-400"> Simulated trading with fake fills (instant execution)</span>
                </div>
              </div>
              <div className="flex items-start gap-2">
                <span className="text-zinc-500">🧪</span>
                <div>
                  <span className="font-medium text-zinc-300">DEMO:</span>
                  <span className="text-zinc-400"> Simulated trading with realistic slippage and delays</span>
                </div>
              </div>
              <div className="flex items-start gap-2">
                <span className="text-rose-400">💰</span>
                <div>
                  <span className="font-medium text-rose-300">LIVE:</span>
                  <span className="text-rose-400"> Real money, real orders on exchange ⚠️</span>
                </div>
              </div>
            </div>
            
            {/* Warning Box for LIVE mode */}
            {currentMode === "LIVE" && (
              <div className="rounded-lg border border-rose-500/50 bg-rose-950/50 p-4">
                <div className="mb-2 flex items-center gap-2 text-rose-400">
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <span className="font-semibold">LIVE MODE ACTIVE</span>
                </div>
                <ul className="ml-7 space-y-1 text-sm text-rose-300">
                  <li>• All orders will be placed on real exchange</li>
                  <li>• Real capital at risk</li>
                  <li>• Monitor positions closely</li>
                  <li>• Use Emergency Stop if needed</li>
                </ul>
              </div>
            )}
            
            {/* Info Box for DEMO/PAPER mode */}
            {currentMode !== "LIVE" && (
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 p-4">
                <div className="flex items-center gap-2 text-emerald-400">
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span className="text-sm font-medium">Safe Mode: No real money at risk</span>
                </div>
              </div>
            )}
          </div>
        </div>
        
        {/* Allocation Mode */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <h2 className="mb-4 text-xl font-semibold text-zinc-100">📊 Allocation Mode</h2>
        
        <div className="space-y-4">
          {/* Radio buttons */}
          <div className="space-y-3">
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-zinc-700 p-4 transition-colors hover:border-zinc-600">
              <input
                type="radio"
                name="allocation"
                checked={allocationMode === "equal"}
                onChange={() => saveAllocationMode("equal")}
                disabled={saving}
                className="h-4 w-4 border-zinc-600 bg-zinc-800 text-emerald-500 focus:ring-emerald-500"
              />
              <div className="flex-1">
                <div className="font-medium text-zinc-100">Equal (split evenly)</div>
                <div className="text-sm text-zinc-400">All symbols get same capital</div>
              </div>
            </label>
            
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-zinc-700 p-4 transition-colors hover:border-zinc-600">
              <input
                type="radio"
                name="allocation"
                checked={allocationMode === "dynamic"}
                onChange={() => saveAllocationMode("dynamic")}
                disabled={saving}
                className="h-4 w-4 border-zinc-600 bg-zinc-800 text-emerald-500 focus:ring-emerald-500"
              />
              <div className="flex-1">
                <div className="font-medium text-zinc-100">Dynamic (based on liquidity)</div>
                <div className="text-sm text-zinc-400">Higher liquidity → more capital</div>
              </div>
            </label>
            
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-zinc-700 p-4 transition-colors hover:border-zinc-600">
              <input
                type="radio"
                name="allocation"
                checked={allocationMode === "smart"}
                onChange={() => saveAllocationMode("smart")}
                disabled={saving}
                className="h-4 w-4 border-zinc-600 bg-zinc-800 text-emerald-500 focus:ring-emerald-500"
              />
              <div className="flex-1">
                <div className="font-medium text-zinc-100">
                  Smart (performance-based)
                  <span className="ml-2 rounded bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-400">Recommended</span>
                </div>
                <div className="text-sm text-zinc-400">
                  Best performers get more capital (Win Rate 40% + Avg PnL 30% + Liquidity 20% + Spread 10%)
                </div>
              </div>
            </label>
          </div>
    
            {/* Allocation Preview */}
            {allocationData && allocationData.active_symbols.length > 0 && (
              <div className="mt-4 rounded-lg border border-zinc-700 bg-zinc-800 p-4">
                <h3 className="mb-3 text-sm font-medium text-zinc-300">Allocation Preview:</h3>
                <div className="space-y-2">
                  {allocationData.active_symbols.map(sym => {
                    const alloc = allocationData.allocations[sym];
                    return (
                      <div key={sym} className="flex items-center justify-between text-sm">
                        <span className="text-zinc-400">{sym}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-zinc-300">
                            ${alloc.allocated_usd.toFixed(0)} ({alloc.allocation_pct.toFixed(1)}%)
                          </span>
                          {alloc.smart_score && (
                            <span className="rounded bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-400">
                              Score: {alloc.smart_score.toFixed(0)}
                            </span>
                          )}
                          <span className="text-zinc-500">• {alloc.max_positions} pos</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            
            {allocationData && allocationData.active_symbols.length === 0 && (
              <div className="text-sm text-zinc-500">
                No active symbols. Start trading to see allocation.
              </div>
            )}
          </div>
        </div>
        
        {/* Strategy Parameters */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
          <h2 className="mb-4 text-xl font-semibold text-zinc-100">⚙️ Trading Parameters</h2>
          
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Position Size (USD)</label>
                <input
                  type="number"
                  value={strategyParams.order_size_usd}
                  onChange={(e) => setStrategyParams(prev => ({ ...prev, order_size_usd: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Timeout (seconds)</label>
                <input
                  type="number"
                  value={strategyParams.timeout_exit_sec}
                  onChange={(e) => setStrategyParams(prev => ({ ...prev, timeout_exit_sec: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
            </div>
            
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Take Profit (bps)</label>
                <input
                  type="number"
                  step="0.1"
                  value={strategyParams.take_profit_bps}
                  onChange={(e) => setStrategyParams(prev => ({ ...prev, take_profit_bps: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Stop Loss (bps)</label>
                <input
                  type="number"
                  step="0.1"
                  value={strategyParams.stop_loss_bps}
                  onChange={(e) => setStrategyParams(prev => ({ ...prev, stop_loss_bps: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Max Concurrent Symbols</label>
                <input
                  type="number"
                  value={strategyParams.max_concurrent_symbols}
                  onChange={(e) => setStrategyParams(prev => ({ ...prev, max_concurrent_symbols: Number(e.target.value) }))}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100"
                />
              </div>
            </div>
            
            {/* Trailing Stop Section */}
            <div className="space-y-4 rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium text-zinc-100">Enable Trailing Stop</div>
                  <div className="text-sm text-zinc-400">Lock in profits as price moves favorably</div>
                </div>
                <label className="relative inline-flex cursor-pointer items-center">
                  <input
                    type="checkbox"
                    checked={strategyParams.enable_trailing_stop}
                    onChange={(e) => setStrategyParams(prev => ({ ...prev, enable_trailing_stop: e.target.checked }))}
                    className="peer sr-only"
                  />
                  <div className="peer h-6 w-11 rounded-full bg-zinc-700 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-zinc-600 after:bg-white after:transition-all after:content-[''] peer-checked:bg-emerald-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-emerald-500"></div>
                </label>
              </div>
              
              {strategyParams.enable_trailing_stop && (
                <div className="grid grid-cols-2 gap-4 border-t border-zinc-700 pt-4">
                  <div>
                    <label className="mb-1 block text-sm text-zinc-400">
                      Activation Threshold (bps)
                      <span className="ml-1 text-zinc-500">🎯</span>
                    </label>
                    <input
                      type="number"
                      step="0.1"
                      value={strategyParams.trailing_activation_bps}
                      onChange={(e) => setStrategyParams(prev => ({ ...prev, trailing_activation_bps: Number(e.target.value) }))}
                      className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100"
                    />
                    <div className="mt-1 text-xs text-zinc-500">Profit to activate trailing</div>
                  </div>
                  
                  <div>
                    <label className="mb-1 block text-sm text-zinc-400">
                      Trail Distance (bps)
                      <span className="ml-1 text-zinc-500">📏</span>
                    </label>
                    <input
                      type="number"
                      step="0.1"
                      value={strategyParams.trailing_stop_bps}
                      onChange={(e) => setStrategyParams(prev => ({ ...prev, trailing_stop_bps: Number(e.target.value) }))}
                      className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100"
                    />
                    <div className="mt-1 text-xs text-zinc-500">Distance from peak price</div>
                  </div>
                </div>
              )}
              
              {strategyParams.enable_trailing_stop && (
                <div className="rounded-lg bg-blue-950/30 border border-blue-500/30 p-3 text-sm text-blue-400">
                  <div className="flex items-start gap-2">
                    <svg className="mt-0.5 h-4 w-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <div>
                      <span className="font-medium">How it works:</span> When profit reaches{" "}
                      <span className="font-mono">{strategyParams.trailing_activation_bps}</span> bps, stop-loss automatically follows price at{" "}
                      <span className="font-mono">{strategyParams.trailing_stop_bps}</span> bps behind peak
                    </div>
                  </div>
                </div>
              )}
            </div>
            
            <button
              onClick={saveStrategyParams}
              disabled={saving}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save Strategy Parameters"}
            </button>
          </div>
        </div>
        {/* Trading Schedule */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
          <h2 className="mb-4 text-xl font-semibold text-zinc-100">⏰ Trading Schedule</h2>
          
          <div className="space-y-4">
            {/* Enable Toggle */}
            <div className="flex items-center justify-between rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <div>
                <div className="font-medium text-zinc-100">Enable Trading Schedule</div>
                <div className="text-sm text-zinc-400">Only trade during specified time window</div>
              </div>
              <label className="relative inline-flex cursor-pointer items-center">
                <input
                  type="checkbox"
                  checked={scheduleSettings.trading_schedule_enabled}
                  onChange={(e) => setScheduleSettings(prev => ({ ...prev, trading_schedule_enabled: e.target.checked }))}
                  className="peer sr-only"
                />
                <div className="peer h-6 w-11 rounded-full bg-zinc-700 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-zinc-600 after:bg-white after:transition-all after:content-[''] peer-checked:bg-emerald-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-emerald-500"></div>
              </label>
            </div>
            
            {/* Time Window */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Start Time (HH:MM)</label>
                <input
                  type="time"
                  value={scheduleSettings.trading_start_time}
                  onChange={(e) => setScheduleSettings(prev => ({ ...prev, trading_start_time: e.target.value }))}
                  disabled={!scheduleSettings.trading_schedule_enabled}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 disabled:opacity-50"
                />
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">End Time (HH:MM)</label>
                <input
                  type="time"
                  value={scheduleSettings.trading_end_time}
                  onChange={(e) => setScheduleSettings(prev => ({ ...prev, trading_end_time: e.target.value }))}
                  disabled={!scheduleSettings.trading_schedule_enabled}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 disabled:opacity-50"
                />
              </div>
            </div>
            
            {/* Timezone */}
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Timezone</label>
              <select
                value={scheduleSettings.trading_timezone}
                onChange={(e) => setScheduleSettings(prev => ({ ...prev, trading_timezone: e.target.value }))}
                disabled={!scheduleSettings.trading_schedule_enabled}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 disabled:opacity-50"
              >
                <option value="Europe/Istanbul">Europe/Istanbul (UTC+3)</option>
                <option value="Europe/Moscow">Europe/Moscow (UTC+3)</option>
                <option value="Europe/London">Europe/London (UTC+0)</option>
                <option value="Europe/Paris">Europe/Paris (UTC+1)</option>
                <option value="America/New_York">America/New_York (UTC-5)</option>
                <option value="America/Chicago">America/Chicago (UTC-6)</option>
                <option value="America/Los_Angeles">America/Los_Angeles (UTC-8)</option>
                <option value="Asia/Tokyo">Asia/Tokyo (UTC+9)</option>
                <option value="Asia/Shanghai">Asia/Shanghai (UTC+8)</option>
                <option value="Asia/Singapore">Asia/Singapore (UTC+8)</option>
                <option value="UTC">UTC (UTC+0)</option>
              </select>
            </div>
            
            {/* Weekends & Close Before End */}
            <div className="grid grid-cols-2 gap-4">
              <div className="flex items-center gap-3 rounded-lg border border-zinc-700 bg-zinc-800 p-4">
                <input
                  type="checkbox"
                  id="trade_weekends"
                  checked={scheduleSettings.trade_on_weekends}
                  onChange={(e) => setScheduleSettings(prev => ({ ...prev, trade_on_weekends: e.target.checked }))}
                  disabled={!scheduleSettings.trading_schedule_enabled}
                  className="h-4 w-4 rounded border-zinc-600 bg-zinc-700 text-emerald-500 focus:ring-emerald-500 disabled:opacity-50"
                />
                <label htmlFor="trade_weekends" className="flex-1 cursor-pointer">
                  <div className="text-sm font-medium text-zinc-100">Trade on Weekends</div>
                  <div className="text-xs text-zinc-400">Allow Saturday & Sunday trading</div>
                </label>
              </div>
              
              <div>
                <label className="mb-1 block text-sm text-zinc-400">Close Before End (minutes)</label>
                <input
                  type="number"
                  min="0"
                  max="60"
                  value={scheduleSettings.close_before_end_minutes}
                  onChange={(e) => setScheduleSettings(prev => ({ ...prev, close_before_end_minutes: Number(e.target.value) }))}
                  disabled={!scheduleSettings.trading_schedule_enabled}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 disabled:opacity-50"
                />
                <div className="mt-1 text-xs text-zinc-500">Close positions X min before end time</div>
              </div>
            </div>
            
            {/* Info Box */}
            {scheduleSettings.trading_schedule_enabled && (
              <div className="rounded-lg border border-blue-500/30 bg-blue-950/20 p-4">
                <div className="flex items-start gap-2 text-blue-400">
                  <svg className="mt-0.5 h-4 w-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <div className="text-sm">
                    <span className="font-medium">Active Schedule:</span> Trading only between{" "}
                    <span className="font-mono">{scheduleSettings.trading_start_time}</span> and{" "}
                    <span className="font-mono">{scheduleSettings.trading_end_time}</span>{" "}
                    ({scheduleSettings.trading_timezone})
                    {!scheduleSettings.trade_on_weekends && " • Weekends excluded"}
                  </div>
                </div>
              </div>
            )}
            
            {/* Note: Schedule applies to same save button */}
            <div className="rounded-lg bg-zinc-800 p-3 text-sm text-zinc-400">
              💡 Schedule settings are saved together with Trading Parameters using the button above
            </div>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}