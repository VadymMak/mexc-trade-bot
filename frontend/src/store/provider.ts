// src/store/provider.ts
import { create } from "zustand";
import {
  getProviderConfig,
  switchProviderConfig,
  type Provider,
  type Mode,
} from "@/api/api";

type ProviderStateShape = {
  active: Provider | null;
  mode: Mode | null;
  wsEnabled: boolean;
  available: Provider[];
  revision: number;
  loading: boolean;
  error: unknown | null;
};

type ProviderActions = {
  load: () => Promise<void>;
  switchTo: (provider: Provider, mode: Mode) => Promise<void>;
};

export type ProviderStore = ProviderStateShape & ProviderActions;

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
      const res = await getProviderConfig();
      set({
        active: res.active,
        mode: res.mode,
        available: res.available ?? ["gate", "mexc", "binance"],
        wsEnabled: !!res.ws_enabled,
        revision: res.revision ?? 0,
        loading: false,
        error: null,
      });
    } catch (e) {
      set({ loading: false, error: e });
      throw e;
    }
  },

  async switchTo(provider, mode) {
    // guard against empty values -> avoids 422
    const prov = (provider ?? "").toLowerCase() as Provider;
    const m = (mode ?? "").toUpperCase() as Mode;
    if (!prov || !m) {
      const err = new Error("Provider/mode are required");
      set({ error: err });
      throw err;
    }

    // avoid redundant switch loops
    const cur = get();
    if (cur.active === prov && cur.mode === m) return;

    set({ loading: true, error: null });
    try {
      const res = await switchProviderConfig(prov, m);
      set({
        active: res.active,
        mode: res.mode,
        available: res.available ?? ["gate", "mexc", "binance"],
        wsEnabled: !!res.ws_enabled,
        revision: res.revision ?? cur.revision + 1,
        loading: false,
        error: null,
      });
    } catch (e) {
      set({ loading: false, error: e });
      throw e;
    }
  },
}));
