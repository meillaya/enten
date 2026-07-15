from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Final, NoReturn

EXPECTED_REPOSITORY: Final = "httpie/cli"
EXPECTED_REVISION: Final = "2105caa49bae87c5809c274e407619a0de2639d1"
EXPECTED_TREE: Final = "5be8d10a92efc928460e86f1ce6dcabbea1a728a"
EXPECTED_SELECTOR: Final = "tests/test_offline.py"
WORKSPACE: Final = Path("/workspace")
CONTRACT_EXIT: Final = 64


def _reject(message: str) -> NoReturn:
    print(f"verifier contract mismatch: {message}", file=sys.stderr)
    raise SystemExit(CONTRACT_EXIT)


def _checked_environment(name: str, expected: str) -> None:
    actual = os.environ.get(name, expected)
    if actual != expected:
        _reject(f"{name} must be {expected!r}, received {actual!r}")


def _git_directory(workspace: Path) -> Path:
    marker = workspace / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        prefix = "gitdir: "
        value = marker.read_text(encoding="utf-8").strip()
        if not value.startswith(prefix):
            _reject("/workspace/.git is not a Git directory pointer")
        return (workspace / value.removeprefix(prefix)).resolve()
    _reject("/workspace must be a Git checkout")


def _packed_reference(git_directory: Path, reference: str) -> str:
    packed_refs = git_directory / "packed-refs"
    if not packed_refs.is_file():
        _reject(f"Git reference {reference!r} is unavailable")
    for line in packed_refs.read_text(encoding="utf-8").splitlines():
        if line.startswith(("#", "^")):
            continue
        revision, separator, name = line.partition(" ")
        if separator and name == reference:
            return revision
    _reject(f"Git reference {reference!r} is unavailable")


def _head_revision(git_directory: Path) -> str:
    head = (git_directory / "HEAD").read_text(encoding="ascii").strip()
    prefix = "ref: "
    if not head.startswith(prefix):
        return head
    reference = head.removeprefix(prefix)
    loose = git_directory / reference
    if loose.is_file():
        return loose.read_text(encoding="ascii").strip()
    return _packed_reference(git_directory, reference)


def _git_object(kind: bytes, content: bytes) -> bytes:
    header = kind + b" " + str(len(content)).encode("ascii") + b"\0"
    return hashlib.sha1(header + content, usedforsecurity=False).digest()


def _blob(path: Path) -> bytes:
    if path.is_symlink():
        return _git_object(b"blob", os.readlink(path).encode())
    return _git_object(b"blob", path.read_bytes())


def _tree(path: Path, *, root: bool = False) -> bytes:
    entries: list[tuple[bytes, bytes]] = []
    for child in path.iterdir():
        if root and child.name == ".git":
            continue
        child_stat = child.lstat()
        name = child.name.encode()
        if stat.S_ISDIR(child_stat.st_mode):
            mode = b"40000"
            digest = _tree(child)
            sort_key = name + b"/"
        elif stat.S_ISLNK(child_stat.st_mode):
            mode = b"120000"
            digest = _blob(child)
            sort_key = name
        elif stat.S_ISREG(child_stat.st_mode):
            mode = b"100755" if child_stat.st_mode & stat.S_IXUSR else b"100644"
            digest = _blob(child)
            sort_key = name
        else:
            _reject(f"unsupported filesystem entry: {child.relative_to(WORKSPACE)}")
        entries.append((sort_key, mode + b" " + name + b"\0" + digest))
    content = b"".join(entry for _, entry in sorted(entries))
    return _git_object(b"tree", content)


def _validate_source() -> None:
    git_directory = _git_directory(WORKSPACE)
    revision = _head_revision(git_directory)
    if revision != EXPECTED_REVISION:
        _reject(f"checkout revision must be {EXPECTED_REVISION}, received {revision}")
    tree = _tree(WORKSPACE, root=True).hex()
    if tree != EXPECTED_TREE:
        _reject(f"checkout tree must be {EXPECTED_TREE}, received {tree}")
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
