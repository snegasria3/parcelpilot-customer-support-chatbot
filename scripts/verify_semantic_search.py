from __future__ import annotations

import sys

from backend.config import get_settings
from backend.container import AppContainer

CHECKS = [
    ("ACCT-002", "My big spreadsheet import dies before it finishes", "KI-208"),
    ("ACCT-001", "The driver collected it but tracking still says booked", "KI-211"),
    ("ACCT-001", "Can I void a shipment without being charged?", "cancellation"),
    ("ACCT-002", "Do we receive compensation after a missed collection?", "service credit"),
]


def main() -> None:
    container = AppContainer.create(get_settings())
    if not container.vector_ready:
        raise SystemExit("Semantic index is not ready. Run: python -m backend.cli bootstrap --rebuild-index")
    failures = []
    for account_id, query, expected in CHECKS:
        results, mode = container.retriever.search(query, account_id=account_id, top_k=5)
        text = " ".join(f"{item.chunk.section} {item.chunk.text}" for item in results).lower()
        passed = expected.lower() in text
        print(f"{'PASS' if passed else 'FAIL'} | {account_id} | {mode} | {query}")
        if not passed:
            failures.append(query)
    if failures:
        raise SystemExit(f"{len(failures)} semantic retrieval check(s) failed")
    print(f"{len(CHECKS)}/{len(CHECKS)} learned-semantic retrieval checks passed")


if __name__ == "__main__":
    sys.exit(main())
