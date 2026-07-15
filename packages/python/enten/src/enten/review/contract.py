from __future__ import annotations

import hashlib
import json

REVIEW_CONTRACT_DOMAIN = b"enten-review-contract-v1\0"


def review_contract_digest(command: str, image: str) -> str:
    payload = json.dumps(
        {"command": command, "image": image},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(REVIEW_CONTRACT_DOMAIN + payload).hexdigest()


__all__ = ["REVIEW_CONTRACT_DOMAIN", "review_contract_digest"]
