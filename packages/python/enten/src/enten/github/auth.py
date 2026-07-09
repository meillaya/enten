"""GitHub App JWT helpers.

A GitHub App authenticates to the REST API with a short-lived JWT signed
with the App's private key. That JWT is then exchanged for a per-
installation access token which is what most endpoints actually require.
"""

from __future__ import annotations

from pathlib import Path
from time import time

import jwt

JWT_ALGORITHM = "RS256"
JWT_TTL_SECONDS = 600  # 10 minutes; GitHub allows up to 10 min.


def create_jwt(app_id: str, private_key_path: str | Path) -> str:
    """Create an RS256 JWT for authenticating as a GitHub App.

    The token is issued ``now`` and expires ``JWT_TTL_SECONDS`` later.
    GitHub tolerates up to 10 minutes of clock skew, so we keep a safe
    margin and never issue a token with more than 10 min of remaining
    life.

    Args:
        app_id: The numeric GitHub App ID.
        private_key_path: Filesystem path to the downloaded PEM private key.

    Returns:
        A compact JWT string ready for the ``Authorization: Bearer <jwt>``
        header.

    Raises:
        FileNotFoundError: If the private key file does not exist.
    """
    key_path = Path(private_key_path)
    private_key = key_path.read_text(encoding="utf-8")

    now = int(time())
    payload = {
        "iat": now - 60,  # 1 min leeway for clock skew.
        "exp": now + JWT_TTL_SECONDS,
        "iss": app_id,
    }

    return jwt.encode(payload, private_key, algorithm=JWT_ALGORITHM)
