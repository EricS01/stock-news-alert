-- title: Options on alerts (paper)
-- note: Buy at the ask at entry, sell at the bid 1 or 3 trading days later; returns in percent. Direction = a call if bullish, a put if bearish. Straddle = call plus put. Both use the strike nearest the stock price.
WITH legs AS (
    SELECT e.item_id, e.ticker, e.tenor, e."right", e.ask, s.score, s.direction,
           d1.bid AS bid_d1, d3.bid AS bid_d3
    FROM option_entries e
    JOIN scored_outcomes s ON s.item_id = e.item_id AND s.ticker = e.ticker AND s.version_status = 'active'
    LEFT JOIN option_snapshots d1
           ON d1.item_id = e.item_id AND d1.ticker = e.ticker AND d1.contract_symbol = e.contract_symbol AND d1.kind = 'd1'
    LEFT JOIN option_snapshots d3
           ON d3.item_id = e.item_id AND d3.ticker = e.ticker AND d3.contract_symbol = e.contract_symbol AND d3.kind = 'd3'
    WHERE e.nearness = 1
),
positions AS (
    SELECT 'direction' AS strategy, tenor, score, item_id, ticker,
           sum(ask) AS cost, sum(bid_d1) AS value_d1, sum(bid_d3) AS value_d3,
           count(*) AS legs, count(bid_d1) AS legs_d1, count(bid_d3) AS legs_d3
    FROM legs
    WHERE ("right" = 'C' AND direction = 'bullish') OR ("right" = 'P' AND direction = 'bearish')
    GROUP BY tenor, score, item_id, ticker
    UNION ALL
    SELECT 'straddle', tenor, score, item_id, ticker,
           sum(ask), sum(bid_d1), sum(bid_d3), count(*), count(bid_d1), count(bid_d3)
    FROM legs
    GROUP BY tenor, score, item_id, ticker
    HAVING count(*) = 2
)
SELECT
    strategy,
    tenor,
    score,
    count(*) AS positions,
    count(*) FILTER (WHERE legs_d1 = legs) AS closed_d1,
    round((avg(value_d1 / cost - 1) FILTER (WHERE legs_d1 = legs AND cost > 0) * 100)::numeric, 1) AS avg_return_d1,
    count(*) FILTER (WHERE legs_d3 = legs) AS closed_d3,
    round((avg(value_d3 / cost - 1) FILTER (WHERE legs_d3 = legs AND cost > 0) * 100)::numeric, 1) AS avg_return_d3
FROM positions
GROUP BY strategy, tenor, score
ORDER BY strategy, tenor, score;
