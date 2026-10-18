-- Count events, not articles, and record why an alert wasn't pushed.

-- Why a score at or above the threshold wasn't pushed (NULL if pushed or below it):
-- 'duplicate' = same story already pushed for this ticker; 'cooldown' = the ticker
-- already had an alert in this direction with an equal or higher score recently.
ALTER TABLE scores
    ADD COLUMN suppressed text CHECK (suppressed IN ('duplicate', 'cooldown'));

-- 95% Wilson score interval for a proportion (hits out of n). Unlike the plain
-- p ± 1.96·√(p(1-p)/n), it stays inside 0..1 and behaves for small n.
CREATE FUNCTION wilson_low(hits numeric, n numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN n > 0 THEN
        ((hits / n) + 1.9208 / n - 1.96 * sqrt((hits / n) * (1 - hits / n) / n + 0.9604 / (n * n)))
        / (1 + 3.8416 / n)
    END
$$;

CREATE FUNCTION wilson_high(hits numeric, n numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN n > 0 THEN
        ((hits / n) + 1.9208 / n + 1.96 * sqrt((hits / n) * (1 - hits / n) / n + 0.9604 / (n * n)))
        / (1 + 3.8416 / n)
    END
$$;

-- One row per rubric, ticker and trading day (of entry): the unit of analysis.
-- A stock moves once a day however many articles cover it, so each article is not
-- an independent observation. The day is represented by its highest-scored
-- article (earliest on ties), whose call and outcome count once.
CREATE VIEW ticker_days AS
WITH ranked AS (
    SELECT
        so.*,
        (so.entry_ts AT TIME ZONE 'America/New_York')::date AS entry_day,
        row_number() OVER day_window AS rn,
        count(*) OVER (PARTITION BY so.prompt_version, so.ticker, (so.entry_ts AT TIME ZONE 'America/New_York')::date) AS articles,
        bool_or(so.alerted) OVER (PARTITION BY so.prompt_version, so.ticker, (so.entry_ts AT TIME ZONE 'America/New_York')::date) AS any_alert
    FROM scored_outcomes so
    WHERE so.entry_ts IS NOT NULL
    WINDOW day_window AS (
        PARTITION BY so.prompt_version, so.ticker, (so.entry_ts AT TIME ZONE 'America/New_York')::date
        ORDER BY so.score DESC, so.entry_ts, so.scored_at, so.item_id
    )
)
SELECT
    prompt_version, version_status, ticker, entry_day, articles, any_alert AS alerted,
    item_id, score, direction, sign, source, title, url, scored_at, entry_ts,
    ret_1d, ret_3d, ret_5d, abn_1d, abn_3d, abn_5d
FROM ranked
WHERE rn = 1;
