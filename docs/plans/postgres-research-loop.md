# Plan: Postgres research loop

**Status:** proposed · **Date:** 2026-10-12

## Context

The app currently pushes alerts for news scored ≥ 4/5, but it doesn't keep any record of how well those scores predict anything. The goal is to turn it into a **research loop**:

1. Log **every** score.
2. Measure what the stock did afterwards (+1, +3 and +5 trading days, relative to the market).
3. For scores ≥ 4, snapshot **near-the-money options** and track hypothetical strategy P&L on paper.
4. Report weekly on what works, then tune the rubric one change at a time, comparing versions side by side.

This is also a chance to get hands-on experience with **Postgres**. Hand-written SQL migrations and plain SQL queries are intentional, so the database work stays visible rather than hidden behind an ORM.

**Decisions made**
| Topic | Choice |
|---|---|
| Database | **Neon** Postgres (free tier) |
| Options data | **yfinance** snapshots, wrapped behind an interface so the source can be swapped later. Near-the-money only. |
| Watchlist | The current 7 **plus 10 large caps**: AMD, AVGO, NFLX, CRM, ORCL, JPM, UNH, LLY, XOM, COST |
| Trading | **Paper only.** The system measures; it never places trades. |

---

## Architecture

```
EventBridge schedules ──► GitHub Actions (one workflow, `job` input) ──► Neon Postgres
  every 30 min   → job=alerts   fetch → dedupe → match → score (log ALL) → push ≥4
                                → queue option entry snapshot for ≥4
  weekdays 19:45 UTC → job=label   price outcomes for matured scores
                                → option re-snapshots (+1d, +3d), entry snapshots still pending
  Saturdays    → job=report  SQL over everything → ntfy summary + Actions job summary
```

- **One workflow, several jobs.** `check.yml` gets a `job` input (`alerts` | `label` | `report`). Every EventBridge rule reuses the **same API destination** and sends a different constant input, e.g. `{"ref":"main","inputs":{"job":"label"}}`. No new connections or tokens are needed.
- **Postgres replaces `seen.json`.** Dedupe becomes "does this `news_items` row already exist?". That removes the state commit-back step and about 48 bot commits a day.
- **Every run is recorded** in a `runs` table, which also fixes two existing bugs:
  - A run fails (red in Actions, so GitHub emails you) if every scoring call failed.
  - Items count as processed only once they've been scored.

---

## Database schema (initial)

Migrations are plain SQL files in `migrations/NNN_name.sql`, applied by a small runner (`python -m alerts.db migrate`) that records what has run in a `schema_migrations` table.

| Table | Purpose | Key columns |
|---|---|---|
| `news_items` | Every fetched item (and the dedupe check) | `id` PK, `source`, `title`, `url`, `text`, `published_at`, `fetched_at`, `meta jsonb` |
| `prompt_versions` | Rubric versions | `id` PK (`v1`…), `model`, `system_prompt`, `status` (`active` / `shadow` / `retired`), `created_at`, `notes` |
| `scores` | Every Claude score | `id`, `item_id` FK, `ticker`, `prompt_version` FK, `score`, `direction`, `reason`, `floor_applied`, `alerted`, `scored_at`; unique on `(item_id, ticker, prompt_version)` |
| `price_outcomes` | What the stock did afterwards | PK `(item_id, ticker)`, `entry_ts`, `entry_price`, `ret_1d/3d/5d`, `spy_ret_1d/3d/5d`, `abn_1d/3d/5d`, `status`, `labeled_at` |
| `option_snapshots` | Near-the-money option quotes | `id`, `item_id`, `ticker`, `contract_symbol`, `expiry`, `strike`, `right` (C/P), `kind` (`entry` / `d1` / `d3`), `taken_at`, `underlying_price`, `bid`, `ask`, `last`, `iv`, `volume`, `open_interest` |
| `option_snapshot_queue` | Entry snapshots waiting for market hours | `item_id`, `ticker`, `queued_at`, `done_at` |
| `runs` | One row per job run | `id`, `job`, `started_at`, `finished_at`, `status`, `counts jsonb`, `error` |

Outcomes are keyed by `(item_id, ticker)` rather than by score, because what the stock did doesn't depend on which rubric scored it. That lets shadow rubrics be compared against the same outcomes.

---

## Key behaviours

**Score logging.** Each score is written with its `prompt_version`. The current prompt becomes `v1`, stored in `prompt_versions` (the text is still read from code; the DB row is the record of what ran).

**Price labeling** (in `label`):
- **Entry price:** the first 5-minute bar at or after `scored_at`, or the next regular-session open if the alert came while the market was closed.
- **Returns:** close-to-entry at +1, +3 and +5 trading days, the same for SPY, and **abnormal return = stock − SPY**.
- **Data:** yfinance daily bars. 5-minute bars are only available for about 60 days, which is fine because labeling runs daily.

**Option snapshots:**
- **Which contracts:** for scores ≥ 4, the nearest weekly expiry that's at least 2 days out, plus the expiry closest to 30 days out. For each, the **two strikes nearest the stock price**, calls and puts: **8 contracts per alert**.
- **Timing:** the entry snapshot is taken in the same alert run when the market is open; otherwise it's queued and taken by the next run during market hours. Re-snapshots (`d1`, `d3`) happen in the 19:45 UTC `label` run, shortly before the close (it's an hour earlier in winter, still inside market hours).
- **Pricing:** paper P&L enters at the **ask** and exits at the **bid**. Never at mid-price.
- **No delta:** yfinance doesn't provide it, and it's left out for now.

**Paper strategies** (computed in report SQL, not stored):
- **Direction trade:** a long call if bullish, or a long put if bearish.
- **Straddle:** call plus put at the strike nearest the stock price.
- **Implied move vs. realized move:** the straddle price divided by the stock price, compared with the actual move.

**Weekly report:**
- **By score bucket:** count, mean |abnormal move| and direction hit rate.
- **Rank correlation** between score and |move|.
- **Breakdowns** by source and by ticker.
- **Options P&L** by strategy and score.
- The **10 worst misses**, in both directions.
- **Comparison of active vs. shadow rubrics.**

The output goes to the Actions job summary and a short ntfy push.

**Shadow rubrics:** any `prompt_versions` row marked `shadow` scores the same pairs. Only the `active` version sends alerts, so comparisons are fair. A shadow version is promoted to `active` only on data collected **after** it was created.

---

## Work breakdown: one branch and PR each

Each PR is merged into `main` before the next one starts. Commits are small and incremental, with **no Claude attribution lines**.

| # | Branch | Scope |
|---|---|---|
| 1 | `feature/postgres-foundation` | Neon setup docs · `alerts/db.py` (psycopg 3) · migration runner · migration 001 (`news_items`, `prompt_versions`, `scores`, `runs`) · dedupe via DB · log all scores · fail the run when scoring fails, and only mark items processed after scoring · remove `seen.json` and the commit-back step · CI workflow running pytest against a Postgres service container |
| 2 | `feature/expand-watchlist` | +10 tickers with aliases and CIKs · fetch sources in parallel (thread pool) so run time stays under 1 minute (each run is billed by the started minute) · log run duration |
| 3 | `feature/price-labeler` | Migration 002 (`price_outcomes`) · `alerts/labeler.py` · `job` input on the workflow · README steps for EventBridge rule and schedule #2 |
| 4 | `feature/options-snapshots` | Migration 003 (`option_snapshots`, the queue) · `alerts/options/` with a `ChainSource` interface and a yfinance implementation · contract selection · entry snapshots plus queue · `d1`/`d3` re-snapshots in `label` |
| 5 | `feature/weekly-report` | `sql/report/*.sql` queries · `alerts/report.py` · job summary and ntfy · README steps for EventBridge rule and schedule #3 |
| 6 | `feature/shadow-rubrics` | Score with every `active` and `shadow` version · prompt text moved into `prompt_versions` · comparison section in the report · a documented promote/retire procedure |

**New dependencies:** `psycopg[binary]`, `yfinance`, `pandas` (pulled in by yfinance).
**New secret:** `DATABASE_URL` (Neon pooled connection string with `sslmode=require`).
**Local development:** a Neon `dev` branch, with its URL in `.env`. DB tests run when `TEST_DATABASE_URL` is set and are skipped otherwise; CI always runs them.

---

## Setup you'll do (outside the code)

1. **Neon:** sign up at neon.tech, then:
   1. Create the project `stock-news-alerts` in an AWS region near `us-east-2`.
   2. Create a `dev` branch.
   3. Copy the **pooled** connection strings for the `main` and `dev` branches.
2. **GitHub:** add a `DATABASE_URL` repository secret containing the `main` connection string.
3. **AWS (PR 3 and PR 5):** add one EventBridge schedule plus rule for `label` and one for `report`. Both reuse the existing API destination.
4. **PRs:** without the GitHub CLI, I push each branch and you open the PR from the link. Optionally, run `brew install gh` and then `gh auth login`, and I'll open them myself.

---

## Costs

| Item | Estimate |
|---|---|
| Neon | $0 (free tier: 0.5 GB storage; this dataset should stay well under 100 MB/year) |
| Claude | About $2–5/month with 17 tickers, roughly 2× more while a shadow rubric runs |
| GitHub Actions | About 1,440 alert runs, plus about 22 label runs and about 4 report runs a month. That stays under 2,000 minutes **only if alert runs finish in under 60 s** (PR 2 handles this). |
| yfinance | $0 |
| AWS | Unchanged (about $0.40/month) |

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| yfinance breaks or rate-limits | Wrapped behind `ChainSource`; snapshot failures are logged and retried next run, and never block alerts |
| Run time crosses 60 s, doubling the billed minutes | Parallel fetching in PR 2; `runs.finished_at` tracks duration |
| Too little data to conclude anything | Log all scores; 17 tickers; no conclusions on options before about 2–3 months |
| Overfitting the rubric | Change one thing at a time; promote only on data collected after the change |
| Neon pooled connections and long transactions | Short transactions per batch; `autocommit` for inserts |
| DST shifts the 19:45 UTC label run | It stays inside market hours year-round (3:45 pm EDT / 2:45 pm EST) |

---

## Verification

- **Each PR:** `pytest` passes locally and in CI, including the DB tests against Postgres, and `--dry-run` works against the Neon `dev` branch.
- **PR 1:** after merging, an EventBridge-triggered run writes rows to `news_items` and `scores` in Neon, `runs` shows `status = ok`, no more state commits appear, and a run with a deliberately bad API key fails red.
- **PR 3:** two trading days after merging, `price_outcomes` has `ret_1d` filled in for scores from the first day, and spot checks match the actual closes.
- **PR 4:** the next score ≥ 4 creates 8 `entry` rows, and `d1` and `d3` rows appear afterwards. One alert raised while the market was closed gets snapshotted at the next open.
- **PR 5:** a manual `job=report` run produces the job summary and an ntfy push.
- **PR 6:** a test `shadow` version writes parallel scores, sends no alerts, and shows up in the report comparison.
