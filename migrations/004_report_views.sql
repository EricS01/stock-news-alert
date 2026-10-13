-- Views the weekly report builds on. Views hold no data: each is a saved query
-- that runs whenever it's selected from, so it's always up to date.

-- Every score with its rubric, its news item, and what the stock did afterwards.
CREATE VIEW scored_outcomes AS
SELECT
    s.item_id,
    s.ticker,
    s.prompt_version,
    v.status AS version_status,
    s.score,
    s.direction,
    CASE s.direction WHEN 'bullish' THEN 1 WHEN 'bearish' THEN -1 END AS sign,  -- NULL if neutral
    s.alerted,
    s.scored_at,
    n.source,
    n.title,
    n.url,
    p.entry_ts,
    p.ret_1d, p.ret_3d, p.ret_5d,
    p.abn_1d, p.abn_3d, p.abn_5d,
    p.status AS outcome_status
FROM scores s
JOIN prompt_versions v ON v.id = s.prompt_version
JOIN news_items n ON n.id = s.item_id
LEFT JOIN price_outcomes p USING (item_id, ticker);

-- Entry option quotes with their tenor and how near the money each strike is
-- (nearness 1 = the strike closest to the stock price for that expiry).
CREATE VIEW option_entries AS
SELECT
    o.*,
    CASE WHEN o.expiry = min(o.expiry) OVER per_alert THEN 'weekly' ELSE 'monthly' END AS tenor,
    dense_rank() OVER (
        PARTITION BY o.item_id, o.ticker, o.expiry
        ORDER BY abs(o.strike - o.underlying_price), o.strike
    ) AS nearness
FROM option_snapshots o
WHERE o.kind = 'entry'
WINDOW per_alert AS (PARTITION BY o.item_id, o.ticker);
