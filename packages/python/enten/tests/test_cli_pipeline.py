from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from enten.cli import app


def test_review_command_runs_local_repository_and_persists_artifact(tmp_path: Path) -> None:
    # Given
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    store_path = tmp_path / "reviews.json"

    # When
    result = CliRunner().invoke(
        app,
        [
            "review",
            "--repo",
            str(repository),
            "--sha",
            "abc123",
            "--verify-command",
            "python -m compileall -q .",
            "--store-path",
            str(store_path),
        ],
    )

    # Then
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["stage"] == "completed"
    assert output["verdict"] == "pass"
    assert output["replayed"] is False
    assert store_path.exists()


def test_readme_default_review_replays_without_explicit_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    arguments = ["review", "--repo", "."]

    # When
    first = runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    # Then
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_output = json.loads(first.output)
    second_output = json.loads(second.output)
    assert second_output["review_id"] == first_output["review_id"]
    assert first_output["replayed"] is False
    assert second_output["replayed"] is True
    stored = json.loads((repository / ".enten/reviews.json").read_text(encoding="utf-8"))
    assert len(stored) == 1


def test_review_replays_when_custom_store_is_inside_repository(tmp_path: Path) -> None:
    # Given
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    store_path = repository / "state/reviews.json"
    runner = CliRunner()
    arguments = [
        "review",
        "--repo",
        str(repository),
        "--verify-command",
        "python -c 'pass'",
        "--store-path",
        str(store_path),
    ]

    # When
    first = runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    # Then
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_output = json.loads(first.output)
    second_output = json.loads(second.output)
    assert second_output["review_id"] == first_output["review_id"]
    assert second_output["replayed"] is True
    assert len(json.loads(store_path.read_text(encoding="utf-8"))) == 1


def test_cost_command_reports_persisted_local_review(tmp_path: Path) -> None:
    # Given
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n", encoding="utf-8")
    store_path = tmp_path / "reviews.json"
    runner = CliRunner()
    reviewed = runner.invoke(
        app,
        [
            "review",
            "--repo",
            str(repository),
            "--pr",
            "8",
            "--sha",
            "c0ffee",
            "--verify-command",
            "python -m compileall -q .",
            "--store-path",
            str(store_path),
        ],
    )
    assert reviewed.exit_code == 0, reviewed.output

    # When
    result = runner.invoke(
        app,
        ["cost", "--pr", "8", "--store-path", str(store_path), "--json"],
    )

    # Then
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output == {"cost_usd": "0", "reviews": 1, "tokens_in": 0, "tokens_out": 0}


def test_review_command_arbitrates_actual_nonzero_process_exit(tmp_path: Path) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n", encoding="utf-8")
    store_path = tmp_path / "reviews.json"

    result = CliRunner().invoke(
        app,
        [
            "review",
            "--repo",
            str(repository),
            "--sha",
            "deadbeef",
            "--verify-command",
            "python -c 'import sys; sys.exit(7)'",
            "--store-path",
            str(store_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["verdict"] == "fail"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored[0]["verification"]["exit_codes"] == [7]


def test_review_and_cost_help_are_available() -> None:
    # Given
    runner = CliRunner()

    # When
    review = runner.invoke(app, ["review", "--help"], terminal_width=160)
    cost = runner.invoke(app, ["cost", "--help"], terminal_width=160)

    # Then
    assert review.exit_code == 0, review.output
    assert "--repo" in review.output
    assert "--verify-command" in review.output
    assert cost.exit_code == 0, cost.output
    assert "--pr" in cost.output
