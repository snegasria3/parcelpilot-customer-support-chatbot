from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.documents import DocumentChunk, source_fingerprint
from backend.embeddings import Embedder, EmbeddingError
from backend.schemas import Citation


class RetrievalError(RuntimeError):
    """Raised when vector search cannot complete safely."""


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: DocumentChunk
    semantic_score: float
    final_score: float


class ChromaSemanticStore:
    def __init__(self, path: Path, embedder: Embedder, collection_name: str = "parcelpilot_customer_sources"):
        try:
            import chromadb
        except ImportError as exc:
            raise RetrievalError("Chroma is not installed") from exc
        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def build(self, chunks: list[DocumentChunk], *, rebuild: bool = False) -> dict[str, Any]:
        fingerprint = source_fingerprint(chunks, self.embedder.model_name)
        existing_fingerprint = self.collection.metadata.get("fingerprint") if self.collection.metadata else None
        if rebuild or existing_fingerprint != fingerprint or self.collection.count() != len(chunks):
            if self.collection.count():
                existing_ids = self.collection.get(include=[]).get("ids", [])
                if existing_ids:
                    self.collection.delete(ids=existing_ids)
            try:
                embeddings = self.embedder.embed_documents([chunk.text for chunk in chunks])
            except EmbeddingError as exc:
                raise RetrievalError(str(exc)) from exc
            for start in range(0, len(chunks), 64):
                batch = chunks[start : start + 64]
                self.collection.upsert(
                    ids=[chunk.chunk_id for chunk in batch],
                    documents=[chunk.text for chunk in batch],
                    embeddings=embeddings[start : start + len(batch)],
                    metadatas=[chunk.metadata() for chunk in batch],
                )
            self.collection.modify(metadata={"fingerprint": fingerprint, "model": self.embedder.model_name})
        return {"chunks": self.collection.count(), "fingerprint": fingerprint, "model": self.embedder.model_name}

    def ready(self, expected_chunks: int) -> bool:
        try:
            return self.collection.count() == expected_chunks and expected_chunks > 0
        except Exception:
            return False

    def query(self, query: str, allowed_ids: set[str], top_k: int) -> list[tuple[str, float]]:
        if not allowed_ids:
            return []
        try:
            vector = self.embedder.embed_query(query)
            result = self.collection.query(
                query_embeddings=[vector],
                n_results=min(max(top_k * 4, 12), len(allowed_ids)),
                where={"chunk_id": {"$in": sorted(allowed_ids)}},
                include=["distances"],
            )
        except Exception as exc:
            raise RetrievalError(f"Learned semantic vector search failed: {exc}") from exc
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [(chunk_id, max(0.0, 1.0 - float(distance))) for chunk_id, distance in zip(ids, distances, strict=True)]


class AuthorityAwareRetriever:
    def __init__(self, chunks: list[DocumentChunk], store: ChromaSemanticStore | None):
        self.chunks = chunks
        self.store = store
        self.by_id = {chunk.chunk_id: chunk for chunk in chunks}

    def allowed_chunks(self, account_id: str, *, include_deprecated_context: bool = False) -> list[DocumentChunk]:
        allowed: list[DocumentChunk] = []
        for chunk in self.chunks:
            if chunk.status == "DEPRECATED" and not include_deprecated_context:
                continue
            if chunk.document_type == "agreement" and chunk.account_id != account_id:
                continue
            allowed.append(chunk)
        return allowed

    def search(
        self,
        query: str,
        *,
        account_id: str,
        top_k: int = 5,
        required_source_files: list[str] | None = None,
    ) -> tuple[list[RetrievedChunk], str]:
        allowed = self.allowed_chunks(account_id)
        allowed_ids = {chunk.chunk_id for chunk in allowed}
        results: list[RetrievedChunk] = []
        mode = "required-source"
        if self.store and self.store.ready(len(self.chunks)):
            try:
                for chunk_id, semantic_score in self.store.query(query, allowed_ids, top_k):
                    chunk = self.by_id[chunk_id]
                    authority_bonus = (chunk.authority_rank / 100.0) * 0.08
                    results.append(RetrievedChunk(chunk, semantic_score, semantic_score + authority_bonus))
                results.sort(key=lambda item: (item.final_score, item.chunk.authority_rank), reverse=True)
                results = results[:top_k]
                mode = "bge+chroma"
            except RetrievalError:
                # Exact rules can still cite their required authorized source; there is no hashing fallback.
                mode = "required-source"

        required = required_source_files or []
        existing = {item.chunk.file_name for item in results}
        query_terms = {term.lower() for term in query.replace("-", " ").split() if len(term) > 2}
        for file_name in required:
            if file_name in existing:
                continue
            candidates = [chunk for chunk in allowed if chunk.file_name == file_name]
            if not candidates:
                continue
            selected = max(
                candidates,
                key=lambda chunk: len(query_terms & {term.lower() for term in chunk.text.replace("-", " ").split()}),
            )
            results.append(RetrievedChunk(selected, 0.0, selected.authority_rank / 100.0 * 0.08))
        results.sort(key=lambda item: (item.final_score, item.chunk.authority_rank), reverse=True)
        return results[: max(top_k, len(required))], mode

    @staticmethod
    def citations(results: list[RetrievedChunk]) -> list[Citation]:
        citations: list[Citation] = []
        for index, item in enumerate(results, start=1):
            excerpt = item.chunk.text[:500].strip()
            citations.append(
                Citation(
                    citation_id=f"D{index}",
                    file_name=item.chunk.file_name,
                    title=item.chunk.title,
                    section=item.chunk.section,
                    page=item.chunk.page,
                    authority=item.chunk.authority,
                    excerpt=excerpt,
                    semantic_score=round(item.semantic_score, 4),
                )
            )
        return citations
