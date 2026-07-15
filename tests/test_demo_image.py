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
SELECTOR = "tests/test_offline.py"
HARDENING = (
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "256",
)


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
    return Path(source).resolve()


def _run_container(
    image: str,
    source: Path,
    *,
    name: str,
    contract_override: tuple[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        *HARDENING,
        "--mount",
        f"type=bind,src={source},dst=/workspace",
    ]
    if contract_override is not None:
        key, value = contract_override
        command.extend(["--env", f"{key}={value}"])
    command.append(image)
    return subprocess.run(command, capture_output=True, text=True, check=False)


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
    for expected in (REPOSITORY, REVISION, TREE, SELECTOR):
        assert expected in dockerfile
        assert expected in verifier


def test_image_metadata_and_runtime_are_minimal(demo_image: str) -> None:
    labels = {
        "org.opencontainers.image.source": f"https://github.com/{REPOSITORY}",
        "org.opencontainers.image.revision": REVISION,
        "io.enten.source.tree": TREE,
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
def test_wrong_sha_or_selector_is_rejected(
    demo_image: str,
    key: str,
    value: str,
) -> None:
    completed = _run_container(
        demo_image,
        _source_checkout(),
        name=f"enten-httpie-reject-{uuid.uuid4().hex[:12]}",
        contract_override=(key, value),
    )
    assert completed.returncode == 64
    assert "contract mismatch" in completed.stderr


@pytest.mark.slow
def test_three_fresh_hardened_networkless_runs_pass_under_budget(
    demo_image: str,
) -> None:
    source = _source_checkout()
    started = time.monotonic()
    transcripts: list[str] = []
    names: list[str] = []

    for _ in range(3):
        name = f"enten-httpie-pass-{uuid.uuid4().hex[:12]}"
        names.append(name)
        completed = _run_container(demo_image, source, name=name)
        transcripts.append(completed.stdout + completed.stderr)
        assert completed.returncode == 0, transcripts[-1]
        assert re.search(r"\b9 passed\b", transcripts[-1]) is not None

    assert time.monotonic() - started < 120
    for name in names:
        removed = subprocess.run(
            ["docker", "container", "inspect", name],
            capture_output=True,
            check=False,
        )
        assert removed.returncode != 0
