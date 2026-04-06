import { create } from "zustand";
import { getProviderConfig, switchProviderConfig } from "@/api/api";
import type { Provider, Mode, ProviderState } from "@/types";

/* ───────── State shape ───────── */
interface ProviderStateShape {
  active: Provider | null;
  mode: Mode | null;
  wsEnabled: boolean;
  available: Provider[];
  revision: number;
  loading: boolean;
  error: Error | null;
}

/* ───────── Actions ───────── */
interface ProviderActions {
  load: () => Promise<void>;
  switchTo: (provider: Provider, mode: Mode) => Promise<void>;
}

/* ───────── Store type ───────── */
export type ProviderStore = ProviderStateShape & ProviderActions;

/* ───────── Zustand store ───────── */
export const useProvider = create<ProviderStore>((set, get) => ({
  active: null,
  mode: null,
  wsEnabled: false,
  available: ["gate", "mexc", "binance"],
  revision: 0,
  loading: false,
  error: null,

  async load() {
    if (get().loading) return;
    set({ loading: true, error: null });

    try {
      const res: ProviderState = await getProviderConfig();

      set({
        active: res.active,
        mode: res.mode,
        available: res.available ?? ["gate", "mexc", "binance"],
        wsEnabled: Boolean(res.ws_enabled),
        revision: res.revision ?? 0,
        loading: false,
        error: null,
      });
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      set({ loading: false, error });
      throw error;
    }
  },

  async switchTo(provider, mode) {
    const prov = provider?.toLowerCase() as Provider;
    const m = mode?.toUpperCase() as Mode;

    if (!prov || !m) {
      const err = new Error("Provider and mode are required");
      set({ error: err });
      throw err;
    }

    const cur = get();
    if (cur.active === prov && cur.mode === m) return;

    set({ loading: true, error: null });

    try {
      const res: ProviderState = await switchProviderConfig(prov, m);

      set({
        active: res.active,
        mode: res.mode,
        available: res.available ?? cur.available,
        wsEnabled: Boolean(res.ws_enabled),
        revision: res.revision ?? cur.revision + 1,
        loading: false,
        error: null,
      });
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      set({ loading: false, error });
      throw error;
    }
  },
}));
