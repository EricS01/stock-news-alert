-- The summary pushed to ntfy (not a report section). Per ticker-day, score 4+.
WITH calls AS (
    SELECT count(*) FILTER (WHERE sign IS NOT NULL) AS n,
           count(*) FILTER (WHERE sign * abn_1d > 0) AS hits
    FROM ticker_days
    WHERE version_status = 'active' AND score >= 4 AND abn_1d IS NOT NULL
)
SELECT
    (SELECT count(*) FROM scores WHERE scored_at >= %(since)s) AS scores,
    (SELECT count(*) FROM scores WHERE scored_at >= %(since)s AND alerted) AS pushed,
    (SELECT count(*) FROM ticker_days WHERE version_status = 'active' AND abn_1d IS NOT NULL) AS labeled_days,
    (SELECT round((avg(abs(abn_1d)) FILTER (WHERE score >= 4) * 100)::numeric, 2)
       FROM ticker_days WHERE version_status = 'active') AS high_move_1d,
    (SELECT round((avg(abs(abn_1d)) FILTER (WHERE score <= 2) * 100)::numeric, 2)
       FROM ticker_days WHERE version_status = 'active') AS low_move_1d,
    calls.n AS calls,
    round(100.0 * calls.hits / nullif(calls.n, 0)) AS hit_rate_1d,
    round(100 * wilson_low(calls.hits, calls.n)) AS hit_low,
    round(100 * wilson_high(calls.hits, calls.n)) AS hit_high
FROM calls;
