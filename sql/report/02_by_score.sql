-- title: By score
-- note: Active rubric, all labeled data so far. Moves are abnormal returns (stock minus SPY) in percent. Hit = moved in the predicted direction (neutral calls excluded).
SELECT
    score,
    count(*) AS n,
    count(abn_1d) AS labeled_1d,
    round((avg(abs(abn_1d)) * 100)::numeric, 2) AS avg_abs_move_1d,
    round((avg(abs(abn_5d)) * 100)::numeric, 2) AS avg_abs_move_5d,
    round(100 * avg((sign * abn_1d > 0)::int) FILTER (WHERE sign IS NOT NULL AND abn_1d IS NOT NULL), 0) AS hit_rate_1d,
    round(100 * avg((sign * abn_5d > 0)::int) FILTER (WHERE sign IS NOT NULL AND abn_5d IS NOT NULL), 0) AS hit_rate_5d
FROM scored_outcomes
WHERE version_status = 'active'
GROUP BY score
ORDER BY score;
