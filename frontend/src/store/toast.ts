import { create } from "zustand";

export type ToastKind = "success" | "error" | "info";
export type Toast = {
  id: string;
  kind: ToastKind;
  title?: string;
  message: string;
  timeoutMs?: number; // авто-закрытие
};

type ToastState = {
  toasts: Toast[];
  add: (t: Omit<Toast, "id">) => string;
  remove: (id: string) => void;
  clear: () => void;
};

function uid() {
  return Math.random().toString(36).slice(2, 9);
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  add: (t) => {
    const id = uid();
    const toast: Toast = { timeoutMs: 3500, ...t, id };
    set((s) => ({ toasts: [...s.toasts, toast] }));
    if (toast.timeoutMs && toast.timeoutMs > 0) {
      setTimeout(() => {
        const exists = get().toasts.find((x) => x.id === id);
        if (exists) get().remove(id);
      }, toast.timeoutMs);
    }
    return id;
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  clear: () => set({ toasts: [] }),
}));
