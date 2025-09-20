# app/market_data/helpers/quote_logging.py
from __future__ import annotations

import time
from math import isfinite
from typing import Dict, Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


class QuoteLogger:
    """Keeps last quotes, throttles logs, prints periodic summaries."""

    def __init__(self, log_throttle_ms: int = 2000, summary_every_ms: int = 5000) -> None:
        self._log_throttle_ms = int(log_throttle_ms)
        self._summary_every_ms = int(summary_every_ms)
        self._last_log_by_symbol: Dict[str, int] = {}
        self._last_summary_by_symbol: Dict[str, int] = {}
        self._last_quote: Dict[str, tuple[float, float, float, float]] = {}  # bid,bidq,ask,askq

    # ---------- validation ----------
    @staticmethod
    def _valid_quote(b: float, bq: float, a: float, aq: float) -> bool:
        if not all(isfinite(x) for x in (b, bq, a, aq)):
            return False
        if b <= 0 or a <= 0 or b >= a:
            return False
        if bq < 0 or aq < 0:
            return False
        return True

    # ---------- public API ----------
    def accept_and_log(
        self,
        symbol: str,
        bid: float,
        bidq: float,
        ask: float,
        askq: float,
        send_time_ms: Optional[int],
        src: Optional[str] = None,
        verbose: bool = False,
    ) -> bool:
        """Store quote, throttle detailed log, and emit periodic summary.
        Returns True if quote accepted.
        """
        if not self._valid_quote(bid, bidq, ask, askq):
            if verbose:
                print(f"⚠️ Dropped bad tick {symbol}: bid={bid} ask={ask} bidq={bidq} askq={askq}")
            return False

        self._last_quote[symbol] = (bid, bidq, ask, askq)

        now = _now_ms()
        last = self._last_log_by_symbol.get(symbol, 0)
        if now - last >= self._log_throttle_ms:
            lag = (now - int(send_time_ms)) if send_time_ms else None
            lag_str = f" lag={lag}ms" if lag is not None else ""
            if src:
                print(f"✅ Parsed {symbol} via {src}: bid={bid} ({bidq}) ask={ask} ({askq}){lag_str}")
            else:
                print(f"✅ Parsed {symbol}: bid={bid} ({bidq}) ask={ask} ({askq}){lag_str}")
            self._last_log_by_symbol[symbol] = now

        self._maybe_summary_log(symbol)
        return True

    # ---------- internals ----------
    def _maybe_summary_log(self, symbol: str) -> None:
        now = _now_ms()
        last = self._last_summary_by_symbol.get(symbol, 0)
        if now - last < self._summary_every_ms:
            return
        q = self._last_quote.get(symbol)
        if not q:
            return
        b, bq, a, aq = q
        mid = 0.5 * (b + a)
        spread = a - b
        spr_bps = (spread / mid) * 1e4 if mid else 0.0
        print(f"📈 {symbol} mid={mid:.8f} spread={spread:.8f} ({spr_bps:.2f} bps) | sizes: bid={bq} ask={aq}")
        self._last_summary_by_symbol[symbol] = now
