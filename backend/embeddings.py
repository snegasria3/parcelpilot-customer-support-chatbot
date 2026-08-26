from __future__ import annotations

from pathlib import Path
from typing import Protocol


class EmbeddingError(RuntimeError):
    """Raised when the learned embedding model is unavailable."""


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedBGE:
    """Local learned semantic embeddings. This is intentionally not a hashing embedder."""

    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str, cache_dir: Path):
        self.model_name = model_name
        try:
            from fastembed import TextEmbedding

            cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        except Exception as exc:  # model setup errors vary by ONNX/runtime platform
            raise EmbeddingError(f"Could not load learned embedding model {model_name}: {exc}") from exc

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return [vector.tolist() for vector in self._model.embed(texts, batch_size=32)]
        except Exception as exc:
            raise EmbeddingError(f"Document embedding failed: {exc}") from exc

    def embed_query(self, text: str) -> list[float]:
        try:
            vector = next(self._model.query_embed(self.QUERY_PREFIX + text))
            return vector.tolist()
        except Exception as exc:
            raise EmbeddingError(f"Query embedding failed: {exc}") from exc
