-- 0001_init.sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id text UNIQUE NOT NULL,
    kind text NOT NULL,
    state text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    idempotency_key text UNIQUE,
    trace_id text,
    CONSTRAINT jobs_state_check CHECK (state IN ('queued','indexing','reviewing','testing','verifying','commenting','done','failed')),
    CONSTRAINT jobs_attempts_check CHECK (attempts >= 0)
);
CREATE INDEX jobs_state_updated_at_idx ON jobs (state, updated_at);
CREATE INDEX jobs_payload_pr_number_idx ON jobs ((payload->>'pr_number'));

CREATE TABLE pr_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id text NOT NULL,
    pr_number int NOT NULL,
    commit_sha text NOT NULL,
    conclusion text NOT NULL,
    cost_usd numeric(10,6) NOT NULL DEFAULT 0,
    tokens_in int NOT NULL DEFAULT 0,
    tokens_out int NOT NULL DEFAULT 0,
    run_id text REFERENCES jobs(run_id) ON DELETE SET NULL,
    evidence jsonb,
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT pr_reviews_conclusion_check CHECK (conclusion IN ('pass','fail','error','skipped')),
    CONSTRAINT pr_reviews_unique UNIQUE (repo_id, pr_number, commit_sha)
);

CREATE TABLE llm_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id uuid REFERENCES pr_reviews(id) ON DELETE CASCADE,
    model text NOT NULL,
    tokens_in int NOT NULL DEFAULT 0,
    tokens_out int NOT NULL DEFAULT 0,
    cost_usd numeric(10,6) NOT NULL DEFAULT 0,
    duration_ms int NOT NULL DEFAULT 0,
    tool_use_count int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_review_id_idx ON llm_calls (review_id, created_at);

CREATE TABLE fixtures (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text UNIQUE NOT NULL,
    kind text NOT NULL,
    repo_url text,
    base_sha text,
    head_sha text,
    expected_findings jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX fixtures_kind_idx ON fixtures (kind);

CREATE TABLE sandbox_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id uuid REFERENCES pr_reviews(id) ON DELETE SET NULL,
    sandbox_provider text NOT NULL,
    sandbox_id text NOT NULL,
    command text NOT NULL,
    exit_code int,
    started_at timestamptz,
    completed_at timestamptz,
    evidence_paths jsonb NOT NULL DEFAULT '[]'::jsonb
);
