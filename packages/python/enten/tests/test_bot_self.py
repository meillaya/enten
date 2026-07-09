"""Tests for ``enten.github.bot_self`` self-detection helpers."""

from __future__ import annotations

import pytest

from enten.config import Settings
from enten.github.bot_self import is_bot_pr
from enten.github.models import PRBranch, PullRequest, Repository, User


def _make_pr(user_login: str, owner_login: str | None = None) -> PullRequest:
    """Build a minimal PullRequest for self-detection tests."""
    owner = User(login=owner_login or "enten", id=1)
    repo = Repository(id=100, full_name="enten/enten", owner=owner)
    return PullRequest(
        id=1,
        number=1,
        title="test",
        state="open",
        user=User(login=user_login, id=2),
        head=PRBranch(ref="feature", sha="abc", repo=repo),
        base=PRBranch(ref="main", sha="def", repo=repo),
        html_url="https://github.com/enten/enten/pull/1",
    )


@pytest.fixture
def settings() -> Settings:
    """Default settings with the production bot slug."""
    return Settings(_env_file=None, github_app_slug="enten-bot")


def test_is_bot_pr_true_for_bot_login(settings: Settings) -> None:
    """The bot login with the ``[bot]`` suffix is recognized as self."""
    pr = _make_pr(user_login="enten-bot[bot]")
    assert is_bot_pr(pr, settings) is True


def test_is_bot_pr_false_for_human_user(settings: Settings) -> None:
    """A human contributor is not the bot."""
    pr = _make_pr(user_login="alice")
    assert is_bot_pr(pr, settings) is False


def test_is_bot_pr_false_for_other_bot(settings: Settings) -> None:
    """Only the enten bot is ignored; other GitHub Apps are not."""
    pr = _make_pr(user_login="github-actions[bot]")
    assert is_bot_pr(pr, settings) is False


def test_is_bot_pr_true_when_head_owner_is_bot(settings: Settings) -> None:
    """Self-detection also works when the head repo owner is the bot."""
    pr = _make_pr(user_login="alice", owner_login="enten-bot")
    assert is_bot_pr(pr, settings) is True
