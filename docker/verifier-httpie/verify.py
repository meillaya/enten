from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Final, NoReturn

EXPECTED_REPOSITORY: Final = "httpie/cli"
EXPECTED_REVISION: Final = "2105caa49bae87c5809c274e407619a0de2639d1"
EXPECTED_TREE: Final = "5be8d10a92efc928460e86f1ce6dcabbea1a728a"
EXPECTED_SOURCE_DIGEST: Final = "43ea1a4bd1c6b5725df2775f4665f092305f16119c806b84de51e5ee5261268e"
EXPECTED_SELECTOR: Final = "tests/test_parser_schema.py"
WORKSPACE: Final = Path("/workspace")
MANIFEST_PATH: Final = Path(".enten-trusted/source-manifest.json")
DIGEST_DOMAIN: Final = b"enten-normalized-source-v1\0"
MAX_SOURCE_FILES: Final = 2_000
MAX_FILE_BYTES: Final = 2 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 64 * 1024 * 1024
CONTRACT_EXIT: Final = 64


def _reject(message: str) -> NoReturn:
    print(f"verifier contract mismatch: {message}", file=sys.stderr)
    raise SystemExit(CONTRACT_EXIT)


def _checked_environment(name: str, expected: str) -> None:
    actual = os.environ.get(name, expected)
    if actual != expected:
        _reject(f"{name} must be {expected!r}, received {actual!r}")


def _read_bounded(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    if size > limit:
        _reject(f"source file exceeds {limit} bytes: {path.relative_to(WORKSPACE)}")
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        _reject(f"source file exceeds {limit} bytes: {path.relative_to(WORKSPACE)}")
    return content


def _manifest() -> dict[str, str | int]:
    path = WORKSPACE / MANIFEST_PATH
    if not path.is_file() or path.is_symlink():
        _reject(f"trusted manifest {MANIFEST_PATH} is missing")
    raw = _read_bounded(path, 16 * 1024)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("trusted manifest is not valid UTF-8 JSON")
    if not isinstance(parsed, dict):
        _reject("trusted manifest must be a JSON object")
    expected_keys = {
        "head_sha",
        "repository_full_name",
        "repository_id",
        "source_digest",
        "version",
    }
    if set(parsed) != expected_keys:
        _reject("trusted manifest fields are not exact")
    canonical = json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if raw != canonical:
        _reject("trusted manifest is not canonical JSON")
    return parsed


def _source_digest() -> str:
    entries: list[tuple[str, bytes]] = []
    total_bytes = 0
    for path in WORKSPACE.rglob("*"):
        relative = path.relative_to(WORKSPACE)
        if relative == MANIFEST_PATH or path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            _reject(f"unsupported source entry: {relative}")
        if len(entries) >= MAX_SOURCE_FILES:
            _reject(f"source contains more than {MAX_SOURCE_FILES} files")
        content = _read_bounded(path, MAX_FILE_BYTES)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            _reject(f"source file is not normalized UTF-8 text: {relative}")
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            _reject(f"source exceeds {MAX_TOTAL_BYTES} aggregate bytes")
        entries.append((relative.as_posix(), content))
    digest = hashlib.sha256(DIGEST_DOMAIN)
    for relative, content in sorted(entries):
        path_bytes = relative.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _validate_source() -> None:
    manifest = _manifest()
    if manifest["version"] != 1:
        _reject("trusted manifest version must be 1")
    repository_id = manifest["repository_id"]
    if not isinstance(repository_id, str) or not repository_id.isdecimal():
        _reject("repository_id must be a nonempty decimal string")
    if manifest["repository_full_name"] != EXPECTED_REPOSITORY:
        _reject(f"repository_full_name must be {EXPECTED_REPOSITORY!r}")
    if manifest["head_sha"] != EXPECTED_REVISION:
        _reject(f"head_sha must be {EXPECTED_REVISION!r}")
    if manifest["source_digest"] != EXPECTED_SOURCE_DIGEST:
        _reject(f"source_digest must be {EXPECTED_SOURCE_DIGEST!r}")
    actual_digest = _source_digest()
    if actual_digest != EXPECTED_SOURCE_DIGEST:
        _reject(f"transported source digest must be {EXPECTED_SOURCE_DIGEST!r}")
    if not (WORKSPACE / EXPECTED_SELECTOR).is_file():
        _reject(f"selector {EXPECTED_SELECTOR!r} is missing")


def main() -> NoReturn:
    if len(sys.argv) != 1:
        _reject("command arguments are not accepted")
    _checked_environment("ENTEN_REPOSITORY", EXPECTED_REPOSITORY)
    _checked_environment("ENTEN_BASE_SHA", EXPECTED_REVISION)
    _checked_environment("ENTEN_SOURCE_TREE", EXPECTED_TREE)
    _checked_environment("ENTEN_TEST_SELECTOR", EXPECTED_SELECTOR)
    _validate_source()
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            "-q",
            EXPECTED_SELECTOR,
        ],
    )


if __name__ == "__main__":
    main()
