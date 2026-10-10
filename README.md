# stock-news-alerts

Sends a push notification to your phone when news breaks that could move a stock on your watchlist.

```
Hacker News ─┐
News RSS ────┤                                   score ≥ 4
SEC EDGAR ───┼─► dedupe ─► watchlist match ─► Claude impact score ─────────► ntfy push 📱
Reddit ──────┘
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

### 3. GitHub
1. Create a **private** repo and push this project to it.
2. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

   | Secret | Required |
   |---|---|
   | `ANTHROPIC_API_KEY` | yes |
   | `NTFY_TOPIC` | yes |
   | `SEC_USER_AGENT` | yes |
   | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | optional; Reddit is skipped without them |

3. Go to **Actions → Check news → Run workflow**. Run it once with *dry run* checked, then once without.

The workflow has no built-in schedule. To run it automatically every 15 minutes, set up the EventBridge trigger described below.

## Reliable scheduling with AWS EventBridge

GitHub's built-in `schedule:` trigger is best-effort: runs can be delayed by 15+ minutes, dropped under load, or never registered at all. So the workflow doesn't use it. Instead, an AWS EventBridge schedule calls GitHub's [workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) every 15 minutes. That's the same call the **Run workflow** button makes, and GitHub runs those right away rather than queueing them.

### How it works

AWS has moved scheduled rules into **EventBridge Scheduler**, which can't call an outside HTTP endpoint directly. The trigger therefore goes through the default event bus:

```
EventBridge Scheduler ──PutEvents──► default event bus ──► Rule ──► API destination ──POST──► GitHub API
  rate(15 minutes)                   source + detail-type    match     + connection (token)      workflow_dispatch
```

| Piece | What it does | Key config |
|---|---|---|
| **Scheduler** `check-news-every-15m` | Fires every 15 min and sends an event to the `default` bus | Target: *Amazon EventBridge (PutEvents)* · source `stock-alerts.scheduler` · detail-type `TriggerCheck` · detail `{}` · flexible window off |
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
   - Choose a rate-based schedule of 15 minutes.
   - Target: *Amazon EventBridge* → `default` bus, with the source and detail-type from the table.
   - Let it create a new role.

Cost: about $0.40/month for the Secrets Manager secret. The schedule and the calls themselves cost a fraction of a cent.

### Troubleshooting

Runs started this way show up on the Actions tab as **workflow_dispatch**. If none appear, open **EventBridge → Rules → trigger-check-news → Monitoring**:

| Symptom | Likely cause |
|---|---|
| No invocations | The schedule's source or detail-type doesn't match the rule pattern, or the schedule is in a different region |
| `FailedInvocations` | The token is expired or wrong, the `Bearer ` prefix is missing, or the target input isn't `{"ref":"main"}` |

## Customizing

Edit `watchlist.yaml`:

```yaml
threshold: 4          # 1-5; lower = more alerts
sources:
  reddit: false       # turn a source off
stocks:
  AMD: [Advanced Micro Devices, Lisa Su]   # ticker: [aliases]
```

Tickers match as `$AMD` or a standalone uppercase `AMD`. Aliases match case-insensitively as whole words. For tickers outside the default list, the SEC source looks up the CIK automatically.

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m alerts.main --dry-run
```

To use real keys locally, copy `.env.example` to `.env`, fill it in, and run `set -a; source .env; set +a`. `.env` is gitignored.

`--dry-run` prints alerts instead of pushing them and doesn't save state. Without `ANTHROPIC_API_KEY`, it lists the unscored watchlist matches.

```bash
.venv/bin/python -m pytest
```

## Caveats
- Automatic runs depend entirely on the EventBridge trigger. If it breaks (an expired token, or a disabled schedule or rule), nothing runs until it's fixed. You can still start a run by hand with **Run workflow**.
- **GitHub Actions minutes:** a private repo gets 2,000 free minutes a month, and each run is billed as at least 1 minute. Running every 15 minutes is about 2,880 runs a month, which exceeds that. To stay free, run every 30 minutes, make the repo public, or allow paid overage (about $7/month).
- When the GitHub token expires, the EventBridge trigger stops working silently. Renew it before the expiry date.
- This is an alerting tool, not investment advice. Impact scores are the model's judgment and can be wrong.
