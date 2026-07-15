from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from enten.review.run import ReviewKey, ReviewRun, ReviewStage


class ReviewStore(Protocol):
    """Persistence contract required by the stage machine and cost command."""

    async def save(self, run: ReviewRun) -> None: ...

    async def get_by_key(self, key: ReviewKey) -> ReviewRun | None: ...

    async def list_reviews(
        self,
        *,
        pr_number: int | None = None,
        repository_id: str | None = None,
    ) -> list[ReviewRun]: ...


class InMemoryReviewStore:
    def __init__(self) -> None:
        self._runs: dict[ReviewKey, ReviewRun] = {}
        self.stage_history: list[ReviewStage] = []

    async def save(self, run: ReviewRun) -> None:
        self._runs[run.request.key] = run
        self.stage_history.append(run.stage)

    async def get_by_key(self, key: ReviewKey) -> ReviewRun | None:
        return self._runs.get(key)

    async def list_reviews(
        self,
        *,
        pr_number: int | None = None,
        repository_id: str | None = None,
    ) -> list[ReviewRun]:
        return _filter_reviews(self._runs.values(), pr_number, repository_id)


class JsonReviewStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> list[ReviewRun]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [ReviewRun.model_validate(item) for item in raw]

    async def save(self, run: ReviewRun) -> None:
        by_key = {item.request.key: item for item in self._read()}
        by_key[run.request.key] = run
        serialized = [
            item.model_dump(mode="json")
            for item in sorted(by_key.values(), key=lambda value: value.request.key)
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(serialized, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    async def get_by_key(self, key: ReviewKey) -> ReviewRun | None:
        return next((run for run in self._read() if run.request.key == key), None)

    async def list_reviews(
        self,
        *,
        pr_number: int | None = None,
        repository_id: str | None = None,
    ) -> list[ReviewRun]:
        return _filter_reviews(self._read(), pr_number, repository_id)


def _filter_reviews(
    reviews: Iterable[ReviewRun],
    pr_number: int | None,
    repository_id: str | None,
) -> list[ReviewRun]:
    return [
        run
        for run in reviews
        if (pr_number is None or run.request.pr_number == pr_number)
        and (repository_id is None or run.request.repository_id == repository_id)
    ]


__all__ = ["InMemoryReviewStore", "JsonReviewStore", "ReviewStore"]
