from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.core import TyperOption
from typer.main import get_command
from typer.testing import CliRunner

from enten.cli import app


def test_review_command_runs_local_repository_and_persists_artifact(tmp_path: Path) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    store_path = tmp_path / "reviews.json"

    result = CliRunner().invoke(
        app,
        [
            "review",
            str(repository),
            "--sha",
            "abc123",
            "--verify",
            "python -m compileall -q .",
            "--store-path",
            str(store_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["stage"] == "completed"
    assert output["verdict"] == "pass"
    assert output["replayed"] is False
    assert output["findings"] == 0
    assert store_path.exists()


def test_root_command_reviews_the_current_directory_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    arguments = ["--verify", "python -m compileall -q .", "--json"]

    first = runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_output = json.loads(first.output)
    second_output = json.loads(second.output)
    assert second_output["review_id"] == first_output["review_id"]
    assert first_output["replayed"] is False
    assert second_output["replayed"] is True
    stored = json.loads((repository / ".enten/reviews.json").read_text(encoding="utf-8"))
    assert len(stored) == 1


def test_root_command_has_a_human_readable_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(repository)

    result = CliRunner().invoke(app, ["--verify", "python -m compileall -q ."])

    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "review " in result.output
    assert ".enten/reviews.json" in result.output


def test_review_replays_when_custom_store_is_inside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    store_path = repository / "state/reviews.json"
    runner = CliRunner()
    arguments = [
        "review",
        str(repository),
        "--verify",
        "python -c 'pass'",
        "--store-path",
        str(store_path),
        "--json",
    ]

    first = runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_output = json.loads(first.output)
    second_output = json.loads(second.output)
    assert second_output["review_id"] == first_output["review_id"]
    assert second_output["replayed"] is True
    assert len(json.loads(store_path.read_text(encoding="utf-8"))) == 1


def test_cost_command_reports_persisted_local_review(tmp_path: Path) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n", encoding="utf-8")
    store_path = tmp_path / "reviews.json"
    runner = CliRunner()
    reviewed = runner.invoke(
        app,
        [
            "review",
            str(repository),
            "--pr",
            "8",
            "--sha",
            "c0ffee",
            "--verify",
            "python -m compileall -q .",
            "--store-path",
            str(store_path),
            "--json",
        ],
    )
    assert reviewed.exit_code == 0, reviewed.output

    result = runner.invoke(
        app,
        ["cost", "--pr", "8", "--store-path", str(store_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "cost_usd": "0",
        "reviews": 1,
        "tokens_in": 0,
        "tokens_out": 0,
    }


def test_review_command_arbitrates_actual_nonzero_process_exit(tmp_path: Path) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n", encoding="utf-8")
    store_path = tmp_path / "reviews.json"

    result = CliRunner().invoke(
        app,
        [
            "review",
            str(repository),
            "--sha",
            "deadbeef",
            "--verify",
            "python -c 'import sys; sys.exit(7)'",
            "--store-path",
            str(store_path),
            "--json",
        ],
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["verdict"] == "fail"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored[0]["verification"]["exit_codes"] == [7]


def test_top_level_help_and_advanced_command_help_are_available() -> None:
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    review = runner.invoke(app, ["review", "--help"])
    cost = runner.invoke(app, ["cost", "--help"])
    command = get_command(app)

    assert root.exit_code == 0, root.output
    assert review.exit_code == 0, review.output
    assert cost.exit_code == 0, cost.output
    assert "--verify" in root.output
    assert "--json" in root.output
    review_options = {
        option
        for parameter in command.commands["review"].params
        if isinstance(parameter, TyperOption)
        for option in parameter.opts
    }
    cost_options = {
        option
        for parameter in command.commands["cost"].params
        if isinstance(parameter, TyperOption)
        for option in parameter.opts
    }
    assert {"--verify", "--store-path", "--probes", "--json"} <= review_options
    assert {"--pr"} <= cost_options
