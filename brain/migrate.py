"""
brain/migrate.py
Миграция pgvector данных с Railway PostgreSQL → Neon brain-vectors

Запуск:
    python brain/migrate.py \
        --source "postgresql://user:pass@railway-host/dbname" \
        --target "postgresql://neondb_owner:pass@ep-xxx.neon.tech/brain_vectors?sslmode=require"
"""
from __future__ import annotations

import argparse
import sys
import psycopg2
import psycopg2.extras


def migrate(source_url: str, target_url: str) -> None:
    print(f"[migrate] Connecting to source (Railway)...")
    src = psycopg2.connect(source_url)
    src_cur = src.cursor(cursor_factory=psycopg2.extras.DictCursor)

    print(f"[migrate] Connecting to target (Neon)...")
    tgt = psycopg2.connect(target_url)
    tgt_cur = tgt.cursor()

    # ── Найди таблицы с pgvector в source ────────────────────────────────────
    src_cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [r[0] for r in src_cur.fetchall()]
    print(f"[migrate] Source tables: {tables}")

    # ── Мигрируй каждую таблицу ───────────────────────────────────────────────
    migrated_total = 0
    for table in tables:
        try:
            src_cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = src_cur.fetchone()[0]
            if count == 0:
                print(f"[migrate] {table}: empty, skipping")
                continue

            print(f"[migrate] {table}: {count} rows...")
            src_cur.execute(f"SELECT * FROM {table}")
            rows = src_cur.fetchall()
            cols = [desc[0] for desc in src_cur.description]

            # Пропускаем id и created_at (авто-генерируются)
            insert_cols = [c for c in cols if c not in ('id',)]
            placeholders = ','.join(['%s'] * len(insert_cols))
            col_str = ','.join(insert_cols)

            inserted = 0
            for row in rows:
                row_dict = dict(zip(cols, row))
                values = [row_dict[c] for c in insert_cols]
                try:
                    tgt_cur.execute(
                        f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
                        f"ON CONFLICT DO NOTHING",
                        values
                    )
                    inserted += 1
                except Exception as e:
                    print(f"  [warn] Row skip: {e}")

            tgt.commit()
            print(f"[migrate] {table}: {inserted}/{count} rows migrated ✓")
            migrated_total += inserted

        except Exception as e:
            print(f"[migrate] {table}: ERROR — {e}")
            tgt.rollback()

    src.close()
    tgt.close()
    print(f"\n[migrate] Done. Total migrated: {migrated_total} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate pgvector Railway → Neon")
    parser.add_argument("--source", required=True, help="Railway PostgreSQL URL")
    parser.add_argument("--target", required=True, help="Neon brain-vectors URL")
    args = parser.parse_args()
    migrate(args.source, args.target)
