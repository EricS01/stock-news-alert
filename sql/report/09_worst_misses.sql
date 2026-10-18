-- title: Worst misses this week
-- note: Per ticker-day. False alarms = the day's top score was 4+ but the stock barely moved. Missed movers = nothing scored above 2 but the stock moved the most. 1-day abnormal move in percent.
(
    SELECT 'false alarm' AS kind, entry_day, ticker, score, direction,
           round((abn_1d * 100)::numeric, 2) AS move_1d, articles, left(title, 80) AS top_article
    FROM ticker_days
    WHERE version_status = 'active' AND score >= 4 AND abn_1d IS NOT NULL AND scored_at >= %(since)s
    ORDER BY abs(abn_1d)
    LIMIT 10
)
UNION ALL
(
    SELECT 'missed mover', entry_day, ticker, score, direction,
           round((abn_1d * 100)::numeric, 2), articles, left(title, 80)
    FROM ticker_days
    WHERE version_status = 'active' AND score <= 2 AND abn_1d IS NOT NULL AND scored_at >= %(since)s
    ORDER BY abs(abn_1d) DESC
    LIMIT 10
);
