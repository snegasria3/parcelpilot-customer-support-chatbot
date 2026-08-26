from __future__ import annotations

from backend.documents import DocumentChunk
from backend.retrieval import AuthorityAwareRetriever, ChromaSemanticStore


class DenseConceptTestEmbedder:
    """Small deterministic test double for Chroma wiring; production uses FastEmbed BGE."""

    model_name = "dense-concept-test-double"

    @staticmethod
    def _vector(text: str) -> list[float]:
        lower = text.lower()
        concepts = [
            ("csv", "spreadsheet", "bulk", "rows", "upload"),
            ("cancel", "void", "stop", "fee", "booking"),
            ("pickup", "collection", "late", "credit", "compensation"),
            ("security", "credential", "key", "exposure"),
        ]
        vector = [float(sum(term in lower for term in group)) for group in concepts]
        magnitude = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / magnitude for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _chunk(chunk_id: str, text: str, section: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        text=text,
        file_name="current.pdf",
        title="Current guide",
        section=section,
        page=1,
        document_type="current_product_guide",
        status="CURRENT",
        authority="current_product_documentation",
        authority_rank=80,
        account_id="",
    )


def test_chroma_dense_vector_search_handles_paraphrase(tmp_path):
    chunks = [
        _chunk("csv", "Large CSV bulk uploads support 5,000 rows.", "Bulk Upload"),
        _chunk("cancel", "A booked shipment can be cancelled subject to a fee.", "Cancellation"),
        _chunk("credit", "A late pickup may qualify for service credit compensation.", "Pickup Credit"),
        _chunk("security", "Credential exposure is a security incident.", "Security"),
    ]
    store = ChromaSemanticStore(tmp_path / "chroma", DenseConceptTestEmbedder(), "vector_test")
    store.build(chunks, rebuild=True)
    retriever = AuthorityAwareRetriever(chunks, store)
    results, mode = retriever.search("My big spreadsheet import keeps dying", account_id="ACCT-001", top_k=1)
    assert mode == "bge+chroma"
    assert results[0].chunk.section == "Bulk Upload"
    assert results[0].semantic_score > 0


def test_chroma_vector_filter_never_returns_foreign_agreement(tmp_path):
    shared = _chunk("shared", "Cancellation policy for shipments", "Cancellation")
    foreign = DocumentChunk(
        chunk_id="foreign",
        text="Cancellation agreement with a special fee waiver",
        file_name="foreign.pdf",
        title="Foreign agreement",
        section="Cancellation",
        page=1,
        document_type="agreement",
        status="ACTIVE",
        authority="active_customer_agreement",
        authority_rank=100,
        account_id="ACCT-002",
    )
    store = ChromaSemanticStore(tmp_path / "chroma", DenseConceptTestEmbedder(), "tenant_vector_test")
    store.build([shared, foreign], rebuild=True)
    retriever = AuthorityAwareRetriever([shared, foreign], store)
    results, _ = retriever.search("Can I void this without a fee?", account_id="ACCT-001", top_k=3)
    assert results
    assert all(item.chunk.account_id != "ACCT-002" for item in results)
