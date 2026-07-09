"""Self-detection helpers for the enten GitHub App.

GitHub App actors appear as ``<slug>[bot]`` in API payloads, while the
configured slug is the plain ``github_app_slug`` value. These helpers
normalize both forms so the agent can ignore its own pull requests and
avoid feedback loops.
"""

from __future__ import annotations

from enten.config import Settings
from enten.github.models import PullRequest


def is_bot_pr(pr: PullRequest, settings: Settings) -> bool:
    """Return True when ``pr`` was authored by the enten GitHub App.

    The check uses both the configured slug and the bot login form
    (``<slug>[bot]``) so it works whether callers compare against the
    App slug or the actual GitHub actor login.
    """
    slug = settings.github_app_slug
    bot_login = f"{slug}[bot]"

    if pr.user.login in (slug, bot_login):
        return True

    head_repo = pr.head.repo
    return head_repo is not None and head_repo.owner.login in (slug, bot_login)


__all__ = ["is_bot_pr"]
