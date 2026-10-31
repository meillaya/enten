from __future__ import annotations

from typing import Final

Catalog = dict[int, dict[tuple[str, str], frozenset[str]]]

BASELINE_COLUMNS: Final[Catalog] = {
    1: {
        ("public", "jobs"): frozenset(
            {
                "id",
                "run_id",
                "kind",
                "state",
                "payload",
                "attempts",
                "created_at",
                "updated_at",
                "last_error",
                "idempotency_key",
                "trace_id",
            }
        ),
        ("public", "pr_reviews"): frozenset(
            {
                "id",
                "repo_id",
                "pr_number",
                "commit_sha",
                "conclusion",
                "cost_usd",
                "tokens_in",
                "tokens_out",
                "run_id",
                "evidence",
                "started_at",
                "completed_at",
            }
        ),
        ("public", "llm_calls"): frozenset(
            {
                "id",
                "review_id",
                "model",
                "tokens_in",
                "tokens_out",
                "cost_usd",
                "duration_ms",
                "tool_use_count",
                "created_at",
            }
        ),
        ("public", "fixtures"): frozenset(
            {"id", "slug", "kind", "repo_url", "base_sha", "head_sha", "expected_findings"}
        ),
        ("public", "sandbox_runs"): frozenset(
            {
                "id",
                "review_id",
                "sandbox_provider",
                "sandbox_id",
                "command",
                "exit_code",
                "started_at",
                "completed_at",
                "evidence_paths",
            }
        ),
    },
    2: {
        ("vec", "chunks"): frozenset(
            {
                "id",
                "repo_id",
                "path",
                "lang",
                "start_line",
                "end_line",
                "text",
                "embedding",
                "symbols",
                "content_hash",
                "indexed_at",
            }
        )
    },
    3: {
        ("public", "jobs"): frozenset({"claimed_by", "claim_generation", "lease_expires_at"}),
        ("public", "review_checkpoints"): frozenset(
            {"run_id", "stage", "generation", "checkpoint", "updated_at"}
        ),
        ("public", "external_effects"): frozenset(
            {
                "id",
                "run_id",
                "effect_key",
                "generation",
                "state",
                "marker",
                "request",
                "external_id",
                "last_error",
                "prepared_at",
                "attempted_at",
                "completed_at",
            }
        ),
    },
}

BASELINE_CONSTRAINTS: Final[Catalog] = {
    1: {
        ("public", "jobs"): frozenset(
            {
                "jobs_attempts_check",
                "jobs_idempotency_key_key",
                "jobs_pkey",
                "jobs_run_id_key",
                "jobs_state_check",
            }
        ),
        ("public", "pr_reviews"): frozenset(
            {
                "pr_reviews_conclusion_check",
                "pr_reviews_pkey",
                "pr_reviews_run_id_fkey",
                "pr_reviews_unique",
            }
        ),
        ("public", "llm_calls"): frozenset({"llm_calls_pkey", "llm_calls_review_id_fkey"}),
        ("public", "fixtures"): frozenset({"fixtures_pkey", "fixtures_slug_key"}),
        ("public", "sandbox_runs"): frozenset({"sandbox_runs_pkey", "sandbox_runs_review_id_fkey"}),
    },
    2: {("vec", "chunks"): frozenset({"chunks_pkey"})},
    3: {
        ("public", "jobs"): frozenset({"jobs_claim_generation_check", "jobs_claim_lease_check"}),
        ("public", "review_checkpoints"): frozenset(
            {
                "review_checkpoints_generation_check",
                "review_checkpoints_pkey",
                "review_checkpoints_run_id_fkey",
                "review_checkpoints_stage_check",
            }
        ),
        ("public", "external_effects"): frozenset(
            {
                "external_effects_completion_check",
                "external_effects_generation_check",
                "external_effects_marker_key",
                "external_effects_pkey",
                "external_effects_run_id_effect_key_key",
                "external_effects_run_id_fkey",
                "external_effects_state_check",
            }
        ),
    },
}

BASELINE_INDEXES: Final[Catalog] = {
    1: {
        ("public", "jobs"): frozenset(
            {
                "jobs_idempotency_key_key",
                "jobs_payload_pr_number_idx",
                "jobs_pkey",
                "jobs_run_id_key",
                "jobs_state_updated_at_idx",
            }
        ),
        ("public", "pr_reviews"): frozenset({"pr_reviews_pkey", "pr_reviews_unique"}),
        ("public", "llm_calls"): frozenset({"llm_calls_pkey", "llm_calls_review_id_idx"}),
        ("public", "fixtures"): frozenset(
            {"fixtures_kind_idx", "fixtures_pkey", "fixtures_slug_key"}
        ),
        ("public", "sandbox_runs"): frozenset({"sandbox_runs_pkey"}),
    },
    2: {
        ("vec", "chunks"): frozenset(
            {
                "chunks_pkey",
                "vec_chunks_embedding_idx",
                "vec_chunks_path_idx",
                "vec_chunks_repo_lang_idx",
            }
        )
    },
    5: {
        ("public", "llm_calls"): frozenset(
            {"llm_calls_pkey", "llm_calls_review_id_idx", "llm_calls_run_id_idx"}
        ),
    },
    3: {
        ("public", "jobs"): frozenset({"jobs_claimable_idx"}),
        ("public", "review_checkpoints"): frozenset({"review_checkpoints_pkey"}),
        ("public", "external_effects"): frozenset(
            {
                "external_effects_marker_key",
                "external_effects_pkey",
                "external_effects_run_id_effect_key_key",
            }
        ),
    },
}
