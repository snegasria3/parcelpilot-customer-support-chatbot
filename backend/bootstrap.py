from __future__ import annotations

from typing import Any

from backend.config import Settings
from backend.data_import import import_assessment_data
from backend.database import Database
from backend.documents import load_document_chunks
from backend.embeddings import FastEmbedBGE
from backend.retrieval import ChromaSemanticStore


def bootstrap(settings: Settings, *, rebuild_index: bool = False) -> dict[str, Any]:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_path)
    imported = import_assessment_data(db, settings.workbook_path, settings.users_seed_path)
    chunks = load_document_chunks(settings.source_docs_path, settings.registry_path)
    embedder = FastEmbedBGE(settings.embedding_model, settings.embedding_cache_dir)
    store = ChromaSemanticStore(settings.vector_path, embedder)
    index = store.build(chunks, rebuild=rebuild_index)
    return {
        "database": str(settings.database_path),
        **imported,
        "document_chunks": len(chunks),
        "retrieval": "bge+chroma",
        "index": index,
        "warnings": settings.warnings(),
    }
