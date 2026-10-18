-- title: Better than guessing?
-- note: 1-day hit rates on the same ticker-days (score 4+, directional calls). If most stocks beat the market this period, always guessing bullish gets free hits; the scorer is only adding information if its range sits above the better baseline.
WITH days AS (
    SELECT sign, abn_1d
    FROM ticker_days
    WHERE version_status = 'active' AND score >= 4 AND sign IS NOT NULL AND abn_1d IS NOT NULL
),
rates AS (
    SELECT 1 AS ord, 'scorer''s calls' AS approach, count(*) AS n, count(*) FILTER (WHERE sign * abn_1d > 0) AS hits FROM days
    UNION ALL
    SELECT 2, 'always bullish', count(*), count(*) FILTER (WHERE abn_1d > 0) FROM days
    UNION ALL
    SELECT 3, 'always bearish', count(*), count(*) FILTER (WHERE abn_1d < 0) FROM days
)
SELECT
    approach,
    n AS ticker_days,
    round(100.0 * hits / nullif(n, 0)) AS hit_rate_1d,
    round(100 * wilson_low(hits, n)) || '–' || round(100 * wilson_high(hits, n)) AS range_1d
FROM rates
WHERE n > 0
ORDER BY ord;
