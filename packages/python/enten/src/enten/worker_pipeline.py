from __future__ import annotations

from pathlib import Path

from enten.pipeline import (
    FindingsGenerator,
    HypothesisGenerator,
    JsonReviewStore,
    PipelineDependencies,
    ReviewEffects,
    ReviewStore,
    ReviewVerifier,
)
from enten.queue import mark_state
from enten.reporting.effects import FencedGitHubEffects, GitHubEffectClient, NoopEffects
from enten.review.local import LocalFindingsGenerator, LocalVerifier
from enten.review.postgres_store import PostgresReviewStore
from enten.review.python_probes import ProbeRunner
from enten.review.run import ReviewStage

_QUEUE_STATE_BY_STAGE: dict[ReviewStage, str] = {
    "created": "indexing",
    "proposed": "reviewing",
    "hypothesized": "testing",
    "probing": "testing",
    "findings": "testing",
    "verified": "verifying",
    "persisted": "commenting",
    "reporting": "commenting",
    "completed": "done",
}


class StaleJobLeaseError(RuntimeError):
    pass


class QueueStageSink:
    def __init__(self, run_id: str, generation: int | None) -> None:
        self._run_id = run_id
        self._generation = generation

    async def transition(self, stage: ReviewStage) -> None:
        updated = await mark_state(
            self._run_id,
            _QUEUE_STATE_BY_STAGE[stage],
            generation=self._generation,
        )
        if not updated:
            raise StaleJobLeaseError(
                f"job lease generation {self._generation} no longer owns {self._run_id}"
            )


def build_pipeline_dependencies(
    run_id: str,
    *,
    effects: ReviewEffects | None = None,
    github_client: GitHubEffectClient | None = None,
    store: ReviewStore | None = None,
    store_path: Path | None = None,
    generation: int | None = None,
    verifier: ReviewVerifier | None = None,
    generator: FindingsGenerator | None = None,
    hypothesis_generator: HypothesisGenerator | None = None,
    probe_runner: ProbeRunner | None = None,
    probe_publish: bool = True,
) -> PipelineDependencies:
    if effects is not None and github_client is not None:
        raise ValueError("provide either explicit effects or a production GitHub client")
    if github_client is not None and generation is None:
        raise ValueError("production GitHub effects require a queue lease generation")
    resolved_effects = effects
    if github_client is not None:
        assert generation is not None
        resolved_effects = FencedGitHubEffects(
            github_client,
            run_id=run_id,
            generation=generation,
        )
    resolved_store = store
    if resolved_store is None:
        if generation is not None and store_path is None:
            resolved_store = PostgresReviewStore(run_id=run_id, generation=generation)
        else:
            path = store_path or Path(".enten/worker-reviews.json")
            resolved_store = JsonReviewStore(path)
    return PipelineDependencies(
        store=resolved_store,
        generator=generator or LocalFindingsGenerator(),
        verifier=verifier or LocalVerifier(),
        effects=resolved_effects or NoopEffects(),
        hypothesis_generator=hypothesis_generator,
        probe_runner=probe_runner,
        probe_publish=probe_publish,
        stage_sink=QueueStageSink(run_id, generation),
    )


__all__ = [
    "QueueStageSink",
    "StaleJobLeaseError",
    "build_pipeline_dependencies",
]
