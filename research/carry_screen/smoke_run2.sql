-- Smoke validation for carry depth RUN 2 (129 names, 50 levels, 120s).
-- Read-only. Pass :t0 as the run-2 start timestamp.
\set t0 '2026-08-19 04:06:20+00'

\echo '=== 1. COVERAGE: names, snapshots, rows per venue/market ==='
SELECT exchange, market,
       count(DISTINCT symbol)                        AS syms,
       count(DISTINCT ts)                            AS snaps,
       count(*)                                      AS rows,
       round(avg(lv))                                AS avg_levels_per_side
FROM (SELECT exchange, market, symbol, ts, side, count(*) lv
      FROM carry_book_l2 WHERE ts > :'t0'
      GROUP BY 1,2,3,4,5) s
GROUP BY 1,2 ORDER BY 1,2;

\echo ''
\echo '=== 2. NAMES MISSING either market (expect 0 rows) ==='
SELECT b.exchange, b.symbol,
       count(*) FILTER (WHERE c.market='perp') AS perp,
       count(*) FILTER (WHERE c.market='spot') AS spot
FROM (SELECT DISTINCT exchange, symbol FROM carry_book_l2 WHERE ts > :'t0') b
LEFT JOIN carry_book_l2 c
       ON c.exchange=b.exchange AND c.symbol=b.symbol AND c.ts > :'t0'
GROUP BY 1,2 HAVING count(*) FILTER (WHERE c.market='perp')=0
                OR count(*) FILTER (WHERE c.market='spot')=0
ORDER BY 1,2;

\echo ''
\echo '=== 3. BOTH SIDES present per snapshot (expect 0 one-sided) ==='
SELECT market, count(*) AS one_sided_snapshots FROM (
  SELECT exchange, symbol, market, ts
  FROM carry_book_l2 WHERE ts > :'t0'
  GROUP BY 1,2,3,4 HAVING count(DISTINCT side) < 2) x
GROUP BY 1;

\echo ''
\echo '=== 4. CROSSED BOOKS: bid1 >= ask1 (expect 0) ==='
WITH touch AS (
  SELECT exchange, symbol, market, ts,
         max(price) FILTER (WHERE side='bid' AND level=1) AS bid1,
         min(price) FILTER (WHERE side='ask' AND level=1) AS ask1
  FROM carry_book_l2 WHERE ts > :'t0' AND level=1
  GROUP BY 1,2,3,4)
SELECT market, count(*) AS crossed
FROM touch WHERE bid1 IS NOT NULL AND ask1 IS NOT NULL AND bid1 >= ask1
GROUP BY 1;

\echo ''
\echo '=== 5. LEVEL ORDERING: price monotone away from touch (expect 0 bad) ==='
WITH ord AS (
  SELECT exchange, symbol, market, side, ts, level, price,
         lag(price) OVER (PARTITION BY exchange,symbol,market,side,ts ORDER BY level) AS prev
  FROM carry_book_l2 WHERE ts > :'t0')
SELECT market, side, count(*) AS misordered_levels
FROM ord
WHERE prev IS NOT NULL
  AND ((side='bid' AND price > prev) OR (side='ask' AND price < prev))
GROUP BY 1,2 ORDER BY 1,2;

\echo ''
\echo '=== 6. size_usd SANITY: nulls, non-positive, and the value distribution ==='
SELECT market,
       count(*) FILTER (WHERE size_usd IS NULL)  AS null_usd,
       count(*) FILTER (WHERE size_usd <= 0)     AS nonpos_usd,
       round(min(size_usd)::numeric, 4)          AS min_usd,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY size_usd)::numeric, 2) AS med_usd,
       round(max(size_usd)::numeric, 0)          AS max_usd
FROM carry_book_l2 WHERE ts > :'t0' GROUP BY 1;

\echo ''
\echo '=== 7. TOUCH SPREAD sanity per venue/market (bps; absurd = bad units) ==='
WITH touch AS (
  SELECT exchange, market, symbol, ts,
         max(price) FILTER (WHERE side='bid' AND level=1) AS bid1,
         min(price) FILTER (WHERE side='ask' AND level=1) AS ask1
  FROM carry_book_l2 WHERE ts > :'t0' AND level=1
  GROUP BY 1,2,3,4)
SELECT exchange, market,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY 1e4*(ask1-bid1)/((ask1+bid1)/2))::numeric, 1) AS median_spread_bps,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY 1e4*(ask1-bid1)/((ask1+bid1)/2))::numeric, 1) AS p95_spread_bps
FROM touch WHERE bid1 > 0 AND ask1 > bid1
GROUP BY 1,2 ORDER BY 1,2;
