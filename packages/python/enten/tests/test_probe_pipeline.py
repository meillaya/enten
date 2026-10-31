"""Pipeline coverage for the Python hypothesis and probe stages."""

from __future__ import annotations

import hashlib
from pathlib import Path

from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
from enten.review.run import (
    EffectReceipt,
    GeneratedFindings,
    Hypothesis,
    HypothesisSet,
    ProbeEvidence,
    ProbeResult,
    ReviewRequest,
    VerificationEvidence,
)

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


class _Generator:
    async def generate(self, proposal: object) -> GeneratedFindings:
        _ = proposal
        return GeneratedFindings(findings=())


class _Hypotheses:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, proposal: object) -> HypothesisSet:
        _ = proposal
        self.calls += 1
        source = "import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n"
        return HypothesisSet(
            hypotheses=(
                Hypothesis(
                    id="hyp-1",
                    category="bug",
                    severity="high",
                    message="Subtraction is used.",
                    file="app.py",
                    line=2,
                    test_source=source,
                    test_sha256=hashlib.sha256(source.encode()).hexdigest(),
                ),
            )
        )


class _Verifier:
    async def verify(self, request: object) -> VerificationEvidence:
        _ = request
        return VerificationEvidence(exit_codes=(0, 0, 0), command=("true",), log_paths=())


class _Effects:
    async def publish(self, payload: object) -> EffectReceipt:
        _ = payload
        return EffectReceipt(state="posted", check_run_id="check", comment_id="comment")

    async def reconcile(self, payload: object) -> EffectReceipt:
        _ = payload
        return EffectReceipt(state="posted", check_run_id="check", comment_id="comment")


class _Probes:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, hypotheses: HypothesisSet, proposal: object) -> ProbeEvidence:
        _ = proposal
        self.calls += 1
        hypothesis = hypotheses.hypotheses[0]
        return ProbeEvidence(
            results=(
                ProbeResult(
                    hypothesis_id=hypothesis.id,
                    status="confirmed",
                    exit_code=1,
                    duration_ms=12,
                    output_excerpt="app.py:2: AssertionError",
                    test_sha256=hypothesis.test_sha256,
                ),
            )
        )


def _request(tmp_path: Path) -> ReviewRequest:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return ReviewRequest(
        repository_id="local/repo",
        pr_number=1,
        head_sha="a" * 40,
        repository_path=repository,
        verification_command=("true",),
        diff=DIFF,
    )


async def test_probe_stages_confirm_and_publish(tmp_path: Path) -> None:
    request = _request(tmp_path)
    hypotheses = _Hypotheses()
    probes = _Probes()
    store = InMemoryReviewStore()

    result = await execute_review(
        request,
        PipelineDependencies(
            store=store,
            generator=_Generator(),
            verifier=_Verifier(),
            effects=_Effects(),
            hypothesis_generator=hypotheses,
            probe_runner=probes,
        ),
    )

    assert result.run.stage == "completed"
    assert result.run.probes is not None
    assert result.run.findings is not None
    assert len(result.run.findings.findings) == 1
    assert result.run.findings.findings[0].evidence is not None
    assert store.stage_history == [
        "created",
        "proposed",
        "hypothesized",
        "probing",
        "findings",
        "verified",
        "persisted",
        "reporting",
        "completed",
    ]
    assert hypotheses.calls == 1
    assert probes.calls == 1


async def test_shadow_mode_records_probes_without_publishing_findings(tmp_path: Path) -> None:
    result = await execute_review(
        _request(tmp_path),
        PipelineDependencies(
            store=InMemoryReviewStore(),
            generator=_Generator(),
            verifier=_Verifier(),
            effects=_Effects(),
            hypothesis_generator=_Hypotheses(),
            probe_runner=_Probes(),
            probe_publish=False,
        ),
    )

    assert result.run.probes is not None
    assert result.run.findings is not None
    assert result.run.findings.findings == ()
    assert "shadow" in result.run.findings.summary


def test_postgres_store_maps_every_review_stage() -> None:
    from enten.review.postgres_store import _QUEUE_STAGE
    from enten.review.run import ReviewStage

    assert set(_QUEUE_STAGE) == set(ReviewStage.__args__)
