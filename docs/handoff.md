# Handoff: stock-news-alerts research loop

**Last updated:** 2026-10-18 · **Plan:** [`plans/postgres-research-loop.md`](plans/postgres-research-loop.md)

Read this first when picking the project back up. It covers what's built, how it runs, what the first week of data showed, and what's next.

---

## 1. What's built

The app pushes phone alerts for market-moving news, and records everything it does in Postgres so the alerts can be judged and improved.

| PR | What it added |
|---|---|
| #3 | Neon Postgres, migrations, every score and every run logged; `seen.json` removed |
| #4 | 17-ticker watchlist, concurrent fetching/scoring, batched writes (runs went from 61 s to about 10 s) |
| #5 | `label` job: entry price and 1/3/5-day returns vs SPY in `price_outcomes` |
| #6 | Options snapshots: 8 near-the-money contracts per alert at entry, +1 day (`d1`) and +3 days (`d3`); paper only |
| #7 | Weekly `report` job: job summary plus an ntfy headline |
| #8 | Report counts **ticker-days** (not articles) with 95% ranges and a baseline; HTML stripped from feeds (GOOGL bug); each story pushed once; 12 h cooldown per ticker and direction |

**Next:** PR 6 of the plan, shadow rubrics (see section 5).

## 2. How it runs

One workflow (`.github/workflows/check.yml`) with a `job` input, triggered by AWS EventBridge (us-east-2) through one API destination, `github-check-news`:

| Job | Schedule | Rule / detail-type | Target input |
|---|---|---|---|
| `alerts` | every 30 min | `trigger-check-news` / `TriggerCheck` | `{"ref":"main"}` |
| `label` | weekdays, `label-weekdays` | `trigger-label` / `TriggerLabel` | `{"ref":"main","inputs":{"job":"label"}}` |
| `report` | Saturdays, `report-weekly` | `trigger-report` / `TriggerReport` | `{"ref":"main","inputs":{"job":"report"}}` |

All schedules send source `stock-alerts.scheduler`. Every run applies pending migrations first and writes a row to `runs`.

- **Databases:** Neon project `stock-news-alerts`. Branch `main` is production (GitHub secret `DATABASE_URL`). Branch `dev` is for local work and tests (`.env`: `DATABASE_URL` pooled, `TEST_DATABASE_URL` direct).
- **Tests:** `pytest` locally runs DB tests only if `TEST_DATABASE_URL` is set. CI (`test.yml`) always runs them against Postgres 18.
- **Look at data:** Neon console → SQL Editor on branch `main`. Start with the `scored_outcomes` and `ticker_days` views; the report queries in `sql/report/` can be pasted in as-is after replacing `%(since)s` with a date.
- **Re-run the report any time:** Actions → Check news → Run workflow → job `report`, with *dry run* ticked to print it in the log without pushing.
- **PR conventions:** one branch and PR per change, small commits, no Claude attribution in commits or PR text. PRs are opened with `gh`.

### Known issue to verify
- **`label` schedule time.** Runs were firing at 23:45 UTC (7:45 pm ET, after the close) because the schedule's time zone was America/New_York with 19:45 entered. Price labels are unaffected, but `d1`/`d3` option snapshots need the market open, so **none were taken in the first week**. The fix: `label-weekdays` should be **15:45 America/New_York** (it stays 15 minutes before the close through daylight saving changes). Confirm with the next weekday's `label` run time and non-zero `options.follow_ups` in its `runs.counts`.

## 3. Findings from the first week (Oct 13–16, 2026)

Source: report dry run on 2026-10-18, after #8 (per ticker-day).

| Metric | Value | Reading |
|---|---|---|
| Score-4+ directional calls | **38 ticker-days** (was shown as 435 articles) | Articles were heavily syndicated: one event counted up to ~90 times |
| 1-day hit rate, score 4+ | **55%**, 95% range 40–70% | Indistinguishable from a coin flip at this sample size |
| Always-bearish baseline | **68%** (53–81%) | Watchlist stocks mostly underperformed SPY that week; the scorer didn't beat the baseline |
| Always-bullish baseline | 32% (19–47%) | |
| Spearman ρ, score vs \|1-day move\| | **−0.02** (50 ticker-days) | Score doesn't predict move size; days scored 5 moved 1.43% vs 1.5% for 4s |
| Ticker-days with an alert | 56 of 64 (news) | With 100–300 articles per stock per day, almost every day has at least one 4: alerts aren't rare, so they carry little information |
| Alerts per day before #8 | ~93 | Expect a handful now (dedupe and cooldown) |
| Implied vs realized (weekly straddle) | 3.06% implied vs 3.89% realized over 3 days, 24 alerts | Interesting but still per alert, not per ticker-day, and tiny |

**What the false alarms have in common** (score 4, 1-day move under 0.4%):
- Opinion and speculation: "Is Apple's Stock in Trouble?"
- Previews of scheduled events: "Netflix Set to Release Quarterly Earnings on Tuesday"
- Recaps of moves that already happened: "Nvidia Stock Falls to $220…"
- Tangential mentions: "SpaceX turns to debt…" scored for NVDA
- Expected product news: Apple HomeView launch

**Before #8, the "missed movers" were mostly recaps** ("Why is Oracle stock gaining 4% today?"): articles that arrive after the move, which no score can catch.

**Data-quality bugs found and fixed in #8:**
- Google News summaries are HTML wrapping a `news.google.com` link, so the "Google" alias matched 87% of news items. That caused 3,864 junk GOOGL scores in one week, and Claude was sent HTML as excerpts. Old rows from before the fix are still in the data.
- Syndicated duplicates ("Headline - Reuters" / "Headline - CNBC") were each pushed and counted.

## 4. What the findings imply

1. **Make 4s rare and meaningful.** The rubric rates "sounds important" rather than "new, material, company-specific". This is the first shadow-rubric experiment.
2. **Judge rubrics paired, not week against week.** Shadow and active score the same news on the same days, which needs far less data than comparing different weeks with different market moods.
3. **Always compare against the baseline.** A hit rate only matters if it beats always-bullish or always-bearish on the same days.
4. **Sample size.** At about 85 ticker-days a week, telling 60% from 50% reliably takes a couple of months of calls. Don't tune to a single week.
5. **Direction vs magnitude.** For heavily covered mega-caps, direction is largely priced in within minutes, and alerts arrive up to 30 minutes after publication. Predicting *which days move a lot* may be more achievable. It's measured by the straddle and implied-vs-realized sections, once `d1`/`d3` snapshots exist.
6. **Self-learning later.** Once there are a few hundred independent ticker-days (2–3 months), a small model retrained weekly on top of the Claude score (inputs: score, direction, source, ticker, time of day, first story vs repeat, pre-entry move) could predict outcomes directly. Promote a new model only if it beats the current one on a later, held-out week. Fine-tuning the LLM itself isn't worth it at this data size.

This is research on the scorer's output, not a trading signal or investment advice. The app never places trades.

## 5. Next steps

1. **PR 6, shadow rubrics** (in progress): score every active and shadow rubric version on the same pairs; only `active` pushes; a paired comparison section in the report; a documented promote/retire procedure. First experiment, **v2**: opinion, previews, price targets, holdings filings, recaps and tangential mentions score 2 or lower, and 4–5 are reserved for new, material, company-specific information. Same model, so the rubric is the only change. This doubles Claude scoring cost while a shadow runs.
2. Verify the `label` schedule fix (section 2).
3. Convert "Implied vs realized" to ticker-days once option follow-ups exist.
4. After 3–4 weeks of paired data: promote or retire v2, by hit rate vs baseline, share of days scored 4+, and move size on 4+ days vs the rest.
