from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from enten.review.remote import SandboxVerifier
from enten.review.run import ReviewRequest
from enten.sandbox.docker import DockerSandbox

ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = ROOT / "docker" / "verifier-httpie"
DOCKERFILE = IMAGE_DIR / "Dockerfile"
LOCKFILE = IMAGE_DIR / "requirements.lock"
BUILD_LOCKFILE = IMAGE_DIR / "build-requirements.lock"
VERIFIER = IMAGE_DIR / "verify.py"

BASE = "python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28"
REPOSITORY = "httpie/cli"
REVISION = "2105caa49bae87c5809c274e407619a0de2639d1"
TREE = "5be8d10a92efc928460e86f1ce6dcabbea1a728a"
SELECTOR = "tests/test_parser_schema.py"
SOURCE_DIGEST = "43ea1a4bd1c6b5725df2775f4665f092305f16119c806b84de51e5ee5261268e"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    completed = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _integration_enabled() -> bool:
    return os.environ.get("ENTEN_RUN_DEMO_IMAGE_INTEGRATION") == "1"


def _source_checkout() -> Path:
    source = os.environ.get("ENTEN_HTTPIE_SOURCE")
    if source is None:
        pytest.skip("ENTEN_HTTPIE_SOURCE does not name the trusted checkout")
    checkout = Path(source).resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode != 0 or revision.stdout.strip() != REVISION:
        pytest.fail(f"ENTEN_HTTPIE_SOURCE must be checkout {REVISION}")
    if tree.returncode != 0 or tree.stdout.strip() != TREE:
        pytest.fail(f"ENTEN_HTTPIE_SOURCE must have tree {TREE}")
    return checkout


def _request(source: Path) -> ReviewRequest:
    return ReviewRequest(
        repository_id="1",
        repository_full_name=REPOSITORY,
        pr_number=1,
        head_sha=REVISION,
        repository_path=source,
        verification_command=("/opt/venv/bin/python", "/opt/verifier/verify.py"),
    )


@pytest.fixture(scope="module")
def demo_image() -> Iterator[str]:
    if not _integration_enabled():
        pytest.skip("set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract")
    if not _docker_available():
        pytest.skip("Docker daemon is unavailable")

    image = f"enten-verifier-httpie-test:{uuid.uuid4().hex[:12]}"
    _ = subprocess.run(
        ["docker", "build", "--pull", "--no-cache", "--tag", image, "."],
        cwd=IMAGE_DIR,
        check=True,
    )
    yield image
    _ = subprocess.run(
        ["docker", "image", "rm", "--force", image],
        capture_output=True,
        check=False,
    )


def test_static_contract_is_exact_and_hash_locked() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    verifier = VERIFIER.read_text(encoding="utf-8")

    assert dockerfile.count(f"FROM {BASE}") == 2
    assert "USER 1000:1000" in dockerfile
    assert "WORKDIR /workspace" in dockerfile
    assert "pip install --require-hashes" in dockerfile
    copy_lines = re.findall(r"^COPY .+$", dockerfile, flags=re.MULTILINE)
    assert copy_lines == [
        "COPY build-requirements.lock /tmp/build-requirements.lock",
        "COPY requirements.lock /tmp/requirements.lock",
        "COPY --from=builder /opt/venv /opt/venv",
        "COPY verify.py /opt/verifier/verify.py",
    ]
    assert "--no-build-isolation" in dockerfile
    for path in (BUILD_LOCKFILE, LOCKFILE):
        lockfile = path.read_text(encoding="utf-8")
        assert "--require-hashes" in lockfile
        pattern = (
            r"(?ms)^[a-z0-9][a-z0-9._-]*(?:\[[a-z0-9,._-]+\])?==.*?"
            + r"(?=^[a-z0-9][a-z0-9._-]*(?:\[[a-z0-9,._-]+\])?==|\Z)"
        )
        requirements = [match.group(0) for match in re.finditer(pattern, lockfile)]
        assert requirements
        assert all("--hash=sha256:" in requirement for requirement in requirements)
    for expected in (REPOSITORY, REVISION, TREE, SELECTOR, SOURCE_DIGEST):
        assert expected in dockerfile
        assert expected in verifier


def test_image_metadata_and_runtime_are_minimal(demo_image: str) -> None:
    labels = {
        "org.opencontainers.image.source": f"https://github.com/{REPOSITORY}",
        "org.opencontainers.image.revision": REVISION,
        "io.enten.source.tree": TREE,
        "io.enten.source.digest": SOURCE_DIGEST,
        "io.enten.test.selector": SELECTOR,
    }
    user = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Config.User}}", demo_image],
        capture_output=True,
        text=True,
        check=True,
    )
    assert user.stdout.strip() == "1000:1000"
    for key, expected in labels.items():
        actual = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                f'{{{{index .Config.Labels "{key}"}}}}',
                demo_image,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert actual.stdout.strip() == expected

    tools = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            demo_image,
            "-c",
            "command -v apt apt-get dpkg pip gcc g++ cc make || true",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert tools.stdout == ""


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ENTEN_BASE_SHA", "0" * 40),
        ("ENTEN_TEST_SELECTOR", "tests/test_cli.py"),
    ],
)
@pytest.mark.asyncio
async def test_wrong_sha_or_selector_is_rejected(
    demo_image: str,
    key: str,
    value: str,
) -> None:
    provider = DockerSandbox(
        image=demo_image,
        environment={key: value},
    )
    evidence = await SandboxVerifier(provider).verify(_request(_source_checkout()))

    assert evidence.verdict == "fail"
    assert evidence.exit_codes == (64, 64, 64)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_three_fresh_hardened_networkless_runs_pass_under_budget(
    demo_image: str,
) -> None:
    started = time.monotonic()
    provider = DockerSandbox(image=demo_image)

    evidence = await SandboxVerifier(provider).verify(_request(_source_checkout()))

    assert time.monotonic() - started < 120
    assert evidence.verdict == "pass"
    assert evidence.exit_codes == (0, 0, 0)
    remaining = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=enten.sandbox=true"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert remaining.stdout.strip() == ""
