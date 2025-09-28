import { useEffect, useRef, useState } from "react";
import StrategySettingsModal from "./StrategySettingsModal";

type OpenDetail = { symbol?: string };
type OpenEvent = CustomEvent<OpenDetail>;

const norm = (s: string) => s.trim().toUpperCase();

export default function StrategySettingsHost() {
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState<string | null>(null);

  // track the last-opened symbol to ignore duplicate events
  const lastSymRef = useRef<string | null>(null);

  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as OpenEvent;
      const nextRaw = ev.detail?.symbol;
      if (!nextRaw) return;
      const next = norm(nextRaw);

      // ignore if same symbol already opened
      if (open && lastSymRef.current === next) return;

      lastSymRef.current = next;
      setSymbol(next);
      setOpen(true);
    };

    window.addEventListener("open-strategy-settings", handler as EventListener);
    return () => window.removeEventListener("open-strategy-settings", handler as EventListener);
  }, [open]);

  if (!open || !symbol) return null;

  return (
    <StrategySettingsModal
      symbol={symbol}
      open={open}
      onClose={() => {
        setOpen(false);
        setSymbol(null);
        // keep lastSymRef; it only prevents redundant re-opens while open
      }}
    />
  );
}
