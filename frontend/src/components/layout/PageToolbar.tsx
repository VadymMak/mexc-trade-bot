// src/components/layout/PageToolbar.tsx
import React from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useStrategy } from "@/store/strategy";
import { useToastStore } from "@/store/toast";

export default function PageToolbar() {
  const { stopAll } = useStrategy();
  const addToast = useToastStore((s) => s.add);
  const location = useLocation();

  const linkStyle = ({ isActive }: { isActive: boolean }) =>
    `rounded-xl px-3 py-2 text-sm ${
      isActive
        ? "bg-zinc-800 text-zinc-100"
        : "text-zinc-300 hover:bg-zinc-800/70"
    }`;

  // Handlers
  const handleStopAll = async () => {
    try {
      await stopAll(false);
      addToast({
        kind: "info",
        title: "Strategy",
        message: "All symbols stopped",
      });
    } catch {
      addToast({
        kind: "error",
        title: "Strategy",
        message: "Failed to stop all symbols",
      });
    }
  };

  const handleFlattenAll = async () => {
    try {
      await stopAll(true);
      addToast({
        kind: "success",
        title: "Strategy",
        message: "All positions flattened",
      });
    } catch {
      addToast({
        kind: "error",
        title: "Strategy",
        message: "Failed to flatten all",
      });
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

      {/* Global controls visible only on Trading page */}
      {location.pathname.startsWith("/trade") && (
        <div className="ml-4 flex items-center gap-2">
          <button
            onClick={handleStopAll}
            className="rounded-lg bg-zinc-700 hover:bg-zinc-600 px-3 py-1.5 text-xs text-zinc-100"
          >
            ⏹ Stop All
          </button>
          <button
            onClick={handleFlattenAll}
            className="rounded-lg bg-rose-600 hover:bg-rose-500 px-3 py-1.5 text-xs text-white"
          >
            🧹 Flatten All
          </button>
        </div>
      )}
    </div>
  );
}
