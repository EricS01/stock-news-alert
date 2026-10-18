-- Prices around publication, for the self-learning layer: did the stock already
-- move between the story coming out and our entry? These need 5-minute bars,
-- which yfinance only serves for ~60 days, so they're captured while they still
-- can be. Existing rows start 'pending', so the next label run backfills them.

ALTER TABLE price_outcomes
    -- first 5-minute bar at or after publication (or the first score, if earlier)
    ADD COLUMN pub_ts         timestamptz,
    ADD COLUMN pub_price      double precision,  -- open of that bar
    ADD COLUMN spy_pub_price  double precision,  -- SPY open in the same bar
    -- close of the last trading day before pub_ts's trading day
    ADD COLUMN prev_close     double precision,
    ADD COLUMN spy_prev_close double precision,
    -- pending: retried each label run; complete: all five filled;
    -- unavailable: no publication time, or bars aged out before it was found
    ADD COLUMN context_status text NOT NULL DEFAULT 'pending'
        CHECK (context_status IN ('pending', 'complete', 'unavailable'));

CREATE INDEX price_outcomes_context_idx ON price_outcomes (context_status) WHERE context_status = 'pending';
