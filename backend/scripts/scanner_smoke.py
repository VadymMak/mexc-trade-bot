# scripts/scanner_smoke.py
import os
import sys
import json
import argparse
import asyncio
from typing import List, Dict, Any

# ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.market_scanner import (
    scan_gate_quote,
    scan_mexc_quote,
    scan_gate_with_preset,
    scan_mexc_with_preset,
)

def _parse_csv_symbols(s: str | None) -> List[str] | None:
    if not s:
        return None
    return [x.strip().upper() for x in s.split(",") if x.strip()]

def _parse_csv_ints(s: str | None) -> List[int] | None:
    if not s:
        return None
    out: List[int] = []
    for p in s.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            pass
    return out or None

def _brief(rows):
    out = []
    for r in rows:
        try:
            out.append({
                "symbol": getattr(r, "symbol", "?"),
                "exchange": getattr(r, "exchange", "?"),
                "bid": getattr(r, "bid", None),
                "ask": getattr(r, "ask", None),
                "spread_bps": getattr(r, "spread_bps", None),
                "eff_taker_bps": getattr(r, "eff_spread_bps_taker", None),
                "eff_maker_bps": getattr(r, "eff_spread_bps_maker", None),
                "usd_per_min": getattr(r, "usd_per_min", None),
                "tpm": getattr(r, "trades_per_min", None),
                "depth5_bid_usd": getattr(r, "depth_at_bps", {}).get(5, {}).get("bid_usd"),
                "depth5_ask_usd": getattr(r, "depth_at_bps", {}).get(5, {}).get("ask_usd"),
                "score": getattr(r, "score", None),
                "reason": getattr(r, "reason", None),
            })
        except Exception:
            pass
    return out

def _print_table(title: str, rows) -> None:
    print()
    print(f"Top 5 — {title}")
    print("symbol     spread_bps  eff_taker  usd/min   tpm   depth5(bid/ask)   reason")
    print("---------  ----------  ---------  -------  -----  -----------------  ------")
    for r in rows[:5]:
        s = getattr(r, "symbol", "?")
        sb = getattr(r, "spread_bps", float("nan"))
        et = getattr(r, "eff_spread_bps_taker", None)
        upm = getattr(r, "usd_per_min", float("nan"))
        tpm = getattr(r, "trades_per_min", float("nan"))
        d5 = getattr(r, "depth_at_bps", {}).get(5, {"bid_usd": 0, "ask_usd": 0})
        reason = getattr(r, "reason", "")
        et_str = f"{et:.0f}" if isinstance(et, (int, float)) else "nan"
        print(
            f"{s:<10}  {sb:>10.3f}  {et_str:>9}  "
            f"{upm:>7.1f}  {tpm:>5.1f}     {int(d5.get('bid_usd',0))}/{int(d5.get('ask_usd',0))}     {reason}"
        )

# --- find this function and replace it entirely ---
def build_overrides_from_args(args) -> dict:
    """
    Translate CLI flags to scanner kwargs (for both direct calls and preset wrappers).
    NOTE: Do NOT include 'explain' or 'use_cache' here — those are passed explicitly.
    """
    overrides = {}

    # common
    if getattr(args, "min_vol", None) is not None:
        overrides["min_quote_vol_usd"] = float(args.min_vol)
    if getattr(args, "max_spread_bps", None) is not None:
        overrides["max_spread_bps"] = float(args.max_spread_bps)

    # depth levels can be stored as 'depth_levels_bps' OR 'depth_levels_bps_raw'
    raw_levels = getattr(args, "depth_levels_bps", None)
    if raw_levels is None:
        raw_levels = getattr(args, "depth_levels_bps_raw", None)
    if raw_levels:
        if isinstance(raw_levels, str):
            parts = [p.strip() for p in raw_levels.split(",") if p.strip()]
        else:
            parts = list(raw_levels)
        try:
            levels = sorted({int(p) for p in parts})
            if levels:
                overrides["depth_levels_bps"] = levels
        except Exception:
            pass  # ignore bad input; tests expect we don't crash

    if getattr(args, "min_depth5_usd", None) is not None:
        overrides["min_depth5_usd"] = float(args.min_depth5_usd)
    if getattr(args, "min_depth10_usd", None) is not None:
        overrides["min_depth10_usd"] = float(args.min_depth10_usd)
    if getattr(args, "min_trades_per_min", None) is not None:
        overrides["min_trades_per_min"] = float(args.min_trades_per_min)
    if getattr(args, "min_usd_per_min", None) is not None:
        overrides["min_usd_per_min"] = float(args.min_usd_per_min)
    if getattr(args, "min_median_trade_usd", None) is not None:
        overrides["min_median_trade_usd"] = float(args.min_median_trade_usd)
    if getattr(args, "min_vol_pattern", None) is not None:
        overrides["min_vol_pattern"] = float(args.min_vol_pattern)
    if getattr(args, "max_atr_proxy", None) is not None:
        overrides["max_atr_proxy"] = float(args.max_atr_proxy)
    if getattr(args, "activity_ratio", None) is not None:
        overrides["activity_ratio"] = float(args.activity_ratio)
    if getattr(args, "liquidity_test", False):
        overrides["liquidity_test"] = True
    if getattr(args, "symbols", None):
        overrides["symbols"] = list(args.symbols)

    # IMPORTANT: do not add 'explain' or 'use_cache' here (passed explicitly)
    return overrides


async def run(args: argparse.Namespace):
    exchanges = [x.strip().lower() for x in (args.exchanges or "gate,mexc").split(",") if x.strip()]
    quote = args.quote.upper()
    symbols = _parse_csv_symbols(args.symbols)
    depth_levels = _parse_csv_ints(args.depth_levels_bps_raw)
    if depth_levels:
        args.depth_levels_bps = depth_levels
    else:
        args.depth_levels_bps = None

    overrides = build_overrides_from_args(args)
    explain_flag = not args.no_explain
    cache_flag = not args.no_cache

    results = {}

    async def run_gate():
        if args.preset:
            rows_gate = await scan_gate_with_preset(
                preset=args.preset,
                quote=quote,
                limit=args.limit,
                include_stables=False,
                exclude_leveraged=True,
                explain=explain_flag,
                use_cache=cache_flag,
                symbols=symbols,
                **overrides,
            )
        else:
            rows_gate = await scan_gate_quote(
                quote=quote,
                limit=args.limit,
                include_stables=False,
                exclude_leveraged=True,
                explain=explain_flag,
                use_cache=cache_flag,
                symbols=symbols,
                **overrides,
            )
        results["gate"] = rows_gate

    async def run_mexc():
        if args.preset:
            rows_mexc = await scan_mexc_with_preset(
                preset=args.preset,
                quote=quote,
                limit=args.limit,
                include_stables=False,
                exclude_leveraged=True,
                explain=explain_flag,
                use_cache=cache_flag,
                symbols=symbols,
                **overrides,
            )
        else:
            rows_mexc = await scan_mexc_quote(
                quote=quote,
                limit=args.limit,
                include_stables=False,
                exclude_leveraged=True,
                explain=explain_flag,
                use_cache=cache_flag,
                symbols=symbols,
                **overrides,
            )
        results["mexc"] = rows_mexc

    tasks = []
    if "gate" in exchanges:
        tasks.append(asyncio.create_task(run_gate()))
    if "mexc" in exchanges:
        tasks.append(asyncio.create_task(run_mexc()))
    await asyncio.gather(*tasks)

    # Output
    if args.json:
        payload = {}
        if "gate" in results:
            payload["gate_len"] = len(results["gate"])
            payload["gate_sample"] = _brief(results["gate"])[:5]
        if "mexc" in results:
            payload["mexc_len"] = len(results["mexc"])
            payload["mexc_sample"] = _brief(results["mexc"])[:5]
        print(json.dumps(payload, indent=2))
        return

    print("\n=== Scanner Smoke Summary ===")
    if "gate" in results:
        print(json.dumps({
            "gate_len": len(results["gate"]),
            "gate_sample": _brief(results["gate"])[:5],
        }, indent=2))
    if "mexc" in results:
        print(json.dumps({
            "mexc_len": len(results["mexc"]),
            "mexc_sample": _brief(results["mexc"])[:5],
        }, indent=2))

    if "gate" in results:
        _print_table(f"GATE ({quote})", results["gate"])
    if "mexc" in results:
        _print_table(f"MEXC ({quote})", results["mexc"])

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--exchanges", "-x", default="gate,mexc",
                   help="Comma list: gate,mexc (default: both)")
    p.add_argument("--quote", default="USDT", help="Quote currency (default: USDT)")
    p.add_argument("--limit", type=int, default=10, help="How many rows to show (default: 10)")
    p.add_argument("--symbols", "-s", help="Comma list of symbols to focus on, e.g. BTCUSDT,ETHUSDT")

    # presets / behavior
    p.add_argument("--preset", default=None, help="Scanner preset name (e.g., balanced, tight, fast)")
    p.add_argument("--no-cache", action="store_true", help="Disable cache")
    p.add_argument("--no-explain", action="store_true", help="Disable explanation annotations")
    p.add_argument("--json", action="store_true", help="Print JSON only")
    p.add_argument("--timeout", type=float, default=None, help="(Reserved) HTTP timeout override")

    # basic filters
    p.add_argument("--min-vol", type=float, default=None, help="Min 24h quote volume in USD")
    p.add_argument("--max-spread-bps", type=float, default=None, help="Max allowed spread (bps) at Stage 1")

    # advanced enrich / filters
    p.add_argument("--depth-levels-bps", dest="depth_levels_bps_raw",
                   help="Comma list of depth levels in bps, e.g. 5,10")
    p.add_argument("--min-depth5-usd", type=float, default=None, help="Min required depth within 5 bps (USD)")
    p.add_argument("--min-depth10-usd", type=float, default=None, help="Min required depth within 10 bps (USD)")
    p.add_argument("--min-trades-per-min", type=float, default=None, help="Min trades per minute")
    p.add_argument("--min-usd-per-min", type=float, default=None, help="Min USD turnover per minute")
    p.add_argument("--min-median-trade-usd", type=float, default=None, help="Min median trade USD")
    p.add_argument("--min-vol-pattern", type=int, default=None, help="Min vol_pattern score (0..100)")
    p.add_argument("--max-atr-proxy", type=float, default=None, help="Max ATR-like volatility proxy")
    p.add_argument("--activity-ratio", type=float, default=None,
                   help="Filter if usd/min < activity_ratio * min(depth5 bid/ask)")
    p.add_argument("--liquidity-test", action="store_true",
                   help="Filter out grade C by depth@5bps")

    return p

if __name__ == "__main__":
    parser = build_arg_parser()
    args = parser.parse_args()
    asyncio.run(run(args))
