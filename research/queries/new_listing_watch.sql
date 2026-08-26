-- NEW-LISTING WATCH (prompt-57a item 3) — READ-ONLY, run whenever.
--
-- WHY THIS EXISTS: the listing-obligation hypothesis ("synthetic volume starts at
-- listing") can NEVER be tested on FRONG or on anything listed before 2026-08-19:
-- tape collection began 2026-08-13 and reported-volume persistence began
-- 2026-08-19T12:59:25Z, with NO BACKFILL. A previous session concluded the
-- hypothesis was REFUTED on the basis of a tape gap that was purely an artifact of
-- when we started collecting. It is UNTESTED, not refuted.
--
-- Any perp listing AFTER 2026-08-19T12:59:25Z arrives with full volume coverage from
-- its very first cycle, which makes it the only clean test available.
--
-- Read the three markers together:
--   pinned_at_floor  : mean reported 24h volume in $95k-$110k (the MEXC floor band)
--   hourly_flatness  : max/min hourly volume ratio. A constant-rate emitter is ~1.
--                      Organic flow ran 2.4-6.9% of day per hour, i.e. ratio ~2.9.
--   from_day_one     : whether those hold in the symbol's FIRST 24h, or only later.

WITH first_seen AS (
    SELECT exchange, symbol, min(ts) AS t0, max(ts) AS t1, count(*) AS snaps
    FROM funding_basis_snapshots
    GROUP BY 1,2
    HAVING min(ts) > TIMESTAMPTZ '2026-08-19 12:59:25+00'   -- collector-fix boundary
),
vol AS (
    SELECT f.exchange, f.symbol, f.ts, f.perp_volume24_usd AS v,
           date_trunc('hour', f.ts) AS hr,
           (f.ts < fs.t0 + INTERVAL '24 hours') AS in_first_24h
    FROM funding_basis_snapshots f
    JOIN first_seen fs USING (exchange, symbol)
    WHERE f.perp_volume24_usd > 0
),
hourly AS (
    SELECT exchange, symbol, hr, in_first_24h, avg(v) AS hv
    FROM vol GROUP BY 1,2,3,4
)
SELECT fs.exchange, fs.symbol,
       fs.t0                                             AS first_seen,
       round(EXTRACT(epoch FROM (fs.t1 - fs.t0))/3600.0) AS hours_observed,
       round(avg(h.hv))                                  AS mean_reported_24h_usd,
       (avg(h.hv) BETWEEN 95000 AND 110000)              AS pinned_at_floor,
       round((max(h.hv)/NULLIF(min(h.hv),0))::numeric, 3) AS hourly_flatness,
       round(avg(h.hv) FILTER (WHERE h.in_first_24h))    AS mean_first_24h_usd,
       count(*) FILTER (WHERE h.in_first_24h)            AS hours_in_first_24h
FROM first_seen fs
JOIN hourly h ON h.exchange = fs.exchange AND h.symbol = fs.symbol
GROUP BY 1,2,3,4
ORDER BY fs.t0;
