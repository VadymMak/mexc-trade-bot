'use client';

import { useEffect, useRef } from 'react';

export function usePolling(
  fn: () => Promise<void>,
  intervalMs: number,
  enabled: boolean = true
): void {
  const fnRef = useRef(fn);
  useEffect(() => { fnRef.current = fn; });

  useEffect(() => {
    if (!enabled) return;

    let timerId: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const tick = async () => {
      if (cancelled || document.hidden) return;
      await fnRef.current().catch(() => {/* errors handled by caller */});
      if (!cancelled) {
        timerId = setTimeout(tick, intervalMs);
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        // Tab hidden — cancel pending tick so no requests fire in background
        if (timerId !== null) {
          clearTimeout(timerId);
          timerId = null;
        }
      } else if (!cancelled) {
        // Tab visible — fetch immediately then resume polling
        tick();
      }
    };

    document.addEventListener('visibilitychange', onVisibility);
    tick();

    return () => {
      cancelled = true;
      if (timerId !== null) clearTimeout(timerId);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [intervalMs, enabled]);
}
