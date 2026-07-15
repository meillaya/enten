from __future__ import annotations

import shutil
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


async def test_pipeline_replay_rebinds_proposal_to_fresh_checkout(tmp_path: Path) -> None:
    from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
    from enten.reporting.effects import NoopEffects
    from enten.review.local import LocalFindingsGenerator
    from enten.review.run import Proposal, ReviewRequest, ReviewRun, VerificationEvidence

    # Given
    old_checkout = tmp_path / "old-checkout"
    old_checkout.mkdir()
    (old_checkout / "app.py").write_text("danger = eval(user_input)\n", encoding="utf-8")
    old_request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a" * 40,
        repository_path=old_checkout,
        public_repository=True,
        local_repository=False,
        verification_command=("true",),
    )
    store = InMemoryReviewStore()
    await store.save(
        ReviewRun(
            review_id="review-1",
            request=old_request,
            stage="proposed",
            proposal=Proposal(
                repository_path=old_checkout,
                files=("app.py",),
                verification_command=("true",),
            ),
        )
    )
    shutil.rmtree(old_checkout)
    fresh_checkout = tmp_path / "fresh-checkout"
    fresh_checkout.mkdir()
    (fresh_checkout / "app.py").write_text("danger = eval(user_input)\n", encoding="utf-8")
    fresh_request = old_request.model_copy(update={"repository_path": fresh_checkout})

    class Verifier:
        async def verify(self, request: ReviewRequest) -> VerificationEvidence:
            assert request.repository_path == fresh_checkout
            return VerificationEvidence(exit_codes=(0,), command=("true",), log_paths=())

    # When
    result = await execute_review(
        fresh_request,
        PipelineDependencies(
            store=store,
            generator=LocalFindingsGenerator(),
            verifier=Verifier(),
            effects=NoopEffects(),
        ),
    )

    # Then
    assert result.run.proposal is not None
    assert result.run.proposal.repository_path == fresh_checkout
    assert result.run.findings is not None
    assert [finding.file for finding in result.run.findings.findings] == ["app.py"]


async def test_completed_checkpoint_replay_reconciles_queue_done_after_crash(
    tmp_path: Path,
) -> None:
    from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
    from enten.review.run import (
        EffectReceipt,
        GeneratedFindings,
        ReviewRequest,
        ReviewStage,
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
            return EffectReceipt(state="posted")

    class CrashAfterCompletedCheckpoint:
        completed_calls = 0

        async def transition(self, stage: ReviewStage) -> None:
            if stage != "completed":
                return
            self.completed_calls += 1
            if self.completed_calls == 1:
                raise RuntimeError("worker crashed before queue done")

    store = InMemoryReviewStore()
    generator = Generator()
    verifier = Verifier()
    effects = Effects()
    stage_sink = CrashAfterCompletedCheckpoint()
    dependencies = PipelineDependencies(
        store=store,
        generator=generator,
        verifier=verifier,
        effects=effects,
        stage_sink=stage_sink,
    )
    request = ReviewRequest(
        repository_id="public/repo",
        pr_number=42,
        head_sha="b" * 40,
        repository_path=repository,
        public_repository=True,
        verification_command=("true",),
    )

    # When
    with pytest.raises(RuntimeError, match="before queue done"):
        await execute_review(request, dependencies)
    replay = await execute_review(request, dependencies)

    # Then
    assert replay.replayed is True
    assert replay.run.stage == "completed"
    assert stage_sink.completed_calls == 2
    assert generator.calls == verifier.calls == effects.calls == 1
