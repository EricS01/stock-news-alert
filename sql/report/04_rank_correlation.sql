-- title: Does a higher score mean a bigger move?
-- note: Spearman rank correlation between score and the size of the abnormal move (1 = perfect, 0 = none). Tied values get their average rank.
WITH moves AS (
    SELECT '1 day' AS horizon, score, abs(abn_1d) AS move
    FROM scored_outcomes WHERE version_status = 'active' AND abn_1d IS NOT NULL
    UNION ALL
    SELECT '5 days', score, abs(abn_5d)
    FROM scored_outcomes WHERE version_status = 'active' AND abn_5d IS NOT NULL
),
ranked AS (
    SELECT
        horizon,
        rank() OVER (PARTITION BY horizon ORDER BY score)
            + (count(*) OVER (PARTITION BY horizon, score) - 1) / 2.0 AS score_rank,
        rank() OVER (PARTITION BY horizon ORDER BY move)
            + (count(*) OVER (PARTITION BY horizon, move) - 1) / 2.0 AS move_rank
    FROM moves
)
SELECT horizon, count(*) AS n, round(corr(score_rank, move_rank)::numeric, 3) AS spearman_rho
FROM ranked
GROUP BY horizon
ORDER BY horizon;
