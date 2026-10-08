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

After that it runs every 15 minutes on its own.

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
- GitHub can delay scheduled runs by 5–15 minutes when it's busy, so alerts aren't instant.
- GitHub disables scheduled workflows after 60 days without repo activity. The state commits normally count as activity. If the workflow gets disabled anyway, re-enable it under Actions.
- This is an alerting tool, not investment advice. Impact scores are the model's judgment and can be wrong.
