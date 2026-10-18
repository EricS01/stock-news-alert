# Plan: Self-learning layer

**Status:** proposed · **Date:** 2026-10-18 · **Builds on:** [postgres-research-loop.md](postgres-research-loop.md), [../handoff.md](../handoff.md)

## Context

The scorer is a fixed prompt. Shadow rubrics (PR 6) let a person improve it one change at a time. This plan adds a layer that **learns from outcomes automatically**: a small statistical model, retrained every week on everything labeled so far, that predicts how likely a news item is to come before a real move. It's promoted only when it beats what's running.

The first week showed why this is needed and why it has to wait. The Claude score barely predicts move size (Spearman ρ −0.02), and almost every ticker-day gets a 4. But there were only about 38 independent score-4+ observations, far too few to learn from. At about 85 ticker-days a week, the model becomes viable after **2–3 months** (600–1,000 ticker-days).

**Decisions made**
| Topic | Choice |
|---|---|
| What learns | A small model on top of Claude's output, not a fine-tuned LLM (too little, too noisy data) |
| Library | scikit-learn: logistic regression first, gradient boosting only once there are several thousand ticker-days |
| Where it runs | A weekly `train` job in the existing workflow, after the Saturday report |
| How it's adopted | The same shadow pattern as rubrics: log predictions, compare paired, then promote |
| Trading | Still paper only. The model decides what's worth a push, nothing more. |

---

## What the model predicts

Two targets, both known only after the fact and both already computed by the label job:

1. **Magnitude:** will the stock make an unusually large move vs SPY over the next trading day? Label: `|abn_1d|` divided by the ticker's trailing 20-day volatility of abnormal returns is above 1.5. A 2% day is ordinary for NVDA and big for JPM, so this compares like with like.
2. **Direction:** given a bullish or bearish call, will `abn_1d` have the predicted sign?

Magnitude comes first. It's likely more learnable (direction on mega-cap news is largely priced in within minutes), and it's what straddles and the implied-vs-realized section care about.

## Inputs (features)

Only information available **when the push decision is made**. Anything computed later is a leak.

| Group | Features | Available from |
|---|---|---|
| Claude | score per rubric, direction, floor applied | `scores` |
| Source | source, publisher (from the " - Publisher" suffix) | `news_items` |
| Ticker | ticker, trailing 20-day abnormal volatility | daily bars |
| Novelty | articles about this ticker in the previous 24 h; is this the first copy of the story; hours since the first copy | `news_items` + `story_key()`, counted **before** this item |
| Timing | session at scoring (pre-market / open / after hours / weekend), minutes from publication to score, day of week | `scored_at`, `published_at` |
| Already moved? | abnormal move from the previous close to entry; **move from publication to entry** | daily bars; 5-minute bars (**expire after ~60 days**) |
| Market | SPY move from the previous close to entry | daily and 5-minute bars |
| Calendar | days to or since the ticker's next or last earnings date | yfinance earnings dates |

Most of these can be rebuilt later from stored data. The exception is anything that needs 5-minute bars, because yfinance only serves them for about 60 days. **That's why Phase 0 starts now.**

## How it learns and is judged

- **Unit:** train on articles, judge on ticker-days, like the report. Splits are by time and grouped by ticker-day, so the same event is never on both sides.
- **Walk-forward evaluation:** train on weeks 1…k, predict week k+1, and slide forward. This is the only honest test for something that will run on next week's news.
- **Metrics:**
  - AUC: does it rank risky days above quiet ones?
  - Calibration (Brier score): does "30%" mean 30%?
  - Precision at the top N days per week: of the days it would push, how many moved?
  - Hit rate vs the always-bullish and always-bearish baselines, for direction
- **Comparison set:**
  - Claude score alone (score ≥ 4)
  - the current model
  - the candidate model

  All three are compared on the same held-out weeks.
- **Promotion gate:** a candidate replaces the current model only if it beats it on the last 4 held-out weeks, and with a 95% interval that doesn't overlap the score-only rule. Otherwise it's logged and discarded. This is what keeps "self-learning" from chasing noise.

## How it's used

- **Shadow first:** every alerts run logs the model's probability next to each score, and pushes don't change.
- **Once promoted:** push when `score ≥ 4 AND probability ≥ cutoff`. The cutoff is set so pushes stay at about N per day. Claude still writes the reason and the direction; the model only gates.
- **Drift:** the weekly report shows the live model's calibration on last week's data. If it degrades, the gate falls back to score-only until a retrained model passes again.

---

## Work breakdown: one branch and PR each

| # | When | Branch | Scope |
|---|---|---|---|
| 0 | **Now** | `feature/capture-entry-context` | Labeler stores the expiring inputs: price at publication (first 5-minute bar at or after `published_at`), SPY at publication, previous close for ticker and SPY. Backfill the ~60 days still available. Migration adds the columns to `price_outcomes`. |
| 1 | Weeks 2–4 | `feature/feature-view` | `features` SQL view (all inputs, as of decision time, with leakage tests) · volatility-normalized magnitude label · earnings dates · report section "Model readiness": labeled ticker-days so far and whether a walk-forward baseline model beats score-only yet |
| 2 | ~Month 2 | `feature/train-job` | `alerts/learn.py` · `train` job (Saturday, after `report`) · `models` table (id, trained_at, features, coefficients as JSON, walk-forward metrics, status) · `model_predictions` table · predictions logged in alert runs as shadow |
| 3 | ~Month 3 | `feature/model-gate` | Promotion gate and `python -m alerts.models promote` · push gate with daily-volume cutoff · calibration and drift section in the report |

**New dependency:** `scikit-learn` (with numpy, already installed via pandas) from PR 2. **New schedule:** none; `train` can run as the second step of the Saturday `report` job.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Leakage (using information from after the decision) | Features computed "as of" `scored_at` in SQL; tests that shift timestamps and assert features don't change |
| Overfitting a few events | Grouped time splits by ticker-day; few features; regularization; 4-week promotion gate |
| Regime change (a bearish week, an earnings season) | Walk-forward evaluation spans different weeks; drift check falls back to score-only |
| Too little data | The "Model readiness" section says when there's enough; no promotion before 600+ ticker-days |
| yfinance gaps | Inputs nullable; the model handles missing values; Phase 0 backfill retries |
| Cost | No extra Claude calls; training is seconds of CPU in the weekly job |

## Verification

- **Phase 0:** new columns are filled for new outcomes and for the backfilled ~60 days; spot check a publication price against raw 5-minute bars.
- **Phase 1:** the feature view has one row per scored pair, and the leakage tests pass. The readiness section appears in the report.
- **Phase 2:** a manual `train` run writes a `models` row with walk-forward metrics, and alert runs log predictions without changing pushes.
- **Phase 3:** the gate refuses a deliberately bad candidate, and a promoted model changes pushes only above the cutoff.
