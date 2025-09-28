import type { AxiosError } from "axios";

/** Normalize unknown error into a human-friendly message */
export function getErrorMessage(err: unknown): string {
  // axios-ошибка
  const maybeAxios = err as AxiosError<unknown>;
  const data = maybeAxios?.response?.data;

  if (typeof data === "object" && data !== null) {
    const d = data as Record<string, unknown>;
    if (typeof d.detail === "string" && d.detail.trim()) return d.detail;
    if (typeof d.message === "string" && d.message.trim()) return d.message;
  }

  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;

  try {
    return JSON.stringify(err);
  } catch {
    return "Unknown error";
  }
}
