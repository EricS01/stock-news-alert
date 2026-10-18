# Handoff: stock-news-alerts research loop

**Last updated:** 2026-10-18 (end of session) · **Plans:** [`plans/postgres-research-loop.md`](plans/postgres-research-loop.md) (done) · [`plans/self-learning-layer.md`](plans/self-learning-layer.md) (next)

Read this first when picking the project back up. It covers what's built, how it runs, what the first week of data showed, and what's next. **Section 0 is the to-do list for the next session.**

---

## 0. Start here: state at the end of the last session

**Open PRs**
- **#11, "Plan: self-learning layer"** (docs only, includes this handoff update). Waiting for review and merge.

**To do next, in order**
1. **Verify the `label` schedule fix.** It was firing at 23:45 UTC (after the close), so no `d1`/`d3` option snapshots were taken in week one. The user was asked to set `label-weekdays` to **15:45 America/New_York** in EventBridge Scheduler. Check: the next weekday `label` run should start at about 19:45 UTC, and its `runs.counts.options.follow_ups` should be non-zero.
   ```bash
   gh run list --workflow check.yml --limit 100 --json displayTitle,createdAt --jq '.[]|select(.displayTitle=="label")|.createdAt' | head -3
   ```
2. **Verify v2 is running as a shadow.** After #10's merge, the next alerts run should record v2 (`runs.counts.rubrics` = `["v1","v2"]`, plus a `shadows.v2` count). Watch Claude cost: scoring roughly doubles while v2 runs.
3. **Build Phase 0 of the self-learning plan** (`feature/capture-entry-context`): store price at publication (first 5-minute bar at or after `published_at`), SPY at publication, and previous closes for ticker and SPY in `price_outcomes`, and backfill. **Deadline: about Dec 10, 2026.** yfinance keeps 5-minute bars for about 60 days, so the first week's (Oct 13) context is lost after that.
4. **Saturday Oct 24:** first scheduled report with suppression and ticker-day counting in effect. Pushes should be a handful a day (they were ~93/day before #8).
5. **Around Nov 8–15:** judge v2 vs v1 with "Rubric comparison" and "Better than guessing?", then `python -m alerts.rubrics promote v2` or `retire v2`.
6. **Later:** Phases 1–3 of the self-learning plan, and convert "Implied vs realized" (`sql/report/08_*.sql`) to ticker-days once `d1`/`d3` snapshots exist.

**Working notes for the assistant**
- `gh` is installed (`/opt/homebrew/bin/gh`) and authenticated as EricS01. Open PRs with it; don't merge without the user saying so. Auto-merge is disabled on the repo.
- **No Claude attribution anywhere:** no `Co-Authored-By` trailer and no "Generated with Claude Code" footer.
- Don't remind the user to rotate pasted credentials.
- Reading production directly from a local shell (for example copying prod into `dev`) was blocked by the permission checker. To see production numbers, trigger a report dry run in Actions and read its log:
  ```bash
  gh workflow run check.yml --ref main -f job=report -f dry_run=true
  ```
- Local Python is 3.9 (`.venv`); CI uses 3.12. Keep code 3.9-compatible. Local DB tests run against the Neon `dev` branch through `.env` (`set -a; source .env; set +a`), which takes 1–2 minutes.
- Commit in small steps and run the tests at each commit before pushing.

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
| #9 | This handoff |
| #10 | **Shadow rubrics:** prompts are files (`prompts/v1.md`, `prompts/v2.md`) synced into `prompt_versions`; active and shadow rubrics score the same pairs, and only active pushes; "Rubric comparison" report section (paired); `python -m alerts.rubrics status / promote / retire`. **v2 is the first experiment** (see section 5). |

All six PRs of the original plan are done (the plan's PRs 1–6 are repo PRs #3–#7 and #10).

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
6. **Self-learning later.** Once there are a few hundred independent ticker-days (2–3 months), a small model retrained weekly on top of the Claude score (inputs: score, direction, source, ticker, time of day, first story vs repeat, pre-entry move) could predict outcomes directly. Promote a new model only if it beats the current one on a later, held-out week. Fine-tuning the LLM itself isn't worth it at this data size. Full plan: [`plans/self-learning-layer.md`](plans/self-learning-layer.md).

This is research on the scorer's output, not a trading signal or investment advice. The app never places trades.

## 5. Experiments in flight

**v2 rubric (shadow, since #10 was merged on 2026-10-18).** Same model as v1. It sends opinion, previews of scheduled events, price targets and routine rating changes, institutional holdings filings, recaps of price moves, already-expected product news and tangential mentions to 2 or lower, and reserves 4–5 for new, material, company-specific information ("most items should score 1 or 2; when unsure, choose the lower score"). Success looks like: 4+ on far fewer ticker-days than v1, bigger moves on its 4+ days than on the rest, and a hit-rate range above both v1 and the better baseline. Judge after 3–4 weeks.

**How shadow rubrics work:** each alerts run sends every (article, ticker) pair to Claude once per live rubric. Results are stored side by side in `scores` (different `prompt_version`). Only the active rubric's scores drive pushes, suppression and option snapshots. Because both rubrics judge the same news on the same days, the comparison isn't confounded by which week it was. To try another idea, add `prompts/v3.md` with one change; never edit an existing file.

## 6. Self-learning layer (planned)

Plan: [`plans/self-learning-layer.md`](plans/self-learning-layer.md). In short: a small scikit-learn model, retrained weekly in the Saturday job, predicts (1) an unusually large volatility-normalized move vs SPY and (2) whether a direction call is right. It uses inputs available at alert time: Claude scores, source, ticker volatility, story novelty, timing, whether the stock already moved before entry, the SPY move, and distance to earnings. It's judged by walk-forward evaluation on ticker-days, runs as a shadow first, and is promoted only if it beats score-only over 4 held-out weeks. It then gates pushes (`score ≥ 4 AND probability ≥ cutoff`).

| Phase | When | Branch | Scope |
|---|---|---|---|
| 0 | **Now** (before ~Dec 10) | `feature/capture-entry-context` | Price and SPY at publication, previous closes; backfill |
| 1 | Weeks 2–4 | `feature/feature-view` | `features` view with leakage tests, normalized label, earnings dates, "Model readiness" report section |
| 2 | ~Month 2 | `feature/train-job` | `alerts/learn.py`, weekly `train`, `models` and `model_predictions` tables, shadow predictions |
| 3 | ~Month 3 | `feature/model-gate` | Promotion gate, push gate with a daily-volume cutoff, drift checks |

Not viable before about 600 labeled ticker-days (2–3 months at ~85 a week).
