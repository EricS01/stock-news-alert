-- title: By source
-- note: Active rubric, all labeled data, per ticker-day (attributed to the source of the day's top article). Alerted days = days with a pushed alert; their hit rate is over 1 day.
SELECT
    source,
    count(*) AS ticker_days,
    sum(articles) AS articles,
    round(avg(score)::numeric, 2) AS avg_score,
    count(*) FILTER (WHERE alerted) AS alerted_days,
    round((avg(abs(abn_1d)) * 100)::numeric, 2) AS avg_abs_move_1d,
    round((avg(abs(abn_1d)) FILTER (WHERE alerted) * 100)::numeric, 2) AS alerted_avg_abs_move_1d,
    round(100 * avg((sign * abn_1d > 0)::int) FILTER (WHERE alerted AND sign IS NOT NULL AND abn_1d IS NOT NULL), 0) AS alerted_hit_rate_1d
FROM ticker_days
WHERE version_status = 'active'
GROUP BY source
ORDER BY ticker_days DESC;
