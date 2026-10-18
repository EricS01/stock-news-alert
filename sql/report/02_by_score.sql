-- title: By score
-- note: Active rubric, all labeled data. Counted per ticker-day (one stock on one trading day, represented by its highest-scored article), because a stock moves once however many articles cover it. Moves are abnormal returns (stock minus SPY) in percent. Hit = moved in the predicted direction; neutral calls excluded. Range = 95 percent confidence interval.
SELECT
    score,
    count(*) AS ticker_days,
    sum(articles) AS articles,
    round((avg(abs(abn_1d)) * 100)::numeric, 2) AS avg_abs_move_1d,
    round((avg(abs(abn_5d)) * 100)::numeric, 2) AS avg_abs_move_5d,
    count(*) FILTER (WHERE sign IS NOT NULL AND abn_1d IS NOT NULL) AS calls_1d,
    round(100 * avg((sign * abn_1d > 0)::int) FILTER (WHERE sign IS NOT NULL AND abn_1d IS NOT NULL), 0) AS hit_rate_1d,
    round(100 * wilson_low(count(*) FILTER (WHERE sign * abn_1d > 0),
                           count(*) FILTER (WHERE sign IS NOT NULL AND abn_1d IS NOT NULL))) || '–' ||
    round(100 * wilson_high(count(*) FILTER (WHERE sign * abn_1d > 0),
                            count(*) FILTER (WHERE sign IS NOT NULL AND abn_1d IS NOT NULL))) AS range_1d,
    round(100 * avg((sign * abn_5d > 0)::int) FILTER (WHERE sign IS NOT NULL AND abn_5d IS NOT NULL), 0) AS hit_rate_5d
FROM ticker_days
WHERE version_status = 'active' AND abn_1d IS NOT NULL
GROUP BY score
ORDER BY score;
