-- title: This week
-- note: Activity since the last report.
SELECT
    (SELECT count(*) FROM runs WHERE started_at >= %(since)s AND job = 'alerts') AS alert_runs,
    (SELECT count(*) FROM runs WHERE started_at >= %(since)s AND status = 'failed') AS failed_runs,
    (SELECT round(avg(extract(epoch FROM finished_at - started_at))::numeric, 1)
       FROM runs WHERE started_at >= %(since)s AND job = 'alerts') AS avg_alert_run_s,
    (SELECT count(*) FROM news_items WHERE fetched_at >= %(since)s) AS news_items,
    (SELECT count(*) FROM scores WHERE scored_at >= %(since)s) AS scores,
    (SELECT count(*) FROM scores WHERE scored_at >= %(since)s AND alerted) AS alerts,
    (SELECT count(*) FROM option_snapshots WHERE taken_at >= %(since)s) AS option_quotes;
