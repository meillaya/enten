ALTER TABLE jobs
    ADD COLUMN claimed_by text,
    ADD COLUMN claim_generation bigint NOT NULL DEFAULT 0,
    ADD COLUMN lease_expires_at timestamptz,
    ADD CONSTRAINT jobs_claim_generation_check CHECK (claim_generation >= 0),
    ADD CONSTRAINT jobs_claim_lease_check CHECK (
        (claimed_by IS NULL AND lease_expires_at IS NULL)
        OR (claimed_by IS NOT NULL AND lease_expires_at IS NOT NULL)
    );

CREATE INDEX jobs_claimable_idx
    ON jobs (state, lease_expires_at, created_at);

CREATE TABLE review_checkpoints (
    run_id text NOT NULL REFERENCES jobs(run_id) ON DELETE CASCADE,
    stage text NOT NULL,
    generation bigint NOT NULL,
    checkpoint jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, stage),
    CONSTRAINT review_checkpoints_stage_check CHECK (
        stage IN ('indexing','reviewing','testing','verifying','commenting')
    ),
    CONSTRAINT review_checkpoints_generation_check CHECK (generation > 0)
);

CREATE TABLE external_effects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id text NOT NULL REFERENCES jobs(run_id) ON DELETE CASCADE,
    effect_key text NOT NULL,
    generation bigint NOT NULL,
    state text NOT NULL,
    marker text NOT NULL UNIQUE,
    request jsonb NOT NULL,
    external_id text,
    last_error text,
    prepared_at timestamptz NOT NULL DEFAULT now(),
    attempted_at timestamptz,
    completed_at timestamptz,
    UNIQUE (run_id, effect_key),
    CONSTRAINT external_effects_generation_check CHECK (generation > 0),
    CONSTRAINT external_effects_state_check CHECK (
        state IN ('prepared','attempted','succeeded','indeterminate')
    ),
    CONSTRAINT external_effects_completion_check CHECK (
        (state = 'succeeded' AND external_id IS NOT NULL AND completed_at IS NOT NULL)
        OR state <> 'succeeded'
    )
);

CREATE FUNCTION guard_external_effect_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = NEW.state THEN
        RETURN NEW;
    END IF;
    IF OLD.state = 'prepared' AND NEW.state = 'attempted' THEN
        RETURN NEW;
    END IF;
    IF OLD.state = 'attempted' AND NEW.state IN ('succeeded', 'indeterminate') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid external effect transition: % -> %', OLD.state, NEW.state
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER external_effect_state_guard
BEFORE UPDATE OF state ON external_effects
FOR EACH ROW EXECUTE FUNCTION guard_external_effect_transition();
