"""
Brain Service — semantic memory for trade pattern recognition.

Converts market-condition snapshots to OpenAI text-embedding-3-small vectors
and stores/queries them via pgvector to validate scanner entries.

Usage:
    brain = get_brain_service()
    result = await brain.validate_entry(
        session="europe", hour_utc=10, is_weekend=False,
        entry_mode="zscore", spread_pct=0.0005, zscore=2.1,
        buy_pressure=0.62, book_imbalance=0.58,
    )
    # result: { multiplier, verdict, win_rate, similar_count, confidence }
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# ── Brain DB session (Neon PostgreSQL, separate from main SQLite engine) ─────

def _make_brain_session() -> sessionmaker:
    """
    Create a SQLAlchemy sessionmaker pointed at NEON_DATABASE_URL.
    Falls back to DATABASE_URL if Neon URL is not set (local dev).
    Raises RuntimeError at import time if no usable Postgres URL is found.
    """
    url = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("NEON_DATABASE_URL (or DATABASE_URL) env var is not set")
    # Railway sometimes gives postgres://, SQLAlchemy needs postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("sqlite"):
        raise RuntimeError(
            "BrainService requires PostgreSQL (pgvector). "
            "Set NEON_DATABASE_URL to a Neon/Postgres connection string."
        )
    _engine = create_engine(url, pool_pre_ping=True, future=True)
    return sessionmaker(bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False)


_BrainSessionLocal: Optional[sessionmaker] = None


def _get_brain_session() -> sessionmaker:
    global _BrainSessionLocal
    if _BrainSessionLocal is None:
        _BrainSessionLocal = _make_brain_session()
    return _BrainSessionLocal


# ── OpenAI client (lazy, singleton) ─────────────────────────────────────────

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai>=1.0.0"
            )
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY env var is not set")
        _openai_client = AsyncOpenAI(api_key=api_key)
    return _openai_client


# ── Session helper ───────────────────────────────────────────────────────────

def _get_trading_session(hour_utc: int) -> str:
    """Map UTC hour to named trading session."""
    if 0 <= hour_utc < 8:
        return "asia"
    if 8 <= hour_utc < 16:
        return "europe"
    return "us"


# ── pgvector formatting ──────────────────────────────────────────────────────

def _emb_to_pg(embedding: List[float]) -> str:
    """Format Python list as pgvector literal '[v1,v2,…]'."""
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


# ── BrainService ─────────────────────────────────────────────────────────────

class BrainService:
    """
    Semantic entry validator backed by pgvector similarity search.

    Workflow:
      1. build_embedding_text(row) → compact text representation
      2. create_embedding(text) → 1536-dim float list via OpenAI
      3. validate_entry(…) → multiplier + verdict for scanner score
    """

    EMBEDDING_MODEL = "text-embedding-3-small"
    MIN_SIMILARITY = 0.70      # cosine similarity threshold
    MIN_ROWS_FOR_SIGNAL = 5    # fewer → low confidence, multiplier=1.0
    HIGH_CONFIDENCE_ROWS = 15  # >= this → 'high' confidence label

    # ── text builder ─────────────────────────────────────────────────────────

    def build_embedding_text(self, row: Dict[str, Any]) -> str:
        """
        Build compact, space-separated feature string for embedding.
        Example: 'session=europe hour=10 weekend=0 mode=zscore
                  spread=0.0005 zscore=2.100 buy_pressure=0.620
                  imbalance=0.580 funding_mins=45.0'
        """
        parts: List[str] = []

        if row.get("trading_session"):
            parts.append(f"session={row['trading_session']}")
        if row.get("hour_utc") is not None:
            parts.append(f"hour={row['hour_utc']}")
        if row.get("is_weekend") is not None:
            parts.append(f"weekend={int(bool(row['is_weekend']))}")
        if row.get("entry_mode"):
            parts.append(f"mode={row['entry_mode']}")
        if row.get("entry_spread_pct"):
            parts.append(f"spread={row['entry_spread_pct']:.4f}")
        if row.get("entry_zscore") is not None:
            parts.append(f"zscore={row['entry_zscore']:.3f}")
        if row.get("buy_pressure") is not None:
            parts.append(f"buy_pressure={row['buy_pressure']:.3f}")
        if row.get("book_imbalance") is not None:
            parts.append(f"imbalance={row['book_imbalance']:.3f}")
        if row.get("mins_to_funding") is not None:
            parts.append(f"funding_mins={row['mins_to_funding']:.1f}")
        if row.get("spread_mean") is not None:
            parts.append(f"spread_mean={row['spread_mean']:.4f}")
        if row.get("spread_std") is not None:
            parts.append(f"spread_std={row['spread_std']:.4f}")
        if row.get("trade_velocity") is not None:
            parts.append(f"velocity={row['trade_velocity']:.2f}")

        return " ".join(parts)

    # ── embedding ────────────────────────────────────────────────────────────

    async def create_embedding(self, text: str) -> List[float]:
        """Call OpenAI API to get 1536-dim embedding vector."""
        client = _get_openai()
        response = await client.embeddings.create(
            model=self.EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding

    # ── storage ──────────────────────────────────────────────────────────────

    async def store_trade(self, trade_data: Dict[str, Any]) -> bool:
        """
        Build embedding for a completed trade and insert to brain_embeddings.
        Returns True on success.
        """
        try:
            text_repr = self.build_embedding_text(trade_data)
            embedding = await self.create_embedding(text_repr)
            emb_str = _emb_to_pg(embedding)

            db = _get_brain_session()()
            try:
                db.execute(
                    text("""
                        INSERT INTO brain_embeddings (
                            symbol, session, hour_utc, day_of_week, is_weekend,
                            entry_mode, entry_spread_pct, entry_zscore,
                            spread_mean, spread_std, buy_pressure,
                            trade_velocity, book_imbalance, mins_to_funding,
                            exit_reason, hold_seconds, pnl_pct,
                            net_pnl_usdt, profitable, scan_embedding
                        ) VALUES (
                            :symbol, :session, :hour_utc, :day_of_week, :is_weekend,
                            :entry_mode, :entry_spread_pct, :entry_zscore,
                            :spread_mean, :spread_std, :buy_pressure,
                            :trade_velocity, :book_imbalance, :mins_to_funding,
                            :exit_reason, :hold_seconds, :pnl_pct,
                            :net_pnl_usdt, :profitable, :emb::vector
                        )
                    """),
                    {**trade_data, "emb": emb_str},
                )
                db.commit()
                return True
            finally:
                db.close()

        except Exception as e:
            logger.error(f"BrainService.store_trade failed: {e}")
            return False

    # ── validation ───────────────────────────────────────────────────────────

    async def validate_entry(
        self,
        session: str,
        hour_utc: int,
        is_weekend: bool,
        entry_mode: str,
        spread_pct: float,
        zscore: Optional[float] = None,
        buy_pressure: Optional[float] = None,
        book_imbalance: Optional[float] = None,
        mins_to_funding: Optional[float] = None,
        spread_mean: Optional[float] = None,
        spread_std: Optional[float] = None,
        trade_velocity: Optional[float] = None,
        top_k: int = 20,
    ) -> Dict[str, Any]:
        """
        Query similar historical trades and return a score multiplier.

        Returns:
            {
                multiplier: float,      # apply to scanner score
                verdict: str,           # 'strong_entry' | 'neutral' | 'avoid'
                win_rate: float | None,
                similar_count: int,
                confidence: str,        # 'low' | 'medium' | 'high'
            }
        """
        _neutral = {
            "multiplier": 1.0,
            "verdict": "neutral",
            "win_rate": None,
            "similar_count": 0,
            "confidence": "low",
        }

        try:
            row = {
                "trading_session": session,
                "hour_utc": hour_utc,
                "is_weekend": is_weekend,
                "entry_mode": entry_mode,
                "entry_spread_pct": spread_pct,
                "entry_zscore": zscore,
                "buy_pressure": buy_pressure,
                "book_imbalance": book_imbalance,
                "mins_to_funding": mins_to_funding,
                "spread_mean": spread_mean,
                "spread_std": spread_std,
                "trade_velocity": trade_velocity,
            }
            text_repr = self.build_embedding_text(row)
            embedding = await self.create_embedding(text_repr)
            emb_str = _emb_to_pg(embedding)

        except Exception as e:
            logger.warning(f"BrainService: embedding creation failed: {e}")
            return _neutral

        try:
            db = _get_brain_session()()
            try:
                result = db.execute(
                    text("""
                        SELECT profitable, net_pnl_usdt, exit_reason, session,
                               1 - (scan_embedding <=> :emb::vector) AS similarity
                        FROM brain_embeddings
                        WHERE 1 - (scan_embedding <=> :emb::vector) > :min_sim
                        ORDER BY scan_embedding <=> :emb::vector
                        LIMIT :top_k
                    """),
                    {
                        "emb": emb_str,
                        "min_sim": self.MIN_SIMILARITY,
                        "top_k": top_k,
                    },
                )
                rows = result.fetchall()
            finally:
                db.close()

        except Exception as e:
            logger.warning(f"BrainService: DB query failed: {e}")
            return _neutral

        n = len(rows)
        _neutral["similar_count"] = n

        if n < self.MIN_ROWS_FOR_SIGNAL:
            return _neutral

        profitable = [r for r in rows if r.profitable]
        win_rate = len(profitable) / n

        if win_rate >= 0.70:
            multiplier = 1.3
            verdict = "strong_entry"
        elif win_rate <= 0.35:
            multiplier = 0.5
            verdict = "avoid"
        else:
            multiplier = 1.0
            verdict = "neutral"

        confidence = "high" if n >= self.HIGH_CONFIDENCE_ROWS else "medium"

        logger.debug(
            f"Brain: {session}@{hour_utc}h {entry_mode} spread={spread_pct:.4f} "
            f"→ {verdict} wr={win_rate:.0%} n={n} mult={multiplier}"
        )

        return {
            "multiplier": multiplier,
            "verdict": verdict,
            "win_rate": win_rate,
            "similar_count": n,
            "confidence": confidence,
        }


# ── Singleton ────────────────────────────────────────────────────────────────

_brain_service: Optional[BrainService] = None


def get_brain_service() -> BrainService:
    """Return module-level singleton BrainService."""
    global _brain_service
    if _brain_service is None:
        _brain_service = BrainService()
    return _brain_service


def is_brain_enabled() -> bool:
    """Check if brain integration is enabled via BRAIN_ENABLED env var."""
    return os.getenv("BRAIN_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
