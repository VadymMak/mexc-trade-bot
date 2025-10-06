// src/components/settings/ProviderSwitch.tsx
import { memo, useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useProvider } from "@/store/provider";
import type { Provider, Mode } from "@/types"; // ← types live in /types now
import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";

const PROVIDER_LABELS: Record<Provider, string> = {
  gate: "Gate",
  mexc: "MEXC",
  binance: "Binance",
};
const MODE_LABELS: Record<Mode, string> = {
  PAPER: "Paper",
  DEMO: "Demo",
  LIVE: "Live",
};

function ProviderSwitch() {
  const toast = useToast();

  // subscribe narrowly to avoid re-renders
  const active    = useProvider((s) => s.active);
  const mode      = useProvider((s) => s.mode);
  const available = useProvider((s) => s.available);
  const wsEnabled = useProvider((s) => s.wsEnabled);
  const loading   = useProvider((s) => s.loading);
  const error     = useProvider((s) => s.error);
  const load      = useProvider((s) => s.load);
  const switchTo  = useProvider((s) => s.switchTo);

  // Local controlled state to avoid “jumping” while user picks values
  const [provSel, setProvSel] = useState<Provider | "">("");
  const [modeSel, setModeSel] = useState<Mode | "">("");

  // One-time provider load on mount if store is empty
  const didInit = useRef(false);
  useEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    if (!active || !mode) {
      void load().catch(() => {
        const msg = getErrorMessage(useProvider.getState().error);
        if (msg) toast.error(msg, "Provider");
      });
    }
  }, [active, mode, load, toast]);

  // Keep selects in sync with store (also handles backend switching underneath)
  const lastSynced = useRef<{ active?: Provider | null; mode?: Mode | null }>({});
  useEffect(() => {
    if (active && mode) {
      const changed =
        lastSynced.current.active !== active || lastSynced.current.mode !== mode;
      if (changed) {
        setProvSel(active);
        setModeSel(mode);
        lastSynced.current = { active, mode };
      }
    }
  }, [active, mode]);

  // Ensure dropdown includes active provider, even if backend returns a minimal list
  const providers = useMemo<Provider[]>(() => {
    const base = (available?.length ? available : ["gate", "mexc", "binance"]) as Provider[];
    return active && !base.includes(active) ? [...base, active] : base;
  }, [available, active]);

  const unchanged = useMemo(
    () => !!active && !!mode && provSel === active && modeSel === mode,
    [active, mode, provSel, modeSel]
  );

  const onApply = useCallback(async (): Promise<void> => {
    try {
      if (!provSel || !modeSel) {
        toast.info("Choose provider and mode");
        return;
      }
      if (unchanged) {
        toast.info("Already using this provider & mode");
        return;
      }
      await switchTo(provSel as Provider, modeSel as Mode);
      toast.success(
        `Switched to ${PROVIDER_LABELS[provSel as Provider]} • ${MODE_LABELS[modeSel as Mode]}`
      );
    } catch (e) {
      toast.error(getErrorMessage(e), "HTTP Error");
    }
  }, [provSel, modeSel, unchanged, switchTo, toast]);

  return (
    <div className="flex items-center gap-3">
      <label htmlFor="provider-select" className="text-sm text-zinc-400">
        Exchange
      </label>
      <select
        id="provider-select"
        className="rounded-xl bg-zinc-800 text-zinc-100 px-3 py-2 outline-none disabled:opacity-60"
        value={provSel}
        onChange={(e) => setProvSel(e.target.value as Provider | "")}
        disabled={loading}
        aria-label="Select exchange"
      >
        <option value="" disabled>
          Select…
        </option>
        {providers.map((p) => (
          <option key={p} value={p}>
            {PROVIDER_LABELS[p]}
          </option>
        ))}
      </select>

      <label htmlFor="mode-select" className="ml-4 text-sm text-zinc-400">
        Mode
      </label>
      <select
        id="mode-select"
        className="rounded-xl bg-zinc-800 text-zinc-100 px-3 py-2 outline-none disabled:opacity-60"
        value={modeSel}
        onChange={(e) => setModeSel(e.target.value as Mode | "")}
        disabled={loading}
        aria-label="Select mode"
        onKeyDown={(e) => { if (e.key === "Enter") void onApply(); }}
      >
        <option value="" disabled>
          Select…
        </option>
        {(["PAPER", "DEMO", "LIVE"] as Mode[]).map((m) => (
          <option key={m} value={m}>
            {MODE_LABELS[m]}
          </option>
        ))}
      </select>

      <button
        onClick={onApply}
        className="ml-4 inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-60"
        disabled={loading || !provSel || !modeSel || unchanged}
        aria-busy={loading}
      >
        {loading ? (
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
        ) : null}
        Apply
      </button>

      <div className="ml-4 text-sm text-zinc-400">WS: {wsEnabled ? "on" : "off"}</div>

      {error ? (
        <div className="ml-3 text-sm text-rose-400" aria-live="polite">
          {getErrorMessage(error)}
        </div>
      ) : null}
    </div>
  );
}

export default memo(ProviderSwitch);
