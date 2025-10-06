import { useRef, useCallback, useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import ProviderSwitch from "@/components/settings/ProviderSwitch";
import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";
import { useSymbols } from "@/store/symbols";
import { useStrategy } from "@/store/strategy";
import { useProvider } from "@/store/provider";
import { parseSymbolsInput } from "@/utils/format";
import { setWatchlist } from "@/api/api";

export default function TopBar() {
  const inputRef = useRef<HTMLInputElement>(null);

  // symbols
  const items = useSymbols((s) => s.items);
  const addSymbols = useSymbols((s) => s.addSymbols);

  // strategy
  const busy = useStrategy((s) => s.busy);
  const start = useStrategy((s) => s.start);
  const stopAll = useStrategy((s) => s.stopAll);

  // provider
  const loadProvider = useProvider((s) => s.load);
  const provider = useProvider((s) => s.active);
  const mode = useProvider((s) => s.mode);
  const wsEnabled = useProvider((s) => s.wsEnabled);
  const loadingProvider = useProvider((s) => s.loading);
  const providerError = useProvider((s) => s.error);

  const toast = useToast();

  // --- hydration flag (so we don't sync empty pre-hydration state) ---
  const [hydrated, setHydrated] = useState(
    useSymbols.persist?.hasHydrated?.() ?? false
  );
  useEffect(() => {
    const off = useSymbols.persist?.onFinishHydration?.(() => setHydrated(true));
    return () => off?.();
  }, []);

  // hydrate provider config on mount
  useEffect(() => {
    loadProvider().catch(() => {
      const msg = getErrorMessage(useProvider.getState().error);
      if (msg) toast.error(msg, "Provider");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- keep backend watchlist in sync ---
  const syncWatchlist = useCallback(async () => {
    try {
      const syms = useSymbols.getState().items.map((i) => i.symbol);
      await setWatchlist(syms);
    } catch (e) {
      toast.error(getErrorMessage(e), "Watchlist");
    }
  }, [toast]);

  // Sync after any item change (post-hydration). This covers adds/removes from anywhere in the UI.
  const symbolsSig = useMemo(
    () => items.map((i) => i.symbol).slice().sort().join(","),
    [items]
  );
  useEffect(() => {
    if (!hydrated) return;
    void syncWatchlist();
  }, [hydrated, symbolsSig, syncWatchlist]);

  const onAdd = useCallback((): void => {
    const raw = inputRef.current?.value ?? "";
    const trimmed = raw.trim();
    if (!trimmed) return;

    const { good, bad } = parseSymbolsInput(trimmed);

    if (good.length > 0) {
      addSymbols(good);
      // sync will run via the items effect above; no need to await here
      toast.success(
        good.length === 1 ? `${good[0]} added` : `Added ${good.length} symbols`
      );
    }

    if (bad.length > 0) {
      const preview = bad.slice(0, 5).join(", ");
      toast.error(
        bad.length === 1
          ? `Ignored invalid token: ${bad[0]}`
          : `Ignored ${bad.length} invalid tokens${
              bad.length > 5 ? ` (e.g. ${preview}…)` : `: ${preview}`
            }`,
        "Validation"
      );
    }

    if (inputRef.current) inputRef.current.value = "";
  }, [addSymbols, toast]);

  const startAllHandler = async (): Promise<void> => {
    try {
      const syms = items.map((i) => i.symbol);
      if (!syms.length) return;
      await start(syms);
      toast.success(`Started ${syms.length} symbols`);
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), "Error");
    }
  };

  const stopAllApi = async (): Promise<void> => {
    try {
      await stopAll(false);
      toast.info("Stopped all (no flatten)");
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), "Error");
    }
  };

  const flattenAllApi = async (): Promise<void> => {
    try {
      await stopAll(true);
      toast.info("Flattened & stopped all");
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), "Error");
    }
  };

  const runningCount = items.filter((i) => i.running).length;

  // Controls are enabled only when provider is ready & active
  const providerReady = !!provider && !loadingProvider && !providerError;
  const controlsDisabled = busy || !providerReady;

  const providerChip = useMemo(() => {
    if (!providerReady) {
      return { text: "• OFFLINE", cls: "text-zinc-400" };
    }
    if (wsEnabled) {
      return { text: "• WS", cls: "text-emerald-400" };
    }
    return { text: "• REST", cls: "text-amber-400" };
  }, [providerReady, wsEnabled]);

  return (
    <div className="sticky top-0 z-10 border-b border-zinc-800/80 bg-zinc-900/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center gap-2 p-3">
        {/* left: nav */}
        <nav className="flex items-center gap-2 pr-2">
          <NavLink
            to="/"
            className={({ isActive }) =>
              `rounded-xl px-3 py-2 text-sm ${
                isActive ? "bg-zinc-800 text-zinc-100" : "text-zinc-300 hover:bg-zinc-800/70"
              }`
            }
            end
          >
            Dashboard
          </NavLink>

          <NavLink
            to="/scanner"
            className={({ isActive }) =>
              `rounded-xl px-3 py-2 text-sm ${
                isActive ? "bg-zinc-800 text-zinc-100" : "text-zinc-300 hover:bg-zinc-800/70"
              }`
            }
          >
            Scanner
          </NavLink>

          <NavLink
            to="/trade"
            className={({ isActive }) =>
              `rounded-xl px-3 py-2 text-sm ${
                isActive ? "bg-zinc-800 text-zinc-100" : "text-zinc-300 hover:bg-zinc-800/70"
              }`
            }
          >
            Trade
          </NavLink>
        </nav>

        <div className="mx-1 h-6 w-px bg-zinc-700/60" />

        {/* Add symbols (supports multi-input) */}
        <input
          ref={inputRef}
          placeholder="Add symbols (e.g. BTCUSDT ETHUSDT)"
          className="w-64 rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm outline-none"
          onKeyDown={(e) => e.key === "Enter" && onAdd()}
        />
        <button
          onClick={onAdd}
          className="rounded-xl bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500"
        >
          + Add
        </button>

        <div className="mx-3 h-6 w-px bg-zinc-700/60" />

        {/* Strategy controls (gated by provider readiness) */}
        <button
          onClick={startAllHandler}
          className="rounded-xl bg-zinc-800 px-3 py-2 text-sm hover:bg-zinc-700 disabled:opacity-60"
          disabled={controlsDisabled}
          title={providerReady ? "" : "Provider not ready"}
        >
          Start All
        </button>
        <button
          onClick={stopAllApi}
          className="rounded-xl bg-zinc-800 px-3 py-2 text-sm hover:bg-zinc-700 disabled:opacity-60"
          disabled={controlsDisabled}
          title={providerReady ? "" : "Provider not ready"}
        >
          Stop All
        </button>
        <button
          onClick={flattenAllApi}
          className="rounded-xl bg-zinc-800 px-3 py-2 text-sm hover:bg-zinc-700 disabled:opacity-60"
          disabled={controlsDisabled}
          title={providerReady ? "" : "Provider not ready"}
        >
          Flatten All
        </button>

        {/* Right side: provider switch + status */}
        <div className="ml-auto flex items-center gap-3">
          <div className="text-sm text-zinc-400">Running: {runningCount}</div>
          <div className="mx-1 h-6 w-px bg-zinc-700/60" />
          <ProviderSwitch />
          <div className="text-xs text-zinc-500">
            {provider?.toUpperCase() || "—"} / {mode ?? "—"}{" "}
            <span className={providerChip.cls}>{providerChip.text}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
