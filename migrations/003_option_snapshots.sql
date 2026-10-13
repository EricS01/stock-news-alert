-- Near-the-money option quotes around alerts, for paper strategy P&L.
-- Paper only: the app records quotes, it never places trades.

CREATE TABLE option_snapshots (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id          text NOT NULL REFERENCES news_items (id),
    ticker           text NOT NULL,
    contract_symbol  text NOT NULL,           -- OCC symbol, e.g. NVDA261016C00227500
    expiry           date NOT NULL,
    strike           double precision NOT NULL,
    "right"          char(1) NOT NULL CHECK ("right" IN ('C', 'P')),  -- quoted: RIGHT is a keyword
    kind             text NOT NULL CHECK (kind IN ('entry', 'd1', 'd3')),
    taken_at         timestamptz NOT NULL DEFAULT now(),
    underlying_price double precision,
    bid              double precision,
    ask              double precision,
    last             double precision,
    iv               double precision,
    volume           integer,
    open_interest    integer,
    UNIQUE (item_id, ticker, contract_symbol, kind)
);

-- Alerts waiting for their entry snapshot (taken during market hours).
CREATE TABLE option_snapshot_queue (
    item_id    text NOT NULL REFERENCES news_items (id),
    ticker     text NOT NULL,
    queued_at  timestamptz NOT NULL DEFAULT now(),
    attempts   integer NOT NULL DEFAULT 0,
    last_error text,
    done_at    timestamptz,                   -- set when taken, or when given up on
    PRIMARY KEY (item_id, ticker)
);

CREATE INDEX option_snapshot_queue_open_idx ON option_snapshot_queue (queued_at) WHERE done_at IS NULL;
