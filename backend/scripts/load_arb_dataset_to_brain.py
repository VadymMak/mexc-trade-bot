"""
load_arb_dataset_to_brain.py — bulk-load historical arb CSV into brain_embeddings.

Usage:
    python scripts/load_arb_dataset_to_brain.py --file data/arb_dataset.csv
    python scripts/load_arb_dataset_to_brain.py --file data/arb_dataset.csv --batch 50 --dry-run

Expected CSV columns (from arb researcher export):
    symbol, entry_mode, entry_spread_pct, entry_zscore,
    spread_mean, spread_std, buy_pressure, trade_velocity, book_imbalance,
    hour_utc, day_of_week, trading_session, is_weekend, mins_to_funding,
    exit_reason, hold_seconds, pnl_pct, net_pnl_usdt, profitable

All other columns are ignored.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# make sure backend/ is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brain_loader")


# ── CSV column helpers ────────────────────────────────────────────────────────

def _opt_float(row: Dict[str, str], key: str) -> Optional[float]:
    v = row.get(key, "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _opt_int(row: Dict[str, str], key: str) -> Optional[int]:
    v = row.get(key, "").strip()
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _opt_bool(row: Dict[str, str], key: str) -> Optional[bool]:
    v = row.get(key, "").strip().lower()
    if not v:
        return None
    return v in {"1", "true", "yes", "t"}


def _parse_row(raw: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Convert a raw CSV row to a brain_embeddings-compatible dict.
    Returns None if entry_spread_pct is missing (mandatory).

    CSV column mapping:
        trading_session  → session (DB) + trading_session (embedding text)
        entry_spread_pct → entry_spread_pct
        ... etc.
    """
    entry_spread_pct = _opt_float(raw, "entry_spread_pct")
    if entry_spread_pct is None:
        return None

    # CSV exports use 'trading_session'; brain_embeddings table uses 'session'
    session = raw.get("trading_session", "").strip() or raw.get("session", "").strip() or None

    return {
        "symbol":           raw.get("symbol", "").strip() or None,
        "session":          session,
        "hour_utc":         _opt_int(raw, "hour_utc"),
        "day_of_week":      _opt_int(raw, "day_of_week"),
        "is_weekend":       _opt_bool(raw, "is_weekend"),
        "entry_mode":       raw.get("entry_mode", "").strip() or None,
        "entry_spread_pct": entry_spread_pct,
        "entry_zscore":     _opt_float(raw, "entry_zscore"),
        "spread_mean":      _opt_float(raw, "spread_mean"),
        "spread_std":       _opt_float(raw, "spread_std"),
        "buy_pressure":     _opt_float(raw, "buy_pressure"),
        "trade_velocity":   _opt_float(raw, "trade_velocity"),
        "book_imbalance":   _opt_float(raw, "book_imbalance"),
        "mins_to_funding":  _opt_float(raw, "mins_to_funding"),
        "exit_reason":      raw.get("exit_reason", "").strip() or None,
        "hold_seconds":     _opt_int(raw, "hold_seconds"),
        "pnl_pct":          _opt_float(raw, "pnl_pct"),
        "net_pnl_usdt":     _opt_float(raw, "net_pnl_usdt"),
        "profitable":       _opt_bool(raw, "profitable"),
        # brain_service.build_embedding_text() reads this key for session feature
        "trading_session":  session,
    }


# ── async loader ─────────────────────────────────────────────────────────────

async def load(csv_path: str, batch: int, dry_run: bool, pause: float, offset: int = 0) -> None:
    from app.services.brain_service import BrainService

    brain = BrainService()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))

    total = len(reader)
    if offset:
        reader = reader[offset:]
        log.info("CSV rows: %d | skipping first %d | remaining: %d | batch: %d | pause: %.1fs | dry_run: %s",
                 total, offset, len(reader), batch, pause, dry_run)
    else:
        log.info("CSV rows: %d | batch: %d | pause: %.1fs | dry_run: %s", total, batch, pause, dry_run)
    total = len(reader)

    loaded = 0
    skipped = 0
    errors = 0

    for batch_start in range(0, total, batch):
        chunk = reader[batch_start: batch_start + batch]
        tasks = []
        parsed_chunk = []

        for raw in chunk:
            trade = _parse_row(raw)
            if trade is None:
                skipped += 1
                continue
            parsed_chunk.append(trade)
            if not dry_run:
                tasks.append(brain.store_trade(trade))

        if dry_run:
            loaded += len(parsed_chunk)
            done = batch_start + len(chunk)
            print(f"[dry-run] Loaded {loaded}/{total}... (batch {batch_start+1}-{done})")
            continue

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ok in results:
            if ok is True:
                loaded += 1
            else:
                errors += 1
                if isinstance(ok, Exception):
                    log.warning("store_trade error: %s", ok)

        done = batch_start + len(chunk)
        print(f"Loaded {loaded}/{total}... (skipped={skipped} errors={errors})")

        # rate-limit pause between batches
        if done < total and pause > 0:
            time.sleep(pause)

    print(f"\nDone. Loaded: {loaded}, Skipped: {skipped}, Errors: {errors}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Load arb CSV dataset into brain_embeddings")
    parser.add_argument("--file", required=True, help="Path to CSV file")
    parser.add_argument("--batch", type=int, default=50,
                        help="Rows per async batch (default 50; keep ≤50 for OpenAI rate limits)")
    parser.add_argument("--pause", type=float, default=1.0,
                        help="Seconds to sleep between batches (default 1.0)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip first N rows — use to resume after interruption (default 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse CSV only — no embeddings, no DB inserts")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        log.error("File not found: %s", args.file)
        sys.exit(1)

    asyncio.run(load(args.file, batch=args.batch, dry_run=args.dry_run, pause=args.pause, offset=args.offset))


if __name__ == "__main__":
    main()
