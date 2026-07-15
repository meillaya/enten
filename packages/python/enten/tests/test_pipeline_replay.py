from __future__ import annotations

from pathlib import Path

import pytest


async def test_pipeline_replay_reuses_completed_key_without_effects(tmp_path: Path) -> None:
    from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
    from enten.review.run import (
        EffectReceipt,
        GeneratedFindings,
        ReviewRequest,
        VerificationEvidence,
    )

    # Given
    repository = tmp_path / "repo"
    repository.mkdir()

    class Generator:
        calls = 0

        async def generate(self, proposal: object) -> GeneratedFindings:
            self.calls += 1
            return GeneratedFindings(findings=(), summary="clean")

    class Verifier:
        calls = 0

        async def verify(self, request: object) -> VerificationEvidence:
            self.calls += 1
            return VerificationEvidence(exit_codes=(0,), command=("true",), log_paths=())

    class Effects:
        calls = 0

        async def publish(self, payload: object) -> EffectReceipt:
            self.calls += 1
            return EffectReceipt(state="posted", check_run_id="1", comment_id="2")

    store = InMemoryReviewStore()
    generator = Generator()
    verifier = Verifier()
    effects = Effects()
    dependencies = PipelineDependencies(
        store=store, generator=generator, verifier=verifier, effects=effects
    )
    request = ReviewRequest(
        repository_id="public/repo",
        pr_number=42,
        head_sha="facefeed",
        repository_path=repository,
        public_repository=True,
        verification_command=("true",),
    )

    # When
    first = await execute_review(request, dependencies)
    replay = await execute_review(request, dependencies)

    # Then
    assert replay.replayed is True
    assert replay.run == first.run
    assert generator.calls == verifier.calls == effects.calls == 1


async def test_pipeline_does_not_retry_ambiguous_external_effect(tmp_path: Path) -> None:
    from enten.pipeline import (
        IndeterminateEffectError,
        InMemoryReviewStore,
        PipelineDependencies,
        execute_review,
    )
    from enten.review.run import (
        EffectReceipt,
        GeneratedFindings,
        ReviewRequest,
        VerificationEvidence,
    )

    # Given
    repository = tmp_path / "repo"
    repository.mkdir()

    class Generator:
        async def generate(self, proposal: object) -> GeneratedFindings:
            return GeneratedFindings(findings=(), summary="clean")

    class Verifier:
        async def verify(self, request: object) -> VerificationEvidence:
            return VerificationEvidence(exit_codes=(0,), command=("true",), log_paths=())

    class Effects:
        calls = 0

        async def publish(self, payload: object) -> EffectReceipt:
            self.calls += 1
            raise TimeoutError("outcome unknown")

    effects = Effects()
    dependencies = PipelineDependencies(
        store=InMemoryReviewStore(),
        generator=Generator(),
        verifier=Verifier(),
        effects=effects,
    )
    request = ReviewRequest(
        repository_id="public/repo",
        pr_number=42,
        head_sha="badcafe",
        repository_path=repository,
        public_repository=True,
        verification_command=("true",),
    )

    # When / Then
    with pytest.raises(IndeterminateEffectError):
        await execute_review(request, dependencies)
    with pytest.raises(IndeterminateEffectError):
        await execute_review(request, dependencies)
    assert effects.calls == 1
