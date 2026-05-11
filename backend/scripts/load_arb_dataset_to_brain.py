"""
load_arb_dataset_to_brain.py — bulk-load historical arb CSV into brain_embeddings.

Usage:
    python scripts/load_arb_dataset_to_brain.py --file data/arb_dataset.csv
    python scripts/load_arb_dataset_to_brain.py --file data/arb_dataset.csv --batch 50 --dry-run
    python scripts/load_arb_dataset_to_brain.py --file data/arb_dataset.csv --auto-offset

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
from typing import Any, Dict, List, Optional

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
    # ── Era 3 filter: only mature zscore strategy ──
    entry_mode = raw.get("entry_mode", "").strip()
    if entry_mode != "zscore":
        return None

    entry_spread_pct = _opt_float(raw, "entry_spread_pct")
    if entry_spread_pct is None or entry_spread_pct < 0.3 or entry_spread_pct > 10:
        return None

    hold_seconds = _opt_int(raw, "hold_seconds")
    if hold_seconds is None or hold_seconds < 5 or hold_seconds > 3600:
        return None

    exit_reason = raw.get("exit_reason", "").strip()
    if not exit_reason:
        return None

    session = raw.get("trading_session", "").strip() or raw.get("session", "").strip() or None

    return {
        "symbol":              raw.get("symbol", "").strip() or None,
        "session":             session,
        "hour_utc":            _opt_int(raw, "hour_utc"),
        "day_of_week":         _opt_int(raw, "day_of_week"),
        "is_weekend":          _opt_bool(raw, "is_weekend"),
        "entry_mode":          entry_mode,
        "entry_spread_pct":    entry_spread_pct,
        "entry_zscore":        _opt_float(raw, "entry_zscore"),
        "spread_mean":         _opt_float(raw, "spread_mean"),
        "spread_std":          _opt_float(raw, "spread_std"),
        "buy_pressure":        _opt_float(raw, "buy_pressure"),
        "trade_velocity":      _opt_float(raw, "trade_velocity"),
        "book_imbalance":      _opt_float(raw, "book_imbalance"),
        "mins_to_funding":     _opt_float(raw, "mins_to_funding"),
        "exit_reason":         exit_reason,
        "hold_seconds":        hold_seconds,
        "pnl_pct":             _opt_float(raw, "pnl_pct"),
        "net_pnl_usdt":        _opt_float(raw, "net_pnl_usdt"),
        "profitable":          _opt_bool(raw, "profitable"),
        "trading_session":     session,
    }


# ── DB helpers (sync, used in thread) ────────────────────────────────────────

def _get_db_count(url: str) -> int:
    """Return current row count from brain_embeddings."""
    import psycopg2
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM brain_embeddings")
    count = cur.fetchone()[0]
    conn.close()
    return count


def _insert_batch(url: str, rows: List[Dict[str, Any]], emb_strings: List[str]) -> tuple[int, int]:
    """
    Insert a batch of rows in a SINGLE transaction with retry on connection drop.
    Returns (inserted, errors).
    """
    import psycopg2

    INSERT_SQL = """
        INSERT INTO brain_embeddings (
            symbol, session, hour_utc, day_of_week, is_weekend,
            entry_mode, entry_spread_pct, entry_zscore,
            spread_mean, spread_std, buy_pressure,
            trade_velocity, book_imbalance, mins_to_funding,
            exit_reason, hold_seconds, pnl_pct,
            net_pnl_usdt, profitable, scan_embedding
        ) VALUES (
            %(symbol)s, %(session)s, %(hour_utc)s, %(day_of_week)s, %(is_weekend)s,
            %(entry_mode)s, %(entry_spread_pct)s, %(entry_zscore)s,
            %(spread_mean)s, %(spread_std)s, %(buy_pressure)s,
            %(trade_velocity)s, %(book_imbalance)s, %(mins_to_funding)s,
            %(exit_reason)s, %(hold_seconds)s, %(pnl_pct)s,
            %(net_pnl_usdt)s, %(profitable)s, %(emb)s::vector
        )
    """

    for attempt in range(3):
        inserted = 0
        errors = 0
        try:
            conn = psycopg2.connect(url, connect_timeout=10)
            conn.autocommit = False
            cur = conn.cursor()

            for trade, emb_str in zip(rows, emb_strings):
                cur.execute(INSERT_SQL, {**trade, "emb": emb_str})
                inserted += 1

            conn.commit()
            conn.close()
            return inserted, errors

        except Exception as e:
            err_msg = str(e)
            log.warning("Batch insert attempt %d failed: %s", attempt + 1, err_msg)
            try:
                conn.close()
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s backoff
            else:
                log.error("Batch insert failed after 3 attempts")
                errors = len(rows)

    return 0, errors


# ── async loader ─────────────────────────────────────────────────────────────

async def load(csv_path: str, batch: int, dry_run: bool, pause: float, offset: int = 0) -> None:
    from app.services.brain_service import BrainService

    brain = BrainService()
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL", "")

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))

    total_csv = len(reader)

    # Auto-resolve offset from DB if --auto-offset flag was used (offset=-1 sentinel)
    if offset == -1:
        offset = _get_db_count(db_url)
        log.info("Auto-offset from DB count: %d", offset)

    if offset:
        reader = reader[offset:]
        log.info(
            "CSV rows: %d | skipping first %d | remaining: %d | batch: %d | pause: %.1fs | dry_run: %s",
            total_csv, offset, len(reader), batch, pause, dry_run,
        )
    else:
        log.info("CSV rows: %d | batch: %d | pause: %.1fs | dry_run: %s", total_csv, batch, pause, dry_run)

    total = len(reader)
    loaded = 0
    skipped = 0
    errors = 0

    for batch_start in range(0, total, batch):
        chunk = reader[batch_start: batch_start + batch]
        parsed_chunk: List[Dict[str, Any]] = []

        for raw in chunk:
            trade = _parse_row(raw)
            if trade is None:
                skipped += 1
            else:
                parsed_chunk.append(trade)

        if not parsed_chunk:
            continue

        if dry_run:
            loaded += len(parsed_chunk)
            done = batch_start + len(chunk)
            log.info("[dry-run] batch %d-%d | parsed=%d | total_loaded=%d/%d",
                     batch_start + 1, done, len(parsed_chunk), loaded, total)
            continue

        # ── get embeddings in ONE batch request to OpenAI ──
        texts = [brain.build_embedding_text(t) for t in parsed_chunk]
        try:
            from app.services.brain_service import _get_openai
            client = _get_openai()
            response = await client.embeddings.create(
                model=brain.EMBEDDING_MODEL,
                input=texts,
            )
            raw_embeddings = [item.embedding for item in response.data]
        except Exception as emb_err:
            log.error("Embedding batch failed: %s", emb_err)
            errors += len(parsed_chunk)
            continue

        valid_rows: List[Dict[str, Any]] = []
        valid_embs: List[str] = []
        for trade, emb in zip(parsed_chunk, raw_embeddings):
            valid_rows.append(trade)
            valid_embs.append("[" + ",".join(f"{v:.8f}" for v in emb) + "]")

        if not valid_rows:
            continue

        # ── single-transaction batch insert (sync, no async event loop blocking) ──
        loop = asyncio.get_event_loop()
        batch_inserted, batch_errors = await loop.run_in_executor(
            None, _insert_batch, db_url, valid_rows, valid_embs
        )

        loaded += batch_inserted
        errors += batch_errors

        done = batch_start + len(chunk)
        log.info(
            "Progress: %d/%d rows processed | +%d inserted | errors=%d | skipped=%d",
            done + offset, total_csv, batch_inserted, errors, skipped,
        )

        if done < total and pause > 0:
            await asyncio.sleep(pause)

    log.info("Done. Loaded: %d, Skipped: %d, Errors: %d", loaded, skipped, errors)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Load arb CSV dataset into brain_embeddings")
    parser.add_argument("--file", required=True, help="Path to CSV file")
    parser.add_argument("--batch", type=int, default=20,
                        help="Rows per async batch (default 20)")
    parser.add_argument("--pause", type=float, default=0.5,
                        help="Seconds to sleep between batches (default 0.5)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip first N rows — use to resume after interruption (default 0)")
    parser.add_argument("--auto-offset", action="store_true",
                        help="Auto-detect offset from DB COUNT(*) — safest resume option")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse CSV only — no embeddings, no DB inserts")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        log.error("File not found: %s", args.file)
        sys.exit(1)

    offset = -1 if args.auto_offset else args.offset

    asyncio.run(load(args.file, batch=args.batch, dry_run=args.dry_run, pause=args.pause, offset=offset))


if __name__ == "__main__":
    main()
