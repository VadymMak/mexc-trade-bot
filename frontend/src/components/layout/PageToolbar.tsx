// src/components/layout/PageToolbar.tsx
import React from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useStrategy } from "@/store/strategy";
import { useToastStore } from "@/store/toast";

export default function PageToolbar() {
  const { stopAll, busy } = useStrategy();
  const addToast = useToastStore((s) => s.add);
  const location = useLocation();

  const linkStyle = ({ isActive }: { isActive: boolean }) =>
    `rounded-xl px-3 py-2 text-sm ${
      isActive ? "bg-zinc-800 text-zinc-100" : "text-zinc-300 hover:bg-zinc-800/70"
    }`;

  const handleStopAll = async () => {
    if (busy) return;
    try {
      await stopAll(false);
      addToast({ kind: "info", title: "Strategy", message: "All symbols stopped" });
    } catch {
      addToast({ kind: "error", title: "Strategy", message: "Failed to stop all symbols" });
    }
  };

  const handleFlattenAll = async () => {
    if (busy) return;
    try {
      await stopAll(true);
      addToast({ kind: "success", title: "Strategy", message: "All positions flattened" });
    } catch {
      addToast({ kind: "error", title: "Strategy", message: "Failed to flatten all" });
    }
  };

  return (
    <div className="flex items-center gap-2">
      <NavLink to="/trade" className={linkStyle}>
        Trading
      </NavLink>
      <NavLink to="/dashboard" className={linkStyle}>
        Dashboard
      </NavLink>
      <NavLink to="/scanner" className={linkStyle}>
        Scanner
      </NavLink>

      {location.pathname.startsWith("/trade") && (
        <div className="ml-4 flex items-center gap-2">
          <button
            onClick={handleStopAll}
            title="Stop all running symbols (no flatten)"
            disabled={busy}
            className={`rounded-lg px-3 py-1.5 text-xs ${
              busy
                ? "bg-zinc-700 text-zinc-300 opacity-60 cursor-not-allowed"
                : "bg-zinc-700 hover:bg-zinc-600 text-zinc-100"
            }`}
          >
            ⏹ Stop All
          </button>
          <button
            onClick={handleFlattenAll}
            title="Flatten all positions and stop strategy"
            disabled={busy}
            className={`rounded-lg px-3 py-1.5 text-xs ${
              busy
                ? "bg-rose-600 text-white opacity-60 cursor-not-allowed"
                : "bg-rose-600 hover:bg-rose-500 text-white"
            }`}
          >
            🧹 Flatten All
          </button>
        </div>
      )}
    </div>
  );
}
