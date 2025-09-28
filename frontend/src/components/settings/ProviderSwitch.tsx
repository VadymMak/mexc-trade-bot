// src/components/settings/ProviderSwitch.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useProvider } from "@/store/provider";
import type { Provider, Mode } from "@/api/api";
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

export default function ProviderSwitch() {
  const toast = useToast();

  // ✅ subscribe to each field separately (prevents infinite re-renders)
  const active     = useProvider((s) => s.active);
  const mode       = useProvider((s) => s.mode);
  const available  = useProvider((s) => s.available);
  const wsEnabled  = useProvider((s) => s.wsEnabled);
  const loading    = useProvider((s) => s.loading);
  const error      = useProvider((s) => s.error);
  const load       = useProvider((s) => s.load);
  const switchTo   = useProvider((s) => s.switchTo);

  // Local controlled selects
  const [provSel, setProvSel] = useState<Provider | "">("");
  const [modeSel, setModeSel] = useState<Mode | "">("");

  // Load once on first mount
  const didInit = useRef(false);
  useEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    if (active == null || mode == null) {
      void load();
    }
  }, [active, mode, load]);

  // When store gets first real values, sync local selects (one-shot)
  const synced = useRef(false);
  useEffect(() => {
    if (synced.current) return;
    if (active && mode) {
      setProvSel(active);
      setModeSel(mode);
      synced.current = true;
    }
  }, [active, mode]);

  const providers = useMemo<Provider[]>(
    () => (available?.length ? available : ["gate", "mexc", "binance"]),
    [available]
  );

  const onApply = async () => {
    try {
      if (!provSel || !modeSel) {
        toast.info("Choose provider and mode");
        return;
      }
      await switchTo(provSel as Provider, modeSel as Mode);
      toast.success(
        `Switched to ${PROVIDER_LABELS[provSel as Provider]} • ${MODE_LABELS[modeSel as Mode]}`
      );
    } catch (e) {
      toast.error(getErrorMessage(e), "HTTP Error");
    }
  };

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-zinc-400">Exchange</label>
      <select
        className="rounded-xl bg-zinc-800 text-zinc-100 px-3 py-2 outline-none"
        value={provSel}
        onChange={(e) => setProvSel(e.target.value as Provider | "")}
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

      <label className="ml-4 text-sm text-zinc-400">Mode</label>
      <select
        className="rounded-xl bg-zinc-800 text-zinc-100 px-3 py-2 outline-none"
        value={modeSel}
        onChange={(e) => setModeSel(e.target.value as Mode | "")}
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
        className="ml-4 rounded-xl bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-60"
        disabled={loading || !provSel || !modeSel}
      >
        Apply
      </button>

      <div className="ml-4 text-sm text-zinc-400">WS: {wsEnabled ? "on" : "off"}</div>

      {error ? (
        <div className="ml-3 text-sm text-rose-400">{getErrorMessage(error)}</div>
      ) : null}
    </div>
  );
}
