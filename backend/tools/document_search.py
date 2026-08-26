from __future__ import annotations

from backend.retrieval import AuthorityAwareRetriever, RetrievedChunk
from backend.schemas import CustomerIdentity


class DocumentSearchTool:
    """Tenant-filtered learned-semantic PDF retrieval tool."""

    name = "document_search"

    def __init__(self, retriever: AuthorityAwareRetriever):
        self.retriever = retriever

    def search(
        self,
        identity: CustomerIdentity,
        query: str,
        required_source_files: list[str],
        top_k: int = 5,
    ) -> tuple[list[RetrievedChunk], str]:
        return self.retriever.search(
            query,
            account_id=identity.account_id,
            top_k=top_k,
            required_source_files=required_source_files,
        )
