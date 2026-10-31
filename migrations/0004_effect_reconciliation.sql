-- Allow an operator to adopt an observed external effect after an ambiguous
-- attempt. The external ID must be present, so the transition records what was
-- actually found rather than guessing.

CREATE OR REPLACE FUNCTION guard_external_effect_transition() RETURNS trigger
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
    IF OLD.state = 'indeterminate' AND NEW.state = 'succeeded'
       AND NEW.external_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid external effect transition: % -> %', OLD.state, NEW.state
        USING ERRCODE = 'check_violation';
END;
$$;
