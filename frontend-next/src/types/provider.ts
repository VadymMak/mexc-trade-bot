/* ───────── Provider base types ───────── */

/** Доступные торговые провайдеры. */
export type Provider = "gate" | "mexc" | "binance";

/** Режим работы стратегий / API. */
export type Mode = "PAPER" | "DEMO" | "LIVE";

/** Текущее состояние выбранного провайдера, возвращаемое бэкендом. */
export interface ProviderState {
  active: Provider;
  mode: Mode;
  available: Provider[];
  ws_enabled: boolean;
  revision: number;
}

/** Ответ при переключении провайдера (может содержать дополнительные поля). */
export interface ProviderSwitchResponse extends ProviderState {
  [key: string]: unknown;
}
