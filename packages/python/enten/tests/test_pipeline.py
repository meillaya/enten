from __future__ import annotations

from decimal import Decimal
from pathlib import Path


async def test_pipeline_persists_before_generation_and_records_all_stages(
    tmp_path: Path,
) -> None:
    from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
    from enten.review.run import (
        EffectReceipt,
        Finding,
        GeneratedFindings,
        ReviewRequest,
        VerificationEvidence,
    )

    # Given
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("danger = eval(user_input)\n", encoding="utf-8")
    store = InMemoryReviewStore()

    class Generator:
        calls = 0

        async def generate(self, proposal: object) -> GeneratedFindings:
            self.calls += 1
            assert await store.get_by_key(("local/repo", 7, "abc123")) is not None
            return GeneratedFindings(
                findings=(
                    Finding(
                        category="security",
                        severity="high",
                        message="Avoid dynamic evaluation.",
                        file="app.py",
                        line=1,
                    ),
                ),
                summary="one finding",
                tokens_in=11,
                tokens_out=7,
                cost_usd=Decimal("0.000021"),
            )

    class Verifier:
        calls = 0

        async def verify(self, request: object) -> VerificationEvidence:
            self.calls += 1
            return VerificationEvidence(exit_codes=(0,), command=("pytest",), log_paths=())

    class Effects:
        payloads: list[object]

        def __init__(self) -> None:
            self.payloads = []

        async def publish(self, payload: object) -> EffectReceipt:
            self.payloads.append(payload)
            return EffectReceipt(state="posted", check_run_id="check-1", comment_id="comment-1")

    generator = Generator()
    verifier = Verifier()
    effects = Effects()
    request = ReviewRequest(
        repository_id="local/repo",
        pr_number=7,
        head_sha="abc123",
        repository_path=repository,
        public_repository=True,
        verification_command=("pytest",),
    )

    # When
    result = await execute_review(
        request,
        PipelineDependencies(store=store, generator=generator, verifier=verifier, effects=effects),
    )

    # Then
    assert result.replayed is False
    assert result.run.stage == "completed"
    assert result.run.verification is not None
    assert result.run.verification.verdict == "pass"
    assert result.run.verification.exit_codes == (0,)
    assert result.run.report_receipt is not None
    assert result.run.report_receipt.state == "posted"
    assert store.stage_history == [
        "created",
        "proposed",
        "findings",
        "verified",
        "persisted",
        "reporting",
        "completed",
    ]
    assert generator.calls == verifier.calls == 1
    assert len(effects.payloads) == 1
    payload = effects.payloads[0]
    assert "app.py:1" in str(payload)
    assert len(str(payload)) < 70_000


async def test_pipeline_arbiter_uses_failing_verification_exit_codes(tmp_path: Path) -> None:
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
        async def generate(self, proposal: object) -> GeneratedFindings:
            return GeneratedFindings(findings=(), summary="clean")

    class Verifier:
        async def verify(self, request: object) -> VerificationEvidence:
            return VerificationEvidence(
                exit_codes=(1, 1, 1), command=("pytest",), log_paths=("result.json",)
            )

    class Effects:
        async def publish(self, payload: object) -> EffectReceipt:
            assert "failure" in str(payload)
            return EffectReceipt(state="posted")

    request = ReviewRequest(
        repository_id="local/failure",
        pr_number=0,
        head_sha="deadbeef",
        repository_path=repository,
        public_repository=True,
        verification_command=("pytest",),
    )

    # When
    result = await execute_review(
        request,
        PipelineDependencies(
            store=InMemoryReviewStore(),
            generator=Generator(),
            verifier=Verifier(),
            effects=Effects(),
        ),
    )

    # Then
    assert result.run.verification is not None
    assert result.run.verification.exit_codes == (1, 1, 1)
    assert result.run.verification.verdict == "fail"
