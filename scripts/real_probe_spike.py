"""Run the probe spike on reversed historical bug-fix commits.

For each selected bug-fix commit, create a detached worktree at the fixed
commit, reverse only the source change, and keep the regression tests. The
resulting checkout is a real historical bug with its real test suite present.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from enten.config import Settings  # noqa: F401


def _load_environment() -> None:
    key_file = Path.home() / "enten-live.env"
    values: dict[str, str] = {}
    if key_file.exists():
        for line in key_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    api_key = values.get("OPENAI_API_KEY", "")
    if not api_key.startswith("sk-or-"):
        raise SystemExit("OPENAI_API_KEY with an OpenRouter key is required")
    os.environ.update(
        {
            "LLM_PROVIDER": "openai-compat",
            "LLM_OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            "LLM_OPENAI_API_KEY": api_key,
            "LLM_DEFAULT_MODEL": "deepseek/deepseek-v4.1-flash-1017",
            "DATABASE_URL": "postgresql+asyncpg://enten:enten@localhost:5433/enten",
            "PER_PR_COST_CAP_USD": "0.50",
        }
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _candidate_commits(repo: Path, limit: int) -> list[str]:
    shas = _git(repo, "log", "--no-merges", "--grep=fix", "-i", "--format=%H").splitlines()
    selected: list[str] = []
    for sha in shas:
        files = _git(repo, "show", "--name-only", "--format=", sha).splitlines()
        source = [
            path
            for path in files
            if path.startswith("more_itertools/") and path.endswith(".py")
        ]
        tests = [
            path
            for path in files
            if path.startswith("tests/") and path.endswith(".py")
        ]
        if source and tests:
            selected.append(sha)
        if len(selected) == limit:
            break
    return selected


async def _run(repo: Path, limit: int) -> None:
    _load_environment()
    settings = Settings()
    from enten.llm import LLMClient, create_llm_provider
    from enten.review.diff import DiffIndex
    from enten.review.hypothesis import LLMHypothesisGenerator
    from enten.review.python_probes import LocalProbeRunner
    from enten.review.run import Proposal

    client = LLMClient(create_llm_provider(settings), settings)
    commits = _candidate_commits(repo, limit)
    print(f"selected={len(commits)}")
    total_hypotheses = 0
    total_confirmed = 0
    cases_with_confirmed = 0
    for index, sha in enumerate(commits, start=1):
        parent = _git(repo, "rev-parse", f"{sha}^").strip()
        reverse = _git(repo, "diff", sha, parent, "--", "more_itertools")
        if not reverse.strip():
            continue
        with tempfile.TemporaryDirectory(prefix="enten-real-probe-") as raw:
            worktree = Path(raw) / "repo"
            _git(repo, "worktree", "add", "--detach", str(worktree), sha)
            try:
                patch = Path(raw) / "reverse.patch"
                patch.write_text(reverse, encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(worktree), "apply", str(patch)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                proposal = Proposal(
                    repository_path=worktree,
                    files=tuple(
                        path.relative_to(worktree).as_posix()
                        for path in sorted((worktree / "more_itertools").glob("*.py"))
                    ),
                    verification_command=("python", "-m", "pytest", "-q"),
                    diff=reverse,
                    changed=DiffIndex.from_patch(reverse),
                    correlation_run_id=f"spike-{sha[:12]}",
                )
                hypotheses = await LLMHypothesisGenerator(client).generate(proposal)
                probes = await LocalProbeRunner().run(hypotheses, proposal)
                confirmed = sum(1 for result in probes.results if result.status == "confirmed")
                total_hypotheses += len(hypotheses.hypotheses)
                total_confirmed += confirmed
                cases_with_confirmed += int(confirmed > 0)
                print(
                    f"{index:02d} {sha[:10]} hypotheses={len(hypotheses.hypotheses)} "
                    f"confirmed={confirmed} cost=${hypotheses.cost_usd}"
                )
            finally:
                shutil.rmtree(worktree, ignore_errors=True)
                _git(repo, "worktree", "prune")
    print(
        f"TOTAL cases={len(commits)} hypotheses={total_hypotheses} "
        f"confirmed={total_confirmed} cases_with_confirmed={cases_with_confirmed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(_run(args.repo.resolve(), args.limit))


if __name__ == "__main__":
    main()
