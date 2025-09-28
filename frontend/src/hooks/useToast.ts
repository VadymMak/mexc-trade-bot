import { useToastStore, type ToastKind } from "@/store/toast";

/** Hook to push/remove/clear toast notifications */
export function useToast() {
  const add = useToastStore((s) => s.add);
  const remove = useToastStore((s) => s.remove);
  const clear = useToastStore((s) => s.clear);

  function push(
    kind: ToastKind,
    message: string,
    title?: string,
    timeoutMs?: number
  ) {
    return add({ kind, message, title, timeoutMs });
  }

  return {
    push,
    success: (msg: string, title?: string) => push("success", msg, title),
    error: (msg: string, title?: string) => push("error", msg, title, 5500),
    info: (msg: string, title?: string) => push("info", msg, title),
    remove,
    clear,
  };
}
