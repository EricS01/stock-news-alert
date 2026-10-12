-- What each scored stock did afterwards, relative to the market (SPY).
-- Keyed by (item_id, ticker), not by score, so every rubric version is judged
-- against the same outcomes.

CREATE TABLE price_outcomes (
    item_id         text NOT NULL REFERENCES news_items (id),
    ticker          text NOT NULL,
    first_scored_at timestamptz NOT NULL,     -- when the pair was first scored
    entry_ts        timestamptz,              -- first 5-minute bar at or after first_scored_at
    entry_price     double precision,         -- open of that bar
    spy_entry_price double precision,         -- SPY open in the same bar
    ret_1d          double precision,         -- close +N trading days / entry_price - 1
    ret_3d          double precision,
    ret_5d          double precision,
    spy_ret_1d      double precision,
    spy_ret_3d      double precision,
    spy_ret_5d      double precision,
    abn_1d          double precision GENERATED ALWAYS AS (ret_1d - spy_ret_1d) STORED,
    abn_3d          double precision GENERATED ALWAYS AS (ret_3d - spy_ret_3d) STORED,
    abn_5d          double precision GENERATED ALWAYS AS (ret_5d - spy_ret_5d) STORED,
    -- pending: some horizons not closed yet; complete: all filled;
    -- expired: no entry found before 5-minute data aged out (~60 days)
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'complete', 'expired')),
    labeled_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, ticker)
);

CREATE INDEX price_outcomes_status_idx ON price_outcomes (status) WHERE status = 'pending';
