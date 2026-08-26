from __future__ import annotations

import argparse
import json

from backend.bootstrap import bootstrap
from backend.config import get_settings
from backend.container import AppContainer


def doctor() -> dict:
    settings = get_settings()
    container = AppContainer.create(settings)
    return {
        "workbook_exists": settings.workbook_path.exists(),
        "source_docs_exists": settings.source_docs_path.exists(),
        "database_ready": container.db.scalar("SELECT COUNT(*) FROM accounts") == 4,
        "dataset_snapshot": container.db.metadata("dataset_snapshot"),
        "llm_provider": settings.llm_provider,
        "llm_configured": bool(settings.groq_api_key),
        "configured_llm": settings.groq_chat_model,
        "embedding_provider": settings.embedding_provider,
        "configured_embedding": settings.embedding_model,
        "vector_backend": settings.vector_backend,
        "vector_index_ready": container.vector_ready,
        "document_chunks": len(container.chunks),
        "tools": container.tools.names,
        "warnings": settings.warnings(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ParcelPilot Customer Support CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser("bootstrap", help="Import Excel and build the learned semantic index")
    bootstrap_parser.add_argument("--rebuild-index", action="store_true")
    subparsers.add_parser("doctor", help="Inspect local configuration and readiness")
    args = parser.parse_args()
    result = bootstrap(get_settings(), rebuild_index=args.rebuild_index) if args.command == "bootstrap" else doctor()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
