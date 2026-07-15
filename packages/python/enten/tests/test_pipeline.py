from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest


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
            assert await store.get_by_key(request.key) is not None
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

        async def reconcile(self, payload: object) -> EffectReceipt:
            raise AssertionError(payload)

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


@pytest.mark.parametrize(
    ("exit_codes", "expected_verdict"),
    [
        ((1, 1, 1), "fail"),
        ((1, 0, 1), "fail"),
        ((0, 1, 0), "flaky"),
    ],
)
async def test_pipeline_arbiter_requires_two_successful_verification_runs(
    tmp_path: Path,
    exit_codes: tuple[int, int, int],
    expected_verdict: str,
) -> None:
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
                exit_codes=exit_codes, command=("pytest",), log_paths=("result.json",)
            )

    class Effects:
        async def publish(self, payload: object) -> EffectReceipt:
            return EffectReceipt(state="posted")

        async def reconcile(self, payload: object) -> EffectReceipt:
            raise AssertionError(payload)

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
    assert result.run.verification.exit_codes == exit_codes
    assert result.run.verification.verdict == expected_verdict


async def test_pipeline_persists_and_reports_verifier_infrastructure_error(
    tmp_path: Path,
) -> None:
    from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
    from enten.review.run import (
        EffectReceipt,
        GeneratedFindings,
        ReportPayload,
        ReviewRequest,
        VerificationEvidence,
    )

    class Generator:
        async def generate(self, proposal: object) -> GeneratedFindings:
            return GeneratedFindings(findings=(), summary="clean")

    class Verifier:
        async def verify(self, request: object) -> VerificationEvidence:
            return VerificationEvidence(
                exit_codes=(0, 0),
                command=("pytest",),
                log_paths=(),
                verdict="error",
                infrastructure_detail="sandbox transport disconnected",
            )

    class Effects:
        payload: ReportPayload | None = None

        async def publish(self, payload: ReportPayload) -> EffectReceipt:
            self.payload = payload
            return EffectReceipt(state="posted")

        async def reconcile(self, payload: ReportPayload) -> EffectReceipt:
            raise AssertionError(payload)

    store = InMemoryReviewStore()
    effects = Effects()
    request = ReviewRequest(
        repository_id="public/error",
        repository_full_name="public/error",
        pr_number=9,
        head_sha="d" * 40,
        repository_path=tmp_path,
        public_repository=True,
        verification_command=("pytest",),
    )

    result = await execute_review(
        request,
        PipelineDependencies(
            store=store,
            generator=Generator(),
            verifier=Verifier(),
            effects=effects,
        ),
    )

    assert result.run.stage == "completed"
    assert result.run.verification is not None
    assert result.run.verification.verdict == "error"
    assert effects.payload is not None
    assert effects.payload.conclusion == "failure"
    assert "Infrastructure error: sandbox transport disconnected" in effects.payload.comment
    assert store.stage_history[-3:] == ["persisted", "reporting", "completed"]


def test_report_payload_sanitizes_hostile_anchored_filename(tmp_path: Path) -> None:
    from enten.reporting.payload import build_report, comment_marker
    from enten.review.run import (
        Finding,
        GeneratedFindings,
        Proposal,
        ReviewRequest,
        ReviewRun,
        VerificationEvidence,
    )

    hostile = "src/evil`\n@octocat\x00.py"
    request = ReviewRequest(
        repository_id="public/repo",
        repository_full_name="public/repo",
        pr_number=4,
        head_sha="e" * 40,
        repository_path=tmp_path,
        verification_command=("true",),
    )
    run = ReviewRun(
        review_id="review-4",
        request=request,
        stage="persisted",
        proposal=Proposal(
            repository_path=tmp_path,
            files=(hostile,),
            verification_command=("true",),
        ),
        findings=GeneratedFindings(
            findings=(
                Finding(
                    category="security",
                    severity="high",
                    message="anchored",
                    file=hostile,
                    line=5,
                ),
            )
        ),
        verification=VerificationEvidence(
            exit_codes=(1,), command=("true",), log_paths=(), verdict="fail"
        ),
    )

    payload = build_report(run)

    assert comment_marker(request.head_sha) in payload.comment
    assert f"Reviewed commit: `{request.head_sha}`" in payload.comment
    assert hostile not in payload.comment
    assert "@octocat" not in payload.comment
    assert "\x00" not in payload.comment
    assert "src/evilˋ�＠octocat�.py:5" in payload.comment
