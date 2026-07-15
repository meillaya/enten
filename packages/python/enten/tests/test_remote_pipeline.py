from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from enten.pipeline import InMemoryReviewStore, PipelineDependencies
from enten.reporting.effects import NoopEffects
from enten.review.local import LocalFindingsGenerator
from enten.review.run import ReviewRequest
from enten.sandbox.models import ExecResult, SandboxHandle


async def test_sandbox_verifier_runs_remote_command_in_ephemeral_provider(tmp_path: Path) -> None:
    from enten.review.remote import SOURCE_MANIFEST_PATH, SandboxVerifier

    # Given
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    class Provider:
        created: list[dict[str, str]]
        commands: list[list[str]]
        destroyed: list[str]

        def __init__(self) -> None:
            self.created = []
            self.commands = []
            self.destroyed = []

        async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
            self.created.append(repo_files)
            return SandboxHandle(
                id=f"sandbox-{len(self.created)}",
                provider="fake",
                image="fake",
            )

        async def run(
            self,
            handle: SandboxHandle,
            cmd: list[str],
            timeout_seconds: int = 120,
        ) -> ExecResult:
            _ = (handle, timeout_seconds)
            self.commands.append(cmd)
            exit_code = (0, 2, 0)[len(self.commands) - 1]
            return ExecResult(exit_code=exit_code, stdout="", stderr="failure", duration_ms=5)

        async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
            _ = (handle, files)

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroyed.append(handle.id)

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
    assert evidence.exit_codes == (0, 2, 0)
    assert evidence.verdict == "flaky"
    assert len(provider.created) == 3
    manifest_payload = provider.created[0][SOURCE_MANIFEST_PATH]
    manifest = json.loads(manifest_payload)
    assert manifest == {
        "head_sha": "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        "repository_full_name": "public/repo",
        "repository_id": "101",
        "source_digest": "d4cd0ccbfb3cd532d848a28b3f15507357f8add83cd3c98cb4ace35abe0fddb2",
        "version": 1,
    }
    assert manifest_payload == (
        '{"head_sha":"a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",'
        '"repository_full_name":"public/repo","repository_id":"101",'
        '"source_digest":"d4cd0ccbfb3cd532d848a28b3f15507357f8add83cd3c98cb4ace35abe0fddb2",'
        '"version":1}'
    )
    assert all(created["app.py"] == "x = 1\n" for created in provider.created)
    assert provider.commands == [["pytest", "-q"]] * 3
    assert provider.destroyed == ["sandbox-1", "sandbox-2", "sandbox-3"]


@pytest.mark.parametrize(
    ("exit_codes", "expected"),
    [
        ((0, 0, 0), "pass"),
        ((0, 1, 0), "flaky"),
        ((1, 0, 1), "fail"),
        ((124, 124, 124), "fail"),
    ],
)
async def test_sandbox_verifier_classifies_three_fresh_runs_and_cleans_up(
    tmp_path: Path,
    exit_codes: tuple[int, int, int],
    expected: str,
) -> None:
    from enten.review.remote import SandboxVerifier

    # Given
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    class Provider:
        created = 0
        destroyed: list[str] = []

        async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
            self.created += 1
            return SandboxHandle(id=f"sandbox-{self.created}", provider="fake", image="fake")

        async def run(
            self,
            handle: SandboxHandle,
            cmd: list[str],
            timeout_seconds: int = 120,
        ) -> ExecResult:
            _ = (cmd, timeout_seconds)
            return ExecResult(
                exit_code=exit_codes[int(handle.id.rsplit("-", 1)[1]) - 1],
                stdout="",
                stderr="",
                duration_ms=1,
            )

        async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
            _ = (handle, files)

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroyed.append(handle.id)

    provider = Provider()
    request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a" * 40,
        repository_path=tmp_path,
        public_repository=True,
        local_repository=False,
        verification_command=("pytest", "-q"),
    )

    # When
    evidence = await SandboxVerifier(provider).verify(request)

    # Then
    assert evidence.exit_codes == exit_codes
    assert evidence.verdict == expected
    assert provider.created == 3
    assert provider.destroyed == ["sandbox-1", "sandbox-2", "sandbox-3"]


async def test_sandbox_verifier_rejects_trusted_manifest_collision(tmp_path: Path) -> None:
    from enten.review.remote import (
        SOURCE_MANIFEST_PATH,
        SandboxVerifier,
        UnvalidatedRemoteCheckoutError,
    )

    # Given
    manifest = tmp_path / SOURCE_MANIFEST_PATH
    manifest.parent.mkdir()
    manifest.write_text("untrusted", encoding="utf-8")

    class Provider:
        async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
            raise AssertionError(repo_files)

        async def run(
            self,
            handle: SandboxHandle,
            cmd: list[str],
            timeout_seconds: int = 120,
        ) -> ExecResult:
            raise AssertionError((handle, cmd, timeout_seconds))

        async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
            raise AssertionError((handle, files))

        async def destroy(self, handle: SandboxHandle) -> None:
            raise AssertionError(handle)

    request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a" * 40,
        repository_path=tmp_path,
        public_repository=True,
        local_repository=False,
        verification_command=("true",),
    )

    # When / Then
    with pytest.raises(UnvalidatedRemoteCheckoutError, match="reserved source manifest"):
        await SandboxVerifier(Provider()).verify(request)


async def test_sandbox_verifier_records_infrastructure_failure_after_cleanup(
    tmp_path: Path,
) -> None:
    from enten.review.remote import SandboxVerifier

    # Given
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    class Provider:
        created = 0
        destroyed: list[str]

        def __init__(self) -> None:
            self.destroyed = []

        async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
            _ = repo_files
            self.created += 1
            return SandboxHandle(id=f"sandbox-{self.created}", provider="fake", image="fake")

        async def run(
            self,
            handle: SandboxHandle,
            cmd: list[str],
            timeout_seconds: int = 120,
        ) -> ExecResult:
            _ = (cmd, timeout_seconds)
            if handle.id == "sandbox-2":
                raise RuntimeError("sandbox daemon disconnected")
            return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

        async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
            _ = (handle, files)

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroyed.append(handle.id)

    provider = Provider()
    request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a" * 40,
        repository_path=tmp_path,
        public_repository=True,
        local_repository=False,
        verification_command=("true",),
    )

    # When
    evidence = await SandboxVerifier(provider).verify(request)

    # Then
    assert evidence.verdict == "error"
    assert evidence.exit_codes == (0, 0)
    assert evidence.infrastructure_detail == "sandbox verification infrastructure failed"
    assert provider.destroyed == ["sandbox-1", "sandbox-2", "sandbox-3"]


async def test_sandbox_verifier_cleans_active_lease_when_cancelled(tmp_path: Path) -> None:
    from enten.review.remote import SandboxVerifier

    # Given
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    started = anyio.Event()

    class Provider:
        destroyed: list[str]

        def __init__(self) -> None:
            self.destroyed = []

        async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
            _ = repo_files
            return SandboxHandle(id="sandbox-1", provider="fake", image="fake")

        async def run(
            self,
            handle: SandboxHandle,
            cmd: list[str],
            timeout_seconds: int = 120,
        ) -> ExecResult:
            _ = (handle, cmd, timeout_seconds)
            started.set()
            await anyio.sleep_forever()
            raise AssertionError("cancelled run cannot return")

        async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
            _ = (handle, files)

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroyed.append(handle.id)

    provider = Provider()
    request = ReviewRequest(
        repository_id="101",
        repository_full_name="public/repo",
        pr_number=42,
        head_sha="a" * 40,
        repository_path=tmp_path,
        public_repository=True,
        local_repository=False,
        verification_command=("true",),
    )

    async def verify_until_cancelled() -> None:
        _ = await SandboxVerifier(provider).verify(request)

    # When
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(verify_until_cancelled)
        await started.wait()
        tasks.cancel_scope.cancel()

    # Then
    assert provider.destroyed == ["sandbox-1"]


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
    import enten.review.public_checkout as checkout_module
    from enten.review.remote import public_pull_request_checkout

    head_sha = "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000"
    calls: list[tuple[str, ...]] = []

    async def fake_git(*arguments: str) -> str:
        calls.append(arguments)
        return head_sha if "rev-parse" in arguments else ""

    async def fake_limited_git(*arguments: str) -> str:
        calls.append(arguments)
        return ""

    monkeypatch.setattr(checkout_module, "_git", fake_git)
    monkeypatch.setattr(checkout_module, "_limited_git", fake_limited_git)

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


async def test_public_checkout_fetch_runs_under_posix_file_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enten.review.public_checkout as checkout_module

    # Given
    commands: list[tuple[str, ...]] = []

    async def fake_run_command(command: tuple[str, ...]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(checkout_module.shutil, "which", lambda command: "/usr/bin/prlimit")
    monkeypatch.setattr(checkout_module, "_run_command", fake_run_command)

    # When
    await checkout_module._limited_git("fetch", "origin")

    # Then
    assert commands == [
        (
            "/usr/bin/prlimit",
            "--fsize=134217728",
            "--",
            "git",
            "fetch",
            "origin",
        )
    ]


async def test_public_checkout_fails_closed_without_posix_file_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enten.review.public_checkout as checkout_module

    # Given
    monkeypatch.setattr(checkout_module.shutil, "which", lambda command: None)

    # When / Then
    with pytest.raises(checkout_module.UnvalidatedRemoteCheckoutError, match="prlimit is required"):
        await checkout_module._limited_git("fetch", "origin")


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        (
            "".join(f"100644 blob {'a' * 40} 1\tfile-{index}.txt\0" for index in range(2_001)),
            "tracked file count",
        ),
        (f"100644 blob {'a' * 40} {2 * 1024 * 1024 + 1}\tlarge.bin\0", "blob size"),
        (
            "".join(
                f"100644 blob {'a' * 40} {2 * 1024 * 1024}\tpart-{index}.bin\0"
                for index in range(33)
            ),
            "expanded bytes",
        ),
    ],
)
async def test_public_checkout_rejects_tree_quota_before_materialization_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    listing: str,
    message: str,
) -> None:
    import enten.review.public_checkout as checkout_module
    from enten.review.remote import UnvalidatedRemoteCheckoutError, public_pull_request_checkout

    # Given
    checkout_paths: list[Path] = []

    async def fake_git(*arguments: str) -> str:
        if "init" in arguments:
            checkout_paths.append(Path(arguments[-1]))
        if "ls-tree" in arguments:
            return listing
        return ""

    async def fake_limited_git(*arguments: str) -> str:
        _ = arguments
        return ""

    monkeypatch.setattr(checkout_module, "_git", fake_git)
    monkeypatch.setattr(checkout_module, "_limited_git", fake_limited_git)

    # When / Then
    with pytest.raises(UnvalidatedRemoteCheckoutError, match=message):
        async with public_pull_request_checkout(
            repository_full_name="public/repo",
            pr_number=42,
            head_sha="a" * 40,
        ):
            raise AssertionError("oversized tree must not be materialized")

    assert len(checkout_paths) == 1
    assert not checkout_paths[0].exists()


async def test_public_checkout_rejects_oversized_fetch_pack_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enten.review.public_checkout as checkout_module
    from enten.review.remote import UnvalidatedRemoteCheckoutError, public_pull_request_checkout

    # Given
    checkout_paths: list[Path] = []

    async def fake_git(*arguments: str) -> str:
        if "init" in arguments:
            checkout_paths.append(Path(arguments[-1]))
        return ""

    async def fake_limited_git(*arguments: str) -> str:
        _ = arguments
        return ""

    monkeypatch.setattr(checkout_module, "_git", fake_git)
    monkeypatch.setattr(checkout_module, "_limited_git", fake_limited_git)
    monkeypatch.setattr(
        checkout_module,
        "_pack_size_bytes",
        lambda checkout: checkout_module.FETCH_PACK_MAX_BYTES + 1,
    )

    # When / Then
    with pytest.raises(UnvalidatedRemoteCheckoutError, match="fetch pack bytes"):
        async with public_pull_request_checkout(
            repository_full_name="public/repo",
            pr_number=42,
            head_sha="a" * 40,
        ):
            raise AssertionError("oversized pack must not be materialized")

    assert len(checkout_paths) == 1
    assert not checkout_paths[0].exists()
