"""
load_arb_dataset_to_brain.py — bulk-load historical arb CSV into brain_embeddings.

Usage:
    python scripts/load_arb_dataset_to_brain.py data/arb_trades.csv
    python scripts/load_arb_dataset_to_brain.py data/arb_trades.csv --batch 50 --dry-run

Expected CSV columns (at minimum):
    entry_spread_pct, symbol, session, hour_utc, day_of_week, is_weekend,
    entry_mode, entry_zscore, spread_mean, spread_std, buy_pressure,
    trade_velocity, book_imbalance, mins_to_funding,
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
from pathlib import Path
from typing import Any, Dict, Optional

# make sure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brain_loader")


# ── CSV helpers ──────────────────────────────────────────────────────────────

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
    """Convert a raw CSV row dict to a brain_embeddings-compatible dict."""
    entry_spread_pct = _opt_float(raw, "entry_spread_pct")
    if entry_spread_pct is None:
        return None  # mandatory field

    return {
        "symbol":           raw.get("symbol", "").strip() or None,
        "session":          raw.get("session", "").strip() or None,
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
        # brain_service.build_embedding_text uses these keys:
        "trading_session":  raw.get("session", "").strip() or None,
    }


# ── async loader ─────────────────────────────────────────────────────────────

async def load(csv_path: str, batch: int, dry_run: bool) -> None:
    from app.services.brain_service import BrainService

    brain = BrainService()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))

    total = len(reader)
    log.info("CSV rows read: %d", total)

    loaded = 0
    skipped = 0
    errors = 0

    # process in batches to avoid overloading the OpenAI API
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
            log.info(
                "[dry-run] batch %d-%d: would insert %d rows",
                batch_start + 1,
                batch_start + len(chunk),
                len(parsed_chunk),
            )
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
        log.info("Progress: %d / %d  (loaded=%d skipped=%d errors=%d)", done, total, loaded, skipped, errors)

    log.info("Done. loaded=%d  skipped=%d  errors=%d  total=%d", loaded, skipped, errors, total)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Load arb CSV dataset into brain_embeddings")
    parser.add_argument("csv", help="Path to CSV file")
    parser.add_argument("--batch", type=int, default=100, help="Rows per async batch (default 100)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB inserts")
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        log.error("File not found: %s", args.csv)
        sys.exit(1)

    asyncio.run(load(args.csv, batch=args.batch, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
