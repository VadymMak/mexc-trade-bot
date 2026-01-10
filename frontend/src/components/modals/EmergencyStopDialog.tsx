// src/components/modals/EmergencyStopDialog.tsx
import { useEffect, useState } from "react";
import cx from "classnames";

export type EmergencyStopDialogProps = {
  open: boolean;
  positionsCount: number;
  onConfirm: () => void;
  onCancel: () => void;
};

export default function EmergencyStopDialog({
  open,
  positionsCount,
  onConfirm,
  onCancel,
}: EmergencyStopDialogProps) {
  const [mounted, setMounted] = useState(false);
  
  useEffect(() => {
    if (open) {
      setMounted(true);
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
      const t = setTimeout(() => setMounted(false), 150);
      return () => clearTimeout(t);
    }
  }, [open]);

  if (!open && !mounted) return null;

  return (
    <div
      className={cx(
        "fixed inset-0 z-[100] flex items-center justify-center",
        "bg-black/70 backdrop-blur-sm",
        open ? "opacity-100" : "opacity-0",
        "transition-opacity duration-150"
      )}
      onClick={onCancel}
    >
      <div
        className={cx(
          "w-[520px] max-w-[95vw]",
          "rounded-2xl border border-rose-500/50 bg-neutral-900 shadow-2xl",
          "transition-transform duration-150",
          open ? "scale-100" : "scale-95"
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-rose-500/30 bg-rose-950/20">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-rose-500/20">
              <svg 
                className="h-6 w-6 text-rose-400" 
                fill="none" 
                viewBox="0 0 24 24" 
                stroke="currentColor"
              >
                <path 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  strokeWidth={2} 
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" 
                />
              </svg>
            </div>
            <div>
              <h3 className="text-lg font-semibold text-rose-300">
                🚨 EMERGENCY STOP
              </h3>
              <p className="text-sm text-rose-400/80">
                This will stop ALL strategies immediately
              </p>
            </div>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          <p className="text-neutral-200">
            You are about to <span className="font-semibold text-rose-300">STOP ALL strategies</span> and <span className="font-semibold text-rose-300">CLOSE ALL positions</span>.
          </p>
          
          {positionsCount > 0 && (
            <div className="rounded-lg bg-rose-950/30 border border-rose-500/30 p-4">
              <div className="mb-2 text-sm font-medium text-rose-300">
                ⚠️ This will:
              </div>
              <ul className="space-y-2 text-sm text-rose-200/90">
                <li className="flex items-start gap-2">
                  <span className="text-rose-400">•</span>
                  <span>Stop all running strategies immediately</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400">•</span>
                  <span>Close <strong>{positionsCount} open position{positionsCount !== 1 ? 's' : ''}</strong> at market price</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400">•</span>
                  <span>All market orders will be executed immediately</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400">•</span>
                  <span>This action cannot be undone</span>
                </li>
              </ul>
            </div>
          )}

          {positionsCount === 0 && (
            <div className="rounded-lg bg-neutral-800/50 border border-neutral-700 p-3">
              <p className="text-sm text-neutral-300">
                ✓ No open positions. This will only stop running strategies.
              </p>
            </div>
          )}

          <div className="rounded-lg bg-amber-950/30 border border-amber-500/30 p-3">
            <p className="text-xs text-amber-300">
              💡 <span className="font-medium">Use this button when:</span> You need to stop everything immediately in emergency situations or when closing for the day.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-neutral-800 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm font-medium rounded-lg border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-rose-600 hover:bg-rose-500 text-white transition-colors flex items-center gap-2"
          >
            <svg 
              className="h-4 w-4" 
              fill="none" 
              viewBox="0 0 24 24" 
              stroke="currentColor"
            >
              <path 
                strokeLinecap="round" 
                strokeLinejoin="round" 
                strokeWidth={2} 
                d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" 
              />
              <path 
                strokeLinecap="round" 
                strokeLinejoin="round" 
                strokeWidth={2} 
                d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" 
              />
            </svg>
            Yes, STOP ALL & FLATTEN
          </button>
        </div>
      </div>
    </div>
  );
}