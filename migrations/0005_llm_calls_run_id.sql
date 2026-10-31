-- Attribute model calls to a queue run so the per-PR cost cap can be
-- enforced cumulatively, independent of the optional pr_reviews row.

ALTER TABLE llm_calls ADD COLUMN run_id text;
CREATE INDEX llm_calls_run_id_idx ON llm_calls (run_id, created_at);
