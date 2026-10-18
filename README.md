# stock-news-alerts

Sends a push notification to your phone when news breaks that could move a stock on your watchlist.

```
Hacker News ─┐
News RSS ────┤                                   score ≥ 4
SEC EDGAR ───┼─► dedupe ─► watchlist match ─► Claude impact score ─────────► ntfy push 📱
Reddit ──────┘      │                                │
                    └──────── Neon Postgres ◄────────┘
                      every item, every score, every run
```

A push looks like this: **NVDA ▼ 4/5 — Nvidia under DOJ antitrust probe**, followed by a one-line reason. Tapping it opens the article.

## Sources

| Source | What it catches | Auth |
|---|---|---|
| Hacker News (Algolia API) | Tech news, launches, outages | none |
| Yahoo Finance + Google News RSS | Mainstream financial headlines, per ticker | none |
| SEC EDGAR | 8-K material events, 10-Q/10-K, Form 4 insider trades | User-Agent with contact info |
| Reddit (r/stocks, r/investing, r/wallstreetbets, r/StockMarket) | Retail chatter | free Reddit app |

8-K filings for earnings (2.02), M&A (1.01/2.01), and exec changes (5.02) always alert. For everything else, Claude (`claude-haiku-5-5`) rates the impact from 1 to 5.

## Setup

### 1. Phone: ntfy
1. Install **ntfy** ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Pick a long, unguessable topic name, e.g. `stocks-` plus random characters. Anyone who knows the name can read your alerts:
   ```bash
   echo "stocks-$(openssl rand -hex 8)"
   ```
3. In the app, tap **+** and subscribe to that topic.

### 2. Keys
- **Anthropic API key**: https://console.anthropic.com → API Keys. Expect a few cents per day.
- **Reddit app** (optional): https://www.reddit.com/prefs/apps → "create another app" → type **script**. Copy the client ID (under the app name) and the secret.
- **SEC User-Agent**: a string with your name and email, e.g. `Jane Doe jane@example.com`. [SEC requires this](https://www.sec.gov/os/accessing-edgar-data).

### 3. Database: Neon Postgres
Every fetched item, every score (not only the ones that alert), and every run is stored in Postgres. That's what dedupes items between runs, and it's the data the research loop builds on (see `docs/plans/postgres-research-loop.md`).

1. Sign up at https://neon.tech (the free tier is plenty).
2. Create a project named `stock-news-alerts` in an AWS region near your EventBridge setup (e.g. `us-east-2`).
3. Create a branch named `dev` for local work (**Branches → New branch**).
4. For each of `main` and `dev`, open **Connect** and copy the connection string. Use the **pooled** string (host contains `-pooler`) for `DATABASE_URL`, and the direct one for running tests. Both should end in `?sslmode=require`.

Tables are created by migrations in `migrations/`, applied automatically at the start of each workflow run (`python -m alerts.db migrate`). Applied migrations are recorded in `schema_migrations`.

### 4. GitHub
1. Create a **private** repo and push this project to it.
2. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

   | Secret | Required |
   |---|---|
   | `DATABASE_URL` | yes: the pooled connection string for the Neon `main` branch |
   | `ANTHROPIC_API_KEY` | yes |
   | `NTFY_TOPIC` | yes |
   | `SEC_USER_AGENT` | yes |
   | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | optional; Reddit is skipped without them |

3. Go to **Actions → Check news → Run workflow**. Run it once with *dry run* checked, then once without.

The workflow has no built-in schedule. To run it automatically every 30 minutes, set up the EventBridge trigger described below.

## Reliable scheduling with AWS EventBridge

GitHub's built-in `schedule:` trigger is best-effort: runs can be delayed by 15+ minutes, dropped under load, or never registered at all. So the workflow doesn't use it. Instead, an AWS EventBridge schedule calls GitHub's [workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) every 30 minutes. That's the same call the **Run workflow** button makes, and GitHub runs those right away rather than queueing them.

### How it works

AWS has moved scheduled rules into **EventBridge Scheduler**, which can't call an outside HTTP endpoint directly. The trigger therefore goes through the default event bus:

```
EventBridge Scheduler ──PutEvents──► default event bus ──► Rule ──► API destination ──POST──► GitHub API
  rate(30 minutes)                   source + detail-type    match     + connection (token)      workflow_dispatch
```

| Piece | What it does | Key config |
|---|---|---|
| **Scheduler** `check-news-every-15m` | Fires every 30 min and sends an event to the `default` bus | Target: *Amazon EventBridge (PutEvents)* · source `stock-alerts.scheduler` · detail-type `TriggerCheck` · detail `{}` · flexible window off |
| **Rule** `trigger-check-news` | Matches that event and forwards it to the API destination | Pattern `{"source":["stock-alerts.scheduler"],"detail-type":["TriggerCheck"]}` · **target input: Constant `{"ref":"main"}`** · retries 2, max age 10 min |
| **API destination** `github-check-news` | The HTTP endpoint the rule calls | `POST https://api.github.com/repos/EricS01/stock-news-alert/actions/workflows/check.yml/dispatches` · limited to 1 call per second |
| **Connection** `github` | Handles authentication (the token is stored in Secrets Manager) | API-key auth: `Authorization: Bearer github_pat_…` · headers `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` |
| **GitHub token** | Lets AWS start the workflow, and nothing else | Fine-grained PAT · only this repo · **Actions: Read and write** |

All the AWS pieces live in one region (here, `us-east-2`). The scheduler sends its event to that region's bus, so the rule must be in the same region.

### Setup

1. **GitHub token:** go to **Settings → Developer settings → Fine-grained tokens → Generate new token**. Limit it to this repo, give it **Actions: Read and write**, and set an expiration. Put the expiry date in a calendar.
2. **API destination and connection:** go to **EventBridge → API destinations → Create**.
   - Use the endpoint and method from the table, then create a new connection.
   - Connection settings: destination type *Other*, auth *API Key*, key name `Authorization`, value `Bearer <token>` (the word `Bearer`, one space, then the full `github_pat_…` token), plus the two headers.
3. **Rule:** go to **EventBridge → Rules → Create rule**.
   - Choose the `default` bus, then *Custom event* with the pattern above.
   - Target: the API destination, with a default execution role.
   - Set the **target input** to Constant `{"ref":"main"}`. Without it, GitHub receives the whole event and rejects it with a 422.
   - Set retries to 2 with a 10-minute maximum age, so an outage doesn't cause a burst of stale runs later.
4. **Schedule:** go to **EventBridge → Scheduler → Create schedule**.
   - Choose a rate-based schedule of 30 minutes. That's about 1,440 runs a month, which keeps a private repo within GitHub's 2,000 free Actions minutes (see Caveats). The first run happens one interval after you create the schedule.
   - Target: *Amazon EventBridge* → `default` bus, with the source and detail-type from the table.
   - Let it create a new role.

Cost: about $0.40/month for the Secrets Manager secret. The schedule and the calls themselves cost a fraction of a cent.

### Second schedule: daily labeling

The `label` job reuses the same API destination and connection. It only needs its own schedule and rule, so that the rule can send a different input:

1. **Rule:** go to **EventBridge → Rules → Create rule** on the `default` bus.
   - Name: `trigger-label`. Pattern: `{"source":["stock-alerts.scheduler"],"detail-type":["TriggerLabel"]}`
   - Target: the same API destination, `github-check-news`.
   - **Target input:** Constant `{"ref":"main","inputs":{"job":"label"}}`
   - Retries 2, maximum age 10 minutes.
2. **Schedule:** go to **EventBridge → Scheduler → Create schedule**.
   - Name: `label-weekdays`. Use a **cron-based** schedule, `cron(45 19 ? * MON-FRI *)`, with time zone **UTC**. That's 3:45 pm EDT or 2:45 pm EST, so it's inside market hours all year. Flexible window off.
   - Target: *Amazon EventBridge* → `default` bus, source `stock-alerts.scheduler`, detail-type `TriggerLabel`, detail `{}`.

These runs show up on the Actions tab named **label**; alert runs are named **alerts**.

### Third schedule: weekly report

Same pattern again, for the `report` job:

1. **Rule** `trigger-report`: pattern `{"source":["stock-alerts.scheduler"],"detail-type":["TriggerReport"]}`, the same API destination, **target input** Constant `{"ref":"main","inputs":{"job":"report"}}`, retries 2, maximum age 10 minutes.
2. **Schedule** `report-weekly`: cron `0 14 ? * SAT *` in **UTC** (Saturday morning in the US), target the `default` bus with source `stock-alerts.scheduler`, detail-type `TriggerReport`, detail `{}`.

### Troubleshooting

Runs started this way show up on the Actions tab as **workflow_dispatch**. If none appear, open **EventBridge → Rules → trigger-check-news → Monitoring**:

| Symptom | Likely cause |
|---|---|
| No invocations | The schedule's source or detail-type doesn't match the rule pattern, or the schedule is in a different region |
| `FailedInvocations` | The token is expired or wrong, the `Bearer ` prefix is missing, or the target input isn't valid JSON like `{"ref":"main"}` |

## Measuring outcomes: the label job

Every score is kept, so the app can check afterwards whether high scores actually preceded big moves. The `label` job runs on weekdays shortly before the close and fills in `price_outcomes` for every scored (item, ticker) pair:

- **Entry:** the open of the first 5-minute bar at or after the pair was first scored. If the market was closed, that's the next regular-session open.
- **Returns:** close +1, +3 and +5 trading days after the entry day, divided by the entry price, minus 1. The same is computed for SPY, and the **abnormal return** (stock − SPY) is a generated column.
- A pair stays `pending` until all three horizons have closed, so labeling picks it up again on later runs. A day's close only counts once it's final, so the 19:45 UTC run never uses today's partial bar. Pairs that still can't be completed after 30 days become `expired`.
- Prices come from Yahoo Finance via `yfinance` (free, unofficial). If one ticker fails, its pairs are retried the next day. If SPY fails, the run fails.

Run it by hand from **Actions → Check news → Run workflow** with job `label`, or locally with `python -m alerts.labeler --dry-run`. Example query:

```sql
SELECT s.score, count(*), avg(abs(p.abn_1d)) AS mean_abs_abn_1d
FROM scores s JOIN price_outcomes p USING (item_id, ticker)
WHERE p.ret_1d IS NOT NULL
GROUP BY s.score ORDER BY s.score;
```

## Options snapshots (paper only)

For every alert (score ≥ threshold), the app records near-the-money option quotes so that simple strategies can be evaluated on paper later. **It never places trades.**

- **Which contracts:** the nearest expiry at least 2 days out, plus the expiry closest to 30 days out. For each, calls and puts at the two strikes nearest the stock price: 8 contracts per alert.
- **When:** the `entry` snapshot is taken by the alert run itself if the market is open (9:35–15:58 ET). Otherwise the alert waits in `option_snapshot_queue` until the next run during market hours. The `label` run re-snapshots the same contracts 1 and 3 trading days later (`d1`, `d3`), shortly before the close. A missed day is skipped rather than taken late, so every `d1` and `d3` is comparable.
- **Data:** yfinance option chains (bid, ask, last, implied volatility, volume, open interest; no greeks). On market holidays the quotes are stale, so snapshots wait. A queued entry that keeps failing is given up after 3 days, with the error in `last_error`.
- Snapshot failures are logged in the run's `counts` and never fail the alert or label job.

Paper P&L should enter at the **ask** and exit at the **bid**, never the mid-price:

```sql
SELECT e.ticker, e.expiry, e.strike, e."right", e.ask AS paid, d.bid AS sold_d1,
       round((d.bid / e.ask - 1)::numeric, 3) AS return_d1
FROM option_snapshots e
JOIN option_snapshots d USING (item_id, ticker, contract_symbol)
WHERE e.kind = 'entry' AND d.kind = 'd1' AND e.ask > 0
ORDER BY e.taken_at DESC LIMIT 20;
```

## Weekly report

The `report` job runs every query in `sql/report/` and publishes the results as the run's **job summary** (open the run on the Actions tab), then pushes a one-line headline to ntfy that links to it. Sections:

| Section | Question it answers |
|---|---|
| This week | Did the runs work, and how much was scored, pushed and suppressed? |
| By score | Do higher scores come before bigger moves (abnormal, vs SPY), and in the predicted direction? With 95% ranges. |
| Better than guessing? | Does the scorer beat always guessing bullish (or bearish) on the same days? |
| Does a higher score mean a bigger move? | Spearman rank correlation between score and move size |
| By source / By ticker | Which sources and tickers produce useful alerts? |
| Options on alerts (paper) | Long call or put in the predicted direction, and straddles: return from buying at the ask to selling at the bid 1 or 3 days later |
| Implied vs realized move | Did the stock move more than options were pricing in? |
| Worst misses this week | Days scored high that didn't move, and days scored low that moved the most |

**Results are counted per ticker-day**, meaning one stock on one trading day, not per article. A big story is syndicated dozens of times, but the stock only moves once, so counting articles would let one event dominate the numbers. The view `ticker_days` (migration 005) groups each stock's articles by the trading day of entry and represents the day by its highest-scored article. Hit rates come with a 95% Wilson confidence interval (the SQL functions `wilson_low` and `wilson_high`), so you can tell a real difference from noise.

The queries are plain SQL over views from `migrations/004_report_views.sql` and `005_events_and_suppression.sql`: `scored_outcomes` (every score with its news item and price outcome) and `option_entries` (entry quotes with tenor and nearness to the money). Each file can be pasted into the Neon SQL editor as is, after replacing `%(since)s` with a date such as `now() - interval '7 days'`. To add a section, add a numbered `.sql` file with a `-- title:` comment.

Run it by hand from **Actions → Check news → Run workflow** with job `report`, or locally with `python -m alerts.report --dry-run`, which prints the report instead of publishing it.

## Customizing

Edit `watchlist.yaml`:

```yaml
threshold: 4          # 1-5; lower = more alerts
sources:
  reddit: false       # turn a source off
stocks:
  AMD: [Advanced Micro Devices, Lisa Su]   # ticker: [aliases]
cashtag_only: [CRM, COST]                  # tickers that are also common words
alert_cooldown_hours: 12                   # see "Fewer, better pushes" below
```

**Fewer, better pushes.** Anything scored at or above `threshold` is pushed unless:
- it's the same story (headline ignoring the " - Publisher" suffix, case and punctuation) as one already pushed for that ticker, or
- the ticker already had a push in the same direction with an equal or higher score within `alert_cooldown_hours` (default 12).

Held-back alerts are still scored and stored, with `scores.suppressed` set to `duplicate` or `cooldown`. Option snapshots are only taken for pushed alerts.

Tickers match as `$AMD` or a standalone uppercase `AMD`. Aliases match case-insensitively as whole words. Tickers listed under `cashtag_only` match only as `$CRM`, so "CRM software" or "LOW COST" don't count; their aliases (Salesforce, Costco) still match. For tickers outside the default list, the SEC source looks up the CIK automatically.

The default watchlist has 17 large caps: AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AMD, AVGO, NFLX, CRM, ORCL, JPM, UNH, LLY, XOM and COST. Each run fetches sources concurrently, scores Claude batches four at a time, and writes to Postgres in one transaction, so it stays well under a minute (GitHub bills each started minute). Every run's phase timings are in `runs.counts` (`fetch_s`, `score_s`, `write_s`, `duration_s`).

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m alerts.main --dry-run
```

To use real keys locally, copy `.env.example` to `.env`, fill it in, and run `set -a; source .env; set +a`. `.env` is gitignored.

`--dry-run` prints alerts instead of pushing them and never writes to the database (it still reads it to skip items already processed). Without `ANTHROPIC_API_KEY`, it lists the unscored watchlist matches; without `DATABASE_URL`, it skips dedupe.

Point `DATABASE_URL` in `.env` at the Neon `dev` branch, never `main`. Before the first run against a new branch:

```bash
.venv/bin/python -m alerts.db migrate
```

```bash
.venv/bin/python -m pytest
```

Database tests run only when `TEST_DATABASE_URL` is set; otherwise they're skipped. Use the **direct** (non-pooled) `dev` connection string: each test creates and drops its own schema, so it never touches the tables the app uses. CI always runs them against a Postgres service container.

To look around the data, use the Neon **SQL Editor**, or `psql "$DATABASE_URL"`:

```sql
SELECT s.scored_at, s.ticker, s.score, s.direction, n.title
FROM scores s JOIN news_items n ON n.id = s.item_id
ORDER BY s.scored_at DESC LIMIT 20;

SELECT started_at, status, counts, error FROM runs ORDER BY started_at DESC LIMIT 10;
```

## Caveats
- Automatic runs depend entirely on the EventBridge trigger. If it breaks (an expired token, or a disabled schedule or rule), nothing runs until it's fixed. You can still start a run by hand with **Run workflow**.
- **GitHub Actions minutes:** a private repo gets 2,000 free minutes a month, and each run is billed as at least 1 minute. The 30-minute schedule (about 1,440 runs a month) stays within that, but alerts can arrive up to about 30 minutes after the news. Every 15 minutes would be about 2,880 runs, over the free tier: for faster alerts, make the repo public or allow paid overage (about $7/month).
- A run fails (red in Actions, so GitHub emails you) when every scoring call fails, a push can't be sent, or the database is unreachable. Items that couldn't be scored are retried on the next run.
- When the GitHub token expires, the EventBridge trigger stops working silently. Renew it before the expiry date.
- This is an alerting tool, not investment advice. Impact scores are the model's judgment and can be wrong.
