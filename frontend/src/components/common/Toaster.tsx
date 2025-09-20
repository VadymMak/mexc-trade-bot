import { useToastStore } from "@/store/toast";
import cx from "classnames";

export default function Toaster() {
  const { toasts, remove } = useToastStore();

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-[9999] flex w-[380px] max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cx(
            "pointer-events-auto rounded-xl border p-3 shadow-lg backdrop-blur",
            "border-zinc-700/70 bg-zinc-800/90 text-zinc-100",
            t.kind === "success" && "border-emerald-700/60",
            t.kind === "error" && "border-rose-700/60",
            t.kind === "info" && "border-sky-700/60"
          )}
        >
          <div className="flex items-start gap-2">
            <div className={cx(
              "mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full",
              t.kind === "success" && "bg-emerald-500",
              t.kind === "error" && "bg-rose-500",
              t.kind === "info" && "bg-sky-500"
            )}/>
            <div className="flex-1">
              {t.title && <div className="text-sm font-semibold">{t.title}</div>}
              <div className="text-sm text-zinc-200">{t.message}</div>
            </div>
            <button
              onClick={() => remove(t.id)}
              className="rounded-md px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-700/60"
            >
              ×
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
