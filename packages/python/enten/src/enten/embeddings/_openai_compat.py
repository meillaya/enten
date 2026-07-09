from __future__ import annotations

import httpx

from enten.embeddings._errors import EmbeddingUnavailableError


class OpenAICompatEmbeddings:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        dimension: int = 1024,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {
            "input": texts,
            "model": self.model,
            "encoding_format": "float",
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for _ in range(self.max_retries):
            try:
                response = await self._client.post(
                    f"{self.base_url}/embeddings",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()["data"]
                embeddings = [item["embedding"] for item in data]
                return [_normalize(emb, self.dimension) for emb in embeddings]
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc

        raise EmbeddingUnavailableError(
            f"embedding call failed after {self.max_retries} attempts"
        ) from last_error


def _normalize(vector: list[float], dimension: int) -> list[float]:
    if len(vector) == dimension:
        return vector
    if len(vector) > dimension:
        return vector[:dimension]
    return vector + [0.0] * (dimension - len(vector))
