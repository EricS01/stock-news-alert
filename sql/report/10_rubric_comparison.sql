-- title: Rubric comparison
-- note: Active and shadow rubrics on the same articles (only those every rubric scored), per ticker-day. A better rubric gives 4+ to fewer days, and those days move more, and in the predicted direction more often, than the rest. Promote a shadow only when its hit-rate range clearly beats the active rubric and the baseline, on several weeks of data.
WITH live AS (
    SELECT id FROM prompt_versions WHERE status IN ('active', 'shadow')
),
shared AS (
    SELECT item_id, ticker
    FROM scored_outcomes
    WHERE prompt_version IN (SELECT id FROM live) AND abn_1d IS NOT NULL
    GROUP BY item_id, ticker
    HAVING count(DISTINCT prompt_version) = (SELECT count(*) FROM live)
),
days AS (
    -- DISTINCT ON keeps the first row of each group in ORDER BY order:
    -- each rubric's highest-scored article per ticker-day.
    SELECT DISTINCT ON (so.prompt_version, so.ticker, (so.entry_ts AT TIME ZONE 'America/New_York')::date)
        so.prompt_version, so.version_status, so.score, so.sign, so.abn_1d
    FROM scored_outcomes so
    JOIN shared USING (item_id, ticker)
    WHERE so.prompt_version IN (SELECT id FROM live)
    ORDER BY so.prompt_version, so.ticker, (so.entry_ts AT TIME ZONE 'America/New_York')::date,
             so.score DESC, so.entry_ts, so.scored_at, so.item_id
)
SELECT
    prompt_version AS rubric,
    version_status AS status,
    count(*) AS ticker_days,
    round(100.0 * count(*) FILTER (WHERE score >= 4) / count(*)) AS share_of_days_4plus,
    round((avg(abs(abn_1d)) FILTER (WHERE score >= 4) * 100)::numeric, 2) AS avg_abs_move_1d_4plus,
    round((avg(abs(abn_1d)) FILTER (WHERE score < 4) * 100)::numeric, 2) AS avg_abs_move_1d_rest,
    count(*) FILTER (WHERE score >= 4 AND sign IS NOT NULL) AS calls_4plus,
    round(100 * avg((sign * abn_1d > 0)::int) FILTER (WHERE score >= 4 AND sign IS NOT NULL)) AS hit_rate_1d,
    round(100 * wilson_low(count(*) FILTER (WHERE score >= 4 AND sign * abn_1d > 0),
                           count(*) FILTER (WHERE score >= 4 AND sign IS NOT NULL))) || '–' ||
    round(100 * wilson_high(count(*) FILTER (WHERE score >= 4 AND sign * abn_1d > 0),
                            count(*) FILTER (WHERE score >= 4 AND sign IS NOT NULL))) AS range_1d
FROM days
GROUP BY prompt_version, version_status
ORDER BY version_status, prompt_version;
