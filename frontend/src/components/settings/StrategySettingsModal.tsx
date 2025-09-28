import { useEffect, useState, useCallback } from "react";
import { getStrategyParams, setStrategyParams, type StrategyParams } from "@/api/api";
import { useToast } from "@/hooks/useToast";

type Props = {
  symbol: string;
  open: boolean;
  onClose: () => void;
};

export default function StrategySettingsModal({ symbol, open, onClose }: Props) {
  const toast = useToast();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [p, setP] = useState<StrategyParams | null>(null);

  // Clear local cache when fully closed so next open refetches
  useEffect(() => {
    if (!open) setP(null);
  }, [open]);

  // Fetch params on open (only once per open)
  useEffect(() => {
    if (!open) return;
    if (p !== null) return;

    const ac = new AbortController();
    let alive = true;

    setLoading(true);
    getStrategyParams()
      .then((params) => {
        if (!alive || ac.signal.aborted) return;
        setP(params);
      })
      .catch(() => {
        if (!alive || ac.signal.aborted) return;
        toast.error("Не удалось загрузить параметры");
      })
      .finally(() => {
        if (!alive || ac.signal.aborted) return;
        setLoading(false);
      });

    return () => {
      alive = false;
      ac.abort();
    };
  }, [open, p, toast]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const upd = useCallback(
    <K extends keyof StrategyParams>(k: K, v: StrategyParams[K]) => {
      setP((cur) => (cur ? { ...cur, [k]: v } : cur));
    },
    []
  );

  const onSave = useCallback(async () => {
    if (!p) return;
    setSaving(true);
    try {
      // setStrategyParams accepts Partial<StrategyParams>, sending full object is fine
      await setStrategyParams(p);
      toast.success("Параметры сохранены");
      onClose();
    } catch {
      toast.error("Ошибка сохранения параметров");
    } finally {
      setSaving(false);
    }
  }, [p, onClose, toast]);

  if (!open) return null;

  const disabled = loading || saving || !p;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label="Strategy Settings"
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      <div
        className="relative w-[560px] max-w-[92vw] rounded-2xl border border-zinc-700 bg-zinc-900 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <div className="text-lg font-semibold">Strategy Settings</div>
          <button
            onClick={onClose}
            className="rounded bg-zinc-800 px-2 py-1 hover:bg-zinc-700"
            type="button"
            title="Close"
          >
            ×
          </button>
        </div>

        <div className="mb-3 text-xs text-zinc-400">
          Symbol: <span className="font-mono">{symbol}</span>
        </div>

        {loading || !p ? (
          <div className="text-sm text-zinc-300">Загрузка…</div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {/* INPUT FILTERS */}
            <FieldNumber
              label="Min spread (bps)"
              value={p.min_spread_bps}
              onChange={(v) => upd("min_spread_bps", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Edge floor (bps)"
              value={p.edge_floor_bps}
              onChange={(v) => upd("edge_floor_bps", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Imbalance min"
              value={p.imbalance_min}
              step={0.01}
              onChange={(v) => upd("imbalance_min", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Imbalance max"
              value={p.imbalance_max}
              step={0.01}
              onChange={(v) => upd("imbalance_max", v)}
              disabled={disabled}
            />
            <FieldCheckbox
              label="Depth check"
              checked={p.enable_depth_check}
              onChange={(v) => upd("enable_depth_check", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Absorption X (bps)"
              value={p.absorption_x_bps}
              onChange={(v) => upd("absorption_x_bps", v)}
              disabled={disabled}
            />

            {/* SIZING / TIMING */}
            <FieldNumber
              label="Order size (USD)"
              value={p.order_size_usd}
              onChange={(v) => upd("order_size_usd", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Timeout exit (sec)"
              value={p.timeout_exit_sec}
              onChange={(v) => upd("timeout_exit_sec", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Max concurrent"
              value={p.max_concurrent_symbols}
              onChange={(v) => upd("max_concurrent_symbols", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Min hold (ms)"
              value={p.min_hold_ms}
              onChange={(v) => upd("min_hold_ms", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Reenter cooldown (ms)"
              value={p.reenter_cooldown_ms}
              onChange={(v) => upd("reenter_cooldown_ms", v)}
              disabled={disabled}
            />

            {/* TRADE MGMT */}
            <FieldNumber
              label="Take profit (bps)"
              value={p.take_profit_bps}
              onChange={(v) => upd("take_profit_bps", v)}
              disabled={disabled}
            />
            <FieldNumber
              label="Stop loss (bps)"
              value={p.stop_loss_bps}
              onChange={(v) => upd("stop_loss_bps", v)}
              disabled={disabled}
            />

            {/* DEMO */}
            <FieldCheckbox
              label="Debug force entry"
              checked={p.debug_force_entry}
              onChange={(v) => upd("debug_force_entry", v)}
              disabled={disabled}
            />
          </div>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="h-9 rounded-lg bg-zinc-700 px-3 hover:bg-zinc-600"
            type="button"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            className="h-9 rounded-lg bg-emerald-600 px-3 hover:bg-emerald-500 disabled:opacity-60"
            type="button"
            disabled={disabled}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FieldNumber({
  label,
  value,
  onChange,
  step,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-zinc-400">{label}</span>
      <input
        type="number"
        step={step ?? 1}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(Number.parseFloat(e.target.value || "0"))}
        disabled={disabled}
        className="rounded-md border border-zinc-700 bg-zinc-900/60 px-2 py-1.5 text-sm outline-none disabled:opacity-60"
      />
    </label>
  );
}

function FieldCheckbox({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center gap-2">
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="h-4 w-4 accent-emerald-600 disabled:opacity-60"
      />
      <span className="text-sm text-zinc-300">{label}</span>
    </label>
  );
}
