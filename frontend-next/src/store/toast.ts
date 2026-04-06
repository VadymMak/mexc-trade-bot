// src/store/toast.ts
import { create } from "zustand";

export type ToastKind = "success" | "error" | "info";

export type Toast = {
  id: string;
  kind: ToastKind;
  message: string;
  title?: string;
  /** Auto-close timeout (ms). If omitted or 0, toast stays until removed. */
  timeoutMs?: number;
};

type ToastState = {
  toasts: Toast[];
  /** Add a toast; returns its id */
  add: (t: Omit<Toast, "id">) => string;
  /** Remove a toast by id */
  remove: (id: string) => void;
  /** Clear all toasts */
  clear: () => void;
};

function uid(): string {
  return Math.random().toString(36).slice(2, 9);
}

type Timer = ReturnType<typeof setTimeout>;
const timers = new Map<string, Timer>();

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],

  add: (t) => {
    const id = uid();
    const toast: Toast = { timeoutMs: 3500, ...t, id };

    set((s) => ({ toasts: [...s.toasts, toast] }));

    if (toast.timeoutMs && toast.timeoutMs > 0) {
      const handle = setTimeout(() => {
        // Avoid double-remove if it was already cleared manually.
        timers.delete(id);
        const exists = get().toasts.some((x) => x.id === id);
        if (exists) get().remove(id);
      }, toast.timeoutMs);
      timers.set(id, handle);
    }

    return id;
  },

  remove: (id) => {
    // Clear any pending timer for this toast
    const handle = timers.get(id);
    if (handle) {
      clearTimeout(handle);
      timers.delete(id);
    }
    set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
  },

  clear: () => {
    // Clear all timers to avoid leaks
    for (const handle of timers.values()) clearTimeout(handle);
    timers.clear();
    set({ toasts: [] });
  },
}));
