-- title: Worst misses this week
-- note: False alarms = scored 4+ but barely moved. Missed movers = scored 1-2 but moved the most. 1-day abnormal move in percent.
(
    SELECT 'false alarm' AS kind, score, ticker, direction,
           round((abn_1d * 100)::numeric, 2) AS move_1d, source, left(title, 80) AS title
    FROM scored_outcomes
    WHERE version_status = 'active' AND score >= 4 AND abn_1d IS NOT NULL AND scored_at >= %(since)s
    ORDER BY abs(abn_1d)
    LIMIT 10
)
UNION ALL
(
    SELECT 'missed mover', score, ticker, direction,
           round((abn_1d * 100)::numeric, 2), source, left(title, 80)
    FROM scored_outcomes
    WHERE version_status = 'active' AND score <= 2 AND abn_1d IS NOT NULL AND scored_at >= %(since)s
    ORDER BY abs(abn_1d) DESC
    LIMIT 10
);
