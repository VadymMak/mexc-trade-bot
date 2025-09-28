import { useRef } from "react";
import { useSymbols } from "@/store/symbols";
import { useStrategy } from "@/store/strategy";
import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";
import ProviderSwitch from "@/components/settings/ProviderSwitch";
import { useProvider } from "@/store/provider";

export default function TopBar() {
  const inputRef = useRef<HTMLInputElement>(null);

  // symbols
  const items = useSymbols((s) => s.items);
  const add = useSymbols((s) => s.add);

  // strategy
  const busy = useStrategy((s) => s.busy);
  const start = useStrategy((s) => s.start);
  const stopAll = useStrategy((s) => s.stopAll);

  // provider meta (for small status text)
  const provider = useProvider((s) => s.active);
  const mode = useProvider((s) => s.mode);
  const wsEnabled = useProvider((s) => s.wsEnabled);

  const toast = useToast();

  const onAdd = () => {
    const v = inputRef.current?.value ?? "";
    const sym = v.trim().toUpperCase();
    if (!sym) return;
    add(sym);
    toast.success(`${sym} added`);
    if (inputRef.current) inputRef.current.value = "";
  };

  const startAll = async () => {
    try {
      const syms = items.map((i) => i.symbol);
      if (!syms.length) return;
      await start(syms);
      toast.success(`Started ${syms.length} symbols`);
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), "Error");
    }
  };

  const stopAllApi = async () => {
    try {
      await stopAll(false);
      toast.info("Stopped all (no flatten)");
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), "Error");
    }
  };

  const flattenAllApi = async () => {
    try {
      await stopAll(true);
      toast.info("Flattened & stopped all");
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), "Error");
    }
  };

  const runningCount = items.filter((i) => i.running).length;

  return (
    <div className="sticky top-0 z-10 border-b border-zinc-800/80 bg-zinc-900/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center gap-2 p-3">

        {/* Add symbol */}
        <input
          ref={inputRef}
          placeholder="Add symbol (e.g. BTCUSDT)"
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

        {/* Strategy controls */}
        <button
          onClick={startAll}
          className="rounded-xl bg-zinc-800 px-3 py-2 text-sm hover:bg-zinc-700 disabled:opacity-60"
          disabled={busy}
        >
          Start All
        </button>
        <button
          onClick={stopAllApi}
          className="rounded-xl bg-zinc-800 px-3 py-2 text-sm hover:bg-zinc-700 disabled:opacity-60"
          disabled={busy}
        >
          Stop All
        </button>
        <button
          onClick={flattenAllApi}
          className="rounded-xl bg-zinc-800 px-3 py-2 text-sm hover:bg-zinc-700 disabled:opacity-60"
          disabled={busy}
        >
          Flatten All
        </button>

        {/* Right side: provider switch + status */}
        <div className="ml-auto flex items-center gap-3">
          <div className="text-sm text-zinc-400">
            Running: {runningCount}
          </div>

          <div className="mx-1 h-6 w-px bg-zinc-700/60" />

          {/* Provider dropdown+mode toggle */}
          <ProviderSwitch />

          {/* tiny status */}
          <div className="text-xs text-zinc-500">
            {provider?.toUpperCase()} / {mode ?? "—"} {wsEnabled ? "• WS" : "• REST"}
          </div>
        </div>
      </div>
    </div>
  );
}
