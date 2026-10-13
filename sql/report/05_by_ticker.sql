-- title: By ticker
-- note: Active rubric, all labeled data.
SELECT
    ticker,
    count(*) AS scores,
    round(avg(score)::numeric, 2) AS avg_score,
    count(*) FILTER (WHERE alerted) AS alerts,
    round((avg(abs(abn_1d)) * 100)::numeric, 2) AS avg_abs_move_1d,
    round((avg(abs(abn_1d)) FILTER (WHERE alerted) * 100)::numeric, 2) AS alerts_avg_abs_move_1d,
    round(100 * avg((sign * abn_1d > 0)::int) FILTER (WHERE alerted AND sign IS NOT NULL AND abn_1d IS NOT NULL), 0) AS alert_hit_rate_1d
FROM scored_outcomes
WHERE version_status = 'active'
GROUP BY ticker
ORDER BY alerts DESC, scores DESC;
