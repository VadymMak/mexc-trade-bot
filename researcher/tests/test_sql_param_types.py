#!/usr/bin/env python3
"""Sweep the DEFECT CLASS, not the instance.

    usage:  CARRY_DSN=... researcher/.venv/bin/python tests/test_sql_param_types.py

FIXING AN INSTANCE OF A DEFECT CLASS IS NOT FIXING THE CLASS. Runtime-dependent
parameter typing has now appeared three times in this project, twice in the SAME
SQL STATEMENT:

  1. 2026-09-04  `basis_pnl_usd = CASE ... THEN $10 ELSE 0 END` — the `0` literal
     made Postgres infer INTEGER and SILENTLY truncate every sub-dollar figure
     to zero. Prepared fine. Caught only by a two-way reconciliation.
  2. 2026-09-14  `close_price = CASE WHEN leg='spot' THEN $5 ELSE $6 END` — both
     arms untyped, both values NULL, so Postgres had no anchor, inferred TEXT
     and RAISED. Broke every exit for nine days. The comment explaining (1) sat
     two lines below it.
  3. The same shape had already been swept once for interval units, where
     sweeping turned up a fourth site in `selector.evaluate` that fixing the
     instance would have missed.

So this test asks POSTGRES what it infers for every parameter in the codebase,
rather than anyone reasoning about it, and fails on either failure mode:

  HARD  — the statement cannot PREPARE (type mismatch). Value-independent:
          if it prepares, no input can make it raise this way.
  SILENT— a parameter is inferred NARROWER than the column it is written into
          (int into double precision), which truncates without an error.
"""
from __future__ import annotations

import ast
import asyncio
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# inferred type -> column type pairs that lose information
LOSSY = {("int2", "double precision"), ("int4", "double precision"),
         ("int8", "double precision"), ("int2", "real"), ("int4", "real"),
         ("int8", "real"), ("numeric", "double precision"),
         ("int4", "bigint"), ("int2", "integer")}
TEXTISH = ("text", "character varying", "jsonb", "USER-DEFINED")

SCAN = ["app/carry/bot/*.py", "app/carry/*.py"]


def sql_literals(path, verbs=r"SELECT|INSERT|UPDATE|DELETE"):
    tree = ast.parse(open(path, encoding="utf-8").read())
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "$1" in n.value and re.search(rf"\b({verbs})\b", n.value, re.I)]


def write_targets(sql):
    """{param_index: (table, column)} for INSERT VALUES and UPDATE SET.

    Explicitly cast parameters (`$n::type`) are skipped on purpose: a cast
    parameter is by definition not inferred, which is the whole remedy.
    """
    out, flat = {}, " ".join(sql.split())
    m = re.search(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", flat, re.I)
    if m:
        for col, val in zip([c.strip() for c in m.group(2).split(",")],
                            [v.strip() for v in m.group(3).split(",")]):
            p = re.fullmatch(r"\$(\d+)", val)
            if p:
                out[int(p.group(1))] = (m.group(1), col)
        return out
    m = re.search(r"UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\s+WHERE\s|$)", flat, re.I)
    if m:
        depth, cur, parts = 0, "", []
        for ch in m.group(2):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur); cur = ""
            else:
                cur += ch
        parts.append(cur)
        for part in parts:
            a = part.split("=", 1)
            if len(a) != 2:
                continue
            for p in re.findall(r"\$(\d+)(?!::)", a[1]):
                out.setdefault(int(p), (m.group(1), a[0].strip()))
    return out


async def run():
    try:
        import asyncpg
    except ImportError:
        print("  SKIP  (asyncpg unavailable)")
        return
    dsn = (os.getenv("CARRY_DSN") or os.getenv("NEON_DATABASE_URL")
           or os.getenv("DATABASE_URL"))
    if not dsn:
        env = os.path.join(ROOT, ".env")
        if os.path.exists(env):
            m = re.search(r"^NEON_DATABASE_URL=(.*)$", open(env).read(), re.M)
            dsn = m.group(1).strip() if m else None
    if not dsn:
        print("  SKIP  (no CARRY_DSN/NEON_DATABASE_URL)")
        return

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        coltypes = {(r["table_name"], r["column_name"]): r["data_type"]
                    for r in await conn.fetch(
                        "SELECT table_name, column_name, data_type "
                        "FROM information_schema.columns WHERE table_schema='public'")}

        files = []
        for pat in SCAN:
            files += sorted(glob.glob(os.path.join(ROOT, pat)))

        unpreparable, lossy, untyped, n_stmt, n_pair = [], [], [], 0, 0
        for path in files:
            rel = os.path.relpath(path, ROOT)
            for lineno, sql in sql_literals(path):
                n_stmt += 1
                try:
                    st = await conn.prepare(sql)
                except Exception as exc:
                    unpreparable.append(f"{rel}:{lineno} {type(exc).__name__}: {exc}")
                    continue
                ptypes = [p.name for p in st.get_parameters()]
                for idx, (tbl, col) in write_targets(sql).items():
                    ct = coltypes.get((tbl, col))
                    if ct is None or idx > len(ptypes):
                        continue
                    n_pair += 1
                    inferred = ptypes[idx - 1]
                    if (inferred, ct) in LOSSY:
                        lossy.append(f"{rel}:{lineno} ${idx} -> {tbl}.{col}: "
                                     f"{inferred} into {ct}")
                    elif inferred in ("text", "unknown") and ct not in TEXTISH:
                        untyped.append(f"{rel}:{lineno} ${idx} -> {tbl}.{col}: "
                                       f"{inferred} into {ct}")

        print(f"    swept {n_stmt} parameterised statements, "
              f"{n_pair} parameter->column pairs")
        check("every statement PREPARES (no DatatypeMismatchError possible)",
              not unpreparable, "; ".join(unpreparable) or f"{n_stmt} ok")
        check("no parameter is inferred NARROWER than its column (no silent truncation)",
              not lossy, "; ".join(lossy) or "none")
        check("no parameter lands untyped in a non-text column",
              not untyped, "; ".join(untyped) or "none")

        # THE CHECK MUST BE ABLE TO FAIL. Reintroduce the exact 2026-09-14 bug
        # and prove the sweep catches it.
        broken = ("UPDATE paper_carry_positions SET close_price = "
                  "CASE WHEN leg='spot' THEN $1 ELSE $2 END WHERE false")
        caught = False
        try:
            await conn.prepare(broken)
        except asyncpg.exceptions.DatatypeMismatchError:
            caught = True
        check("the sweep DOES catch the original defect when reintroduced",
              caught, "DatatypeMismatchError on the untyped CASE")
    finally:
        await conn.close()


def main() -> int:
    print("SQL parameter typing — sweeping the class, not the instance")
    asyncio.run(run())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
