"""Determinism fixture loader (T06 stub).

T37 (eval harness) will implement the actual two-runs-must-match test
that feeds this fixture through the bot pipeline twice and asserts the
resulting verdicts are byte-identical. Here we only verify that the
fixture is loadable, well-formed, and exposes every field the eval
harness will rely on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

DETERMINISM_FIXTURE_PATH = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "determinism" / "pr.json"
)


@dataclass(frozen=True)
class PRFixture:
    """Minimal PR payload that the eval harness feeds to the bot twice."""

    pr_id: int
    pr_number: int
    head_sha: str
    base_sha: str
    files: dict[str, str]

    @property
    def title(self) -> str:
        return self._title

    @property
    def body(self) -> str:
        return self._body

    @property
    def author(self) -> str:
        return self._author

    # Stored on the instance so the dataclass stays hashable + frozen
    # while the harness can still read the original fields.
    _title: str
    _body: str
    _author: str


def load_determinism_pr(path: Path | None = None) -> PRFixture:
    """Load ``tests/fixtures/determinism/pr.json`` into a :class:`PRFixture`.

    Args:
        path: Override the default fixture path (used by tests to point at
              a temporary copy). Defaults to the repo-root fixture.

    Raises:
        FileNotFoundError: The fixture JSON does not exist.
        ValueError: The JSON is missing one of the required fields.
    """
    fixture_path = path or DETERMINISM_FIXTURE_PATH
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))

    required = {"id", "number", "title", "body", "head_sha", "base_sha", "user"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"Determinism fixture missing fields: {sorted(missing)}")

    files_raw = raw.get("files") or {}
    if not isinstance(files_raw, dict):
        raise ValueError("`files` must be a mapping of path -> diff string")

    return PRFixture(
        pr_id=int(raw["id"]),
        pr_number=int(raw["number"]),
        head_sha=str(raw["head_sha"]),
        base_sha=str(raw["base_sha"]),
        files={str(k): str(v) for k, v in files_raw.items()},
        _title=str(raw["title"]),
        _body=str(raw["body"]),
        _author=str(raw["user"]["login"]),
    )


# ─── Tests ─────────────────────────────────────────────────────────────


def test_load_determinism_pr_returns_a_well_formed_fixture() -> None:
    """The bundled pr.json must parse and expose every required field."""
    pr = load_determinism_pr()

    assert pr.pr_id == 1234567890
    assert pr.pr_number == 42
    assert pr.head_sha == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert pr.base_sha == "feedfacefeedfacefeedfacefeedfacefeedface"
    assert pr.title  # non-empty
    assert pr.body  # non-empty
    assert pr.author  # non-empty
    assert isinstance(pr.files, dict) and pr.files, "files must be non-empty"


def test_load_determinism_pr_rejects_missing_fields(tmp_path: Path) -> None:
    """A fixture missing required fields must raise :class:`ValueError`."""
    bad = tmp_path / "pr.json"
    bad.write_text(json.dumps({"id": 1, "number": 2}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing fields"):
        load_determinism_pr(path=bad)


def test_load_determinism_pr_rejects_nonexistent_path(tmp_path: Path) -> None:
    """A missing file must raise :class:`FileNotFoundError`."""
    with pytest.raises(FileNotFoundError):
        load_determinism_pr(path=tmp_path / "does-not-exist.json")


def test_determinism_fixture_stub_is_two_runnable() -> None:
    """Stub for T37: same fixture must be re-loadable and equal.

    T37 extends this with a real pipeline run-twice. For T06 we only
    assert that ``load_determinism_pr()`` is referentially
    deterministic — calling it twice yields equal fixtures.
    """
    first = load_determinism_pr()
    second = load_determinism_pr()

    assert first == second
    assert first.head_sha == second.head_sha
    assert first.base_sha == second.base_sha


def test_determinism_fixture_paths_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default fixture path must point inside the repo's tests/ dir."""
    # We don't move the real file; we just confirm the resolver lands
    # where we expect (a ``pr.json`` under tests/fixtures/determinism/).
    assert DETERMINISM_FIXTURE_PATH.name == "pr.json"
    assert DETERMINISM_FIXTURE_PATH.parent.name == "determinism"
    assert DETERMINISM_FIXTURE_PATH.exists()
