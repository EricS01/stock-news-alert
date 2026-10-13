-- The one-line summary pushed to ntfy (not a report section).
SELECT
    (SELECT count(*) FROM scores WHERE scored_at >= %(since)s) AS scores,
    (SELECT count(*) FROM scores WHERE scored_at >= %(since)s AND alerted) AS alerts,
    round((avg(abs(abn_1d)) FILTER (WHERE score >= 4) * 100)::numeric, 2) AS high_move_1d,
    round((avg(abs(abn_1d)) FILTER (WHERE score <= 2) * 100)::numeric, 2) AS low_move_1d,
    round(100 * avg((sign * abn_1d > 0)::int) FILTER (WHERE alerted AND sign IS NOT NULL AND abn_1d IS NOT NULL), 0) AS alert_hit_rate_1d,
    count(abn_1d) AS labeled_1d
FROM scored_outcomes
WHERE version_status = 'active';
