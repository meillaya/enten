from __future__ import annotations

from pathlib import Path

import pytest

from enten.pipeline import InMemoryReviewStore, PipelineDependencies
from enten.reporting.effects import NoopEffects
from enten.review.local import LocalFindingsGenerator
from enten.review.run import ReviewRequest
from enten.sandbox.models import ExecResult, SandboxHandle


async def test_sandbox_verifier_runs_remote_command_in_ephemeral_provider(tmp_path: Path) -> None:
    from enten.review.remote import SandboxVerifier

    # Given
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    class Provider:
        created: dict[str, str] | None = None
        command: list[str] | None = None
        destroyed = False

        async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
            self.created = repo_files
            return SandboxHandle(id="sandbox-1", provider="fake", image="fake")

        async def run(
            self,
            handle: SandboxHandle,
            cmd: list[str],
            timeout_seconds: int = 120,
        ) -> ExecResult:
            _ = (handle, timeout_seconds)
            self.command = cmd
            return ExecResult(exit_code=2, stdout="", stderr="failure", duration_ms=5)

        async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
            _ = (handle, files)

        async def destroy(self, handle: SandboxHandle) -> None:
            _ = handle
            self.destroyed = True

    provider = Provider()
    request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        repository_path=tmp_path,
        public_repository=True,
        local_repository=False,
        verification_command=("pytest", "-q"),
    )

    # When
    evidence = await SandboxVerifier(provider).verify(request)

    # Then
    assert evidence.exit_codes == (2,)
    assert provider.created == {"app.py": "x = 1\n"}
    assert provider.command == ["pytest", "-q"]
    assert provider.destroyed is True


async def test_worker_remote_job_fails_closed_without_checkout_validation(tmp_path: Path) -> None:
    from enten import worker as worker_module
    from enten.review.local import LocalVerifier
    from enten.review.remote import UnvalidatedRemoteCheckoutError

    # Given
    dependencies = PipelineDependencies(
        store=InMemoryReviewStore(),
        generator=LocalFindingsGenerator(),
        verifier=LocalVerifier(),
        effects=NoopEffects(),
    )
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
        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    }

    # When / Then
    with pytest.raises(UnvalidatedRemoteCheckoutError):
        await worker_module.run_pipeline(
            "queue-run-1",
            payload=payload,
            dependencies=dependencies,
            repository_path=tmp_path,
        )


async def test_public_checkout_validator_requires_requested_pr_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from enten.review import remote
    from enten.review.remote import GitPublicCheckoutValidator, UnvalidatedRemoteCheckoutError

    request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        repository_path=tmp_path,
        public_repository=True,
        local_repository=False,
        verification_command=("true",),
    )

    async def fake_git(*arguments: str) -> str:
        if "rev-parse" in arguments:
            return request.head_sha
        if "get-url" in arguments:
            return "https://github.com/public/repo.git"
        return "b84d88e7554fc1fa21bcbc4efae3c0a85e3a0000\trefs/pull/42/head"

    monkeypatch.setattr(remote, "_git", fake_git)

    with pytest.raises(UnvalidatedRemoteCheckoutError, match="public pull request ref"):
        await GitPublicCheckoutValidator().validate(request)


async def test_public_pr_checkout_fetches_exact_ref_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from enten.review import remote
    from enten.review.remote import public_pull_request_checkout

    head_sha = "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000"
    calls: list[tuple[str, ...]] = []

    async def fake_git(*arguments: str) -> str:
        calls.append(arguments)
        return head_sha if "rev-parse" in arguments else ""

    monkeypatch.setattr(remote, "_git", fake_git)

    async with public_pull_request_checkout(
        repository_full_name="public/repo",
        pr_number=42,
        head_sha=head_sha,
    ) as checkout:
        created_checkout = checkout
        assert checkout.is_dir()

    assert not created_checkout.exists()
    assert any(
        "fetch" in call and "refs/pull/42/head" in call and "--depth=1" in call for call in calls
    )
