-- title: Implied vs realized move
-- note: Implied = weekly straddle cost at entry divided by the stock price. Realized = the stock's actual move over 3 trading days. Beat = realized exceeded implied (straddle buyers would have done well).
WITH straddles AS (
    SELECT item_id, ticker, sum(ask) / max(underlying_price) AS implied
    FROM option_entries
    WHERE tenor = 'weekly' AND nearness = 1
    GROUP BY item_id, ticker
    HAVING count(*) = 2
)
SELECT
    s.score,
    count(*) AS alerts,
    round((avg(st.implied) * 100)::numeric, 2) AS avg_implied_move,
    round((avg(abs(s.ret_3d)) * 100)::numeric, 2) AS avg_realized_move_3d,
    round(100 * avg((abs(s.ret_3d) > st.implied)::int), 0) AS beat_rate
FROM straddles st
JOIN scored_outcomes s USING (item_id, ticker)
WHERE s.version_status = 'active' AND s.ret_3d IS NOT NULL
GROUP BY s.score
ORDER BY s.score;
