# scripts/test_scanner.py
import asyncio
import sys
import argparse
import json
import time
from pathlib import Path
from typing import List, Dict, Any

# --- Make project root importable when running as a file ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# -----------------------------------------------------------

from app.config.settings import settings
from app.services.market_scanner import (
    scan_mexc_with_preset,
    scan_gate_with_preset,
    ScanRow,
)

# We import these "internal" helpers only for diagnostics in this test script.
# If you later want to avoid underscore imports, mirror the logic locally.
try:
    from app.services.market_scanner import _mexc_rest_base, _gate_rest_base  # type: ignore
except Exception:
    _mexc_rest_base = lambda: getattr(settings, "mexc_rest_base", "https://api.mexc.com")  # noqa: E731
    _gate_rest_base = lambda: getattr(settings, "gate_rest_base", "https://api.gateio.ws/api/v4")  # noqa: E731


def _parse_csv(s: str) -> List[str]:
    return [x.strip().upper() for x in s.split(",") if x.strip()]


def _row_to_dict(row: ScanRow) -> Dict[str, Any]:
    d5 = row.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
    return {
        "exchange": row.exchange,
        "symbol": row.symbol,
        "score": row.score,
        "bid": row.bid,
        "ask": row.ask,
        "spread_bps": row.spread_bps,
        "usd_per_min": row.usd_per_min,
        "trades_per_min": row.trades_per_min,
        "atr_proxy": row.atr_proxy,
        "vol_pattern": row.vol_pattern,
        "liquidity_grade": row.liquidity_grade,
        "net_profit_pct": row.net_profit_pct,
        "depth_5bps": {"bid_usd": d5.get("bid_usd", 0.0), "ask_usd": d5.get("ask_usd", 0.0)},
        "reason": row.reason,
        "reasons_all": row.reasons_all,
    }


def _print_rows(rows: List[ScanRow], title: str, *, as_json: bool = False) -> None:
    print(f"\n{title}")
    print(f"Found {len(rows)} rows.")
    if as_json:
        payload = [_row_to_dict(r) for r in rows]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    for i, row in enumerate(rows, 1):
        d5 = row.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        print(f"\n{i}. {row.exchange.upper()} - {row.symbol}")
        print(
            f"   Score: {row.score:.2f} | Spread: {row.spread_bps:.2f} bps"
            f" | USD/min: {row.usd_per_min:.2f} | TPM: {row.trades_per_min:.2f}"
        )
        print(
            f"   ATR proxy: {row.atr_proxy or 0:.4f} | Vol Pattern: {row.vol_pattern or 0}/100"
            f" | Grade: {row.liquidity_grade or '-'} | NetProfit%: {row.net_profit_pct or 0:.3f}"
        )
        print(f"   Depth@5bps: bid=${d5.get('bid_usd', 0):.0f} ask=${d5.get('ask_usd', 0):.0f}")
        if row.reason:
            print(f"   Reason: {row.reason}")
        if row.reasons_all:
            print(f"   Reasons: {', '.join(row.reasons_all)}")


async def _run(
    exchange: str,
    preset: str,
    quote: str,
    limit: int,
    symbols: List[str],
    explain: bool,
    use_cache: bool,
    dump_json: bool,
) -> None:
    # Diagnostics (global)
    print("🔧 Settings snapshot")
    print(f"  ACTIVE_PROVIDER = {settings.active_provider}")
    print(f"  ACTIVE_MODE     = {settings.active_mode}")
    print(f"  REST resolved   = {settings.rest_base_url_resolved}")
    print(f"  Symbols (env)   = {settings.symbols}")
    if symbols:
        print(f"  Symbols (cli)   = {symbols}")

    rows_gate: List[ScanRow] = []
    rows_mexc: List[ScanRow] = []

    # Selected exchange: MEXC
    if exchange in {"mexc", "both"}:
        print("\n🧪 Scanning MEXC…")
        print(f"   • Base URL: {_mexc_rest_base()}")
        t0 = time.perf_counter()
        try:
            rows_mexc = await scan_mexc_with_preset(
                preset=preset,
                quote=quote,
                limit=limit,
                symbols=symbols or None,
                explain=explain,
                use_cache=use_cache,
                include_stables=False,
                exclude_leveraged=True,
            )
            dt = time.perf_counter() - t0
            _print_rows(rows_mexc, f"✅ MEXC ({preset})  ⏱ {dt:.2f}s", as_json=dump_json)
        except Exception as e:
            dt = time.perf_counter() - t0
            print(f"❌ MEXC scan failed in {dt:.2f}s: {e}")

    # Selected exchange: Gate
    if exchange in {"gate", "both"}:
        print("\n🧪 Scanning Gate…")
        print(f"   • Base URL: {_gate_rest_base()}")
        t0 = time.perf_counter()
        try:
            rows_gate = await scan_gate_with_preset(
                preset=("scalper" if preset == "balanced" else preset),  # default suggestion
                quote=quote,
                limit=limit,
                symbols=symbols or None,
                explain=explain,
                use_cache=use_cache,
                include_stables=False,
                exclude_leveraged=True,
            )
            dt = time.perf_counter() - t0
            _print_rows(rows_gate, f"✅ Gate ({preset})  ⏱ {dt:.2f}s", as_json=dump_json)
        except Exception as e:
            dt = time.perf_counter() - t0
            print(f"⚠️  Gate scan failed in {dt:.2f}s (network/timeout?): {e}. Skipping Gate.")

    # Combined summary (human or JSON)
    all_rows = rows_mexc + rows_gate
    if dump_json:
        print("\n📊 Combined JSON")
        print(json.dumps([_row_to_dict(r) for r in all_rows], indent=2, ensure_ascii=False))
    print(f"\n📊 Combined total rows: {len(all_rows)}")


def main():
    ap = argparse.ArgumentParser(description="Run spot scanner (MEXC/Gate) with presets.")
    ap.add_argument("--exchange", default="mexc", choices=["mexc", "gate", "both"],
                    help="Which exchange to scan.")
    ap.add_argument("--preset", default="balanced",
                    help="Preset key (must exist in app.scoring.presets.PRESETS).")
    ap.add_argument("--quote", default="USDT", help="Quote currency (e.g. USDT).")
    ap.add_argument("--limit", type=int, default=5, help="Max rows to return.")
    ap.add_argument("--symbols", default="", help="Optional CSV: BTCUSDT,ETHUSDT,…")
    ap.add_argument("--no-cache", action="store_true", help="Disable scanner cache.")
    ap.add_argument("--no-explain", action="store_true",
                    help="Disable reasons/reasons_all enrichment.")
    ap.add_argument("--json", action="store_true",
                    help="Print results as JSON (per-exchange and combined).")
    args = ap.parse_args()

    symbols = _parse_csv(args.symbols) if args.symbols else []
    asyncio.run(_run(
        exchange=args.exchange,
        preset=args.preset,
        quote=args.quote,
        limit=args.limit,
        symbols=symbols,
        explain=not args.no_explain,
        use_cache=not args.no_cache,
        dump_json=args.json,
    ))


if __name__ == "__main__":
    main()
