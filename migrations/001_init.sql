-- Core tables: fetched news, rubric versions, every score, and job runs.

CREATE TABLE news_items (
    id           text PRIMARY KEY,          -- source-prefixed, e.g. "hn:12345"
    source       text NOT NULL,
    title        text NOT NULL,
    url          text NOT NULL,
    text         text NOT NULL DEFAULT '',
    published_at timestamptz,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    meta         jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX news_items_fetched_at_idx ON news_items (fetched_at);

CREATE TABLE prompt_versions (
    id            text PRIMARY KEY,         -- "v1", "v2", ...
    model         text NOT NULL,
    system_prompt text NOT NULL,
    status        text NOT NULL CHECK (status IN ('active', 'shadow', 'retired')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    notes         text
);

-- Only one rubric may send alerts at a time.
CREATE UNIQUE INDEX prompt_versions_one_active_idx ON prompt_versions (status) WHERE status = 'active';

CREATE TABLE scores (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id        text NOT NULL REFERENCES news_items (id),
    ticker         text NOT NULL,
    prompt_version text NOT NULL REFERENCES prompt_versions (id),
    score          smallint NOT NULL CHECK (score BETWEEN 1 AND 5),
    direction      text NOT NULL CHECK (direction IN ('bullish', 'bearish', 'neutral')),
    reason         text NOT NULL DEFAULT '',
    floor_applied  boolean NOT NULL DEFAULT false,  -- score was raised by the SEC 8-K floor
    alerted        boolean NOT NULL DEFAULT false,
    scored_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_id, ticker, prompt_version)
);

CREATE INDEX scores_scored_at_idx ON scores (scored_at);

CREATE TABLE runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job         text NOT NULL,              -- "alerts", later "label" and "report"
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed')),
    counts      jsonb NOT NULL DEFAULT '{}',
    error       text
);

CREATE INDEX runs_job_started_at_idx ON runs (job, started_at DESC);
