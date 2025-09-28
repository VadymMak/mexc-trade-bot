// src/components/controls/FlattenButton.tsx
import { useState, useId } from "react";
import cx from "classnames";

type Size = "sm" | "md";

export type FlattenButtonProps = {
  symbol: string;
  onConfirm: (symbol: string) => void | Promise<void>;
  disabled?: boolean;
  label?: string;
  size?: Size;
};

export default function FlattenButton({
  symbol,
  onConfirm,
  disabled = false,
  label = "Flatten",
  size = "md",
}: FlattenButtonProps) {
  const [open, setOpen] = useState(false);
  const id = useId();

  const sz = size === "sm" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm";

  return (
    <div className="inline-flex items-center gap-2">
      <button
        aria-describedby={id}
        onClick={() => setOpen(true)}
        disabled={disabled}
        className={cx(
          "rounded-xl border border-neutral-700 hover:bg-neutral-800 transition text-neutral-200",
          disabled ? "opacity-50 cursor-not-allowed" : "",
          sz
        )}
      >
        {label}
      </button>

      {/* minimal inline confirm */}
      {open && (
        <div
          id={id}
          className="inline-flex items-center gap-2 bg-neutral-900/80 border border-neutral-700 rounded-xl px-2 py-1"
        >
          <span className="text-xs text-neutral-300">Confirm {label} {symbol}?</span>
          <button
            onClick={() => { setOpen(false); void onConfirm(symbol); }}
            className="px-2 py-0.5 rounded-lg bg-red-600/80 hover:bg-red-600 text-white text-xs"
          >
            Yes
          </button>
          <button
            onClick={() => setOpen(false)}
            className="px-2 py-0.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-neutral-200 text-xs"
          >
            No
          </button>
        </div>
      )}
    </div>
  );
}
