from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest


def test_review_contract_digest_matches_the_shared_edge_contract() -> None:
    from enten.review.contract import review_contract_digest

    digest = review_contract_digest(
        "python -m compileall -q .",
        "python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28",
    )

    assert digest == "d8cd300e795765debd5620f2e5bc68ab5fd408722774b6258266eeb55fc91e08"


def test_python_parser_consumes_the_shared_contract_fixtures() -> None:
    from pydantic import ValidationError

    from enten.pipeline import ReviewJobEnvelope

    fixtures = Path(__file__).parents[4] / "contracts" / "fixtures"
    valid = json.loads((fixtures / "pr-job-envelope.valid.json").read_text(encoding="utf-8"))
    invalid = json.loads((fixtures / "pr-job-envelope.invalid.json").read_text(encoding="utf-8"))
    invalid_coercions = json.loads(
        (fixtures / "pr-job-envelope.invalid-coercions.json").read_text(encoding="utf-8")
    )
    valid_delivery_ids = json.loads(
        (fixtures / "pr-job-envelope.valid-delivery-ids.json").read_text(encoding="utf-8")
    )
    valid_numeric_values = json.loads(
        (fixtures / "pr-job-envelope.valid-numeric-values.json").read_text(encoding="utf-8")
    )
    unicode_boundaries = json.loads(
        (fixtures / "pr-job-envelope.unicode-boundaries.json").read_text(encoding="utf-8")
    )

    ReviewJobEnvelope.model_validate(valid)
    for delivery_id in valid_delivery_ids:
        candidate = copy.deepcopy(valid)
        candidate["delivery_id"] = delivery_id
        ReviewJobEnvelope.model_validate(candidate)
    for case in valid_numeric_values:
        candidate = copy.deepcopy(valid)
        target = candidate
        for segment in case["path"][:-1]:
            target = target[segment]
        target[case["path"][-1]] = case["value"]
        ReviewJobEnvelope.model_validate(candidate)
    for case in unicode_boundaries:
        valid_text = case["prefix"] + case["unit"] * case["valid_count"]
        invalid_text = case["prefix"] + case["unit"] * case["invalid_count"]
        for text, accepted in ((valid_text, True), (invalid_text, False)):
            candidate = copy.deepcopy(valid)
            target = candidate
            for segment in case["path"][:-1]:
                target = target[segment]
            target[case["path"][-1]] = text
            if accepted:
                ReviewJobEnvelope.model_validate(candidate)
            else:
                with pytest.raises(ValidationError):
                    ReviewJobEnvelope.model_validate(candidate)
    with pytest.raises(ValidationError):
        ReviewJobEnvelope.model_validate(invalid)
    for case in invalid_coercions:
        candidate = copy.deepcopy(valid)
        target = candidate
        path = case["path"]
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = case["value"]
        with pytest.raises(ValidationError):
            ReviewJobEnvelope.model_validate(candidate)


def test_job_envelope_parses_exact_contract_shape(tmp_path: Path) -> None:
    from enten.pipeline import ReviewJobEnvelope

    # Given / When
    envelope = ReviewJobEnvelope.model_validate(
        {
            "version": "1",
            "installation_id": 9,
            "delivery_id": "11111111-1111-4111-8111-111111111111",
            "repository": {"id": 101, "full_name": "public/repo"},
            "pull_request": {
                "number": 42,
                "head_sha": "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
            },
            "action": "synchronize",
            "actor": {"id": 4, "login": "alice"},
            "review_contract": "b" * 64,
            "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
        }
    )

    # Then
    request = envelope.to_request(repository_path=tmp_path)
    assert request.key == (
        "101",
        42,
        "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        "b" * 64,
    )
    assert request.repository_id == "101"
    assert request.repository_full_name == "public/repo"
    assert request.verification_command == ("python", "-m", "compileall", "-q", ".")

    with pytest.raises(ValueError, match="review contract"):
        envelope.to_request(
            repository_path=tmp_path,
            expected_review_contract="c" * 64,
        )


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("version",), "2"),
        (("installation_id",), 0),
        (("delivery_id",), "not-a-uuid"),
        (("repository", "id"), 0),
        (("repository", "full_name"), "missing-owner"),
        (("repository", "full_name"), f"owner/{'r' * 250}"),
        (("pull_request", "head_sha"), "not-a-sha"),
        (("action",), "closed"),
        (("actor", "id"), 0),
        (("actor", "login"), "a" * 256),
        (("review_contract",), "invalid"),
        (("traceparent",), "invalid"),
    ],
)
def test_job_envelope_rejects_values_outside_shared_contract(
    path: tuple[str, ...], invalid_value: str | int
) -> None:
    from pydantic import ValidationError

    from enten.pipeline import ReviewJobEnvelope

    # Given
    valid: dict[str, object] = {
        "version": "1",
        "installation_id": 9,
        "delivery_id": "11111111-1111-4111-8111-111111111111",
        "repository": {"id": 101, "full_name": "public/repo"},
        "pull_request": {
            "number": 42,
            "head_sha": "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        },
        "action": "opened",
        "actor": {"id": 4, "login": "alice"},
        "review_contract": "b" * 64,
        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    }
    invalid = copy.deepcopy(valid)
    target = invalid
    for segment in path[:-1]:
        nested = target[segment]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = invalid_value

    # When / Then
    with pytest.raises(ValidationError):
        ReviewJobEnvelope.model_validate(invalid)


def test_private_remote_repository_is_rejected(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from enten.review.run import ReviewRequest

    # Given / When / Then
    with pytest.raises(ValidationError):
        ReviewRequest(
            repository_id="private/repo",
            repository_full_name="private/repo",
            pr_number=1,
            head_sha="abc",
            repository_path=tmp_path,
            public_repository=False,
            local_repository=False,
            verification_command=("true",),
        )


async def test_worker_delegates_job_envelope_to_shared_stage_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from enten import worker as worker_module
    from enten.pipeline import InMemoryReviewStore, PipelineDependencies
    from enten.reporting.effects import NoopEffects
    from enten.review.local import LocalFindingsGenerator, LocalVerifier
    from enten.review.run import PipelineResult, ReviewRequest, ReviewRun

    # Given
    captured_requests: list[ReviewRequest] = []
    captured_dependencies: list[PipelineDependencies] = []
    dependencies = PipelineDependencies(
        store=InMemoryReviewStore(),
        generator=LocalFindingsGenerator(),
        verifier=LocalVerifier(),
        effects=NoopEffects(),
    )

    class CheckoutValidator:
        async def validate(self, request: ReviewRequest) -> None:
            captured_requests.append(request)

    async def shared_execute(
        request: ReviewRequest, injected: PipelineDependencies
    ) -> PipelineResult:
        captured_requests.append(request)
        captured_dependencies.append(injected)
        return PipelineResult(
            run=ReviewRun(
                review_id="review-1",
                request=request,
                stage="completed",
            ),
            replayed=False,
        )

    monkeypatch.setattr(worker_module, "execute_review", shared_execute, raising=False)
    payload = {
        "version": "1",
        "installation_id": 9,
        "delivery_id": "11111111-1111-4111-8111-111111111111",
        "repository": {"id": 101, "full_name": "public/repo"},
        "pull_request": {
            "number": 42,
            "head_sha": "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        },
        "action": "opened",
        "actor": {"id": 4, "login": "alice"},
        "review_contract": "b" * 64,
        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    }

    # When
    result = await worker_module.run_pipeline(
        "queue-run-1",
        payload=payload,
        dependencies=dependencies,
        repository_path=tmp_path,
        checkout_validator=CheckoutValidator(),
    )

    # Then
    assert result.run.stage == "completed"
    assert len(captured_requests) == 2
    assert len(captured_dependencies) == 1
    request = captured_requests[-1]
    assert request.key == (
        "101",
        42,
        "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        "b" * 64,
    )
    assert captured_dependencies[0] is dependencies


async def test_worker_stage_sink_stops_when_generation_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from enten import worker as worker_module

    # Given
    async def stale_mark_state(run_id: str, state: str, *, generation: int | None = None) -> bool:
        _ = (run_id, state, generation)
        return False

    monkeypatch.setattr("enten.worker_pipeline.mark_state", stale_mark_state)
    sink = worker_module.QueueStageSink("run-1", generation=7)

    # When / Then
    with pytest.raises(worker_module.StaleJobLeaseError):
        await sink.transition("proposed")
