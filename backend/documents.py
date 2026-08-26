from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

HEADING_PATTERN = re.compile(r"^(?:\d+\.\s+.+|KI-\d+\s+-\s+.+|Severity and response targets)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: str
    text: str
    file_name: str
    title: str
    section: str
    page: int
    document_type: str
    status: str
    authority: str
    authority_rank: int
    account_id: str

    def metadata(self) -> dict[str, Any]:
        return asdict(self) | {"text": ""}


def _clean_text(text: str) -> str:
    text = text.replace("\u200b", "").replace("\ufeff", "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _split_page(text: str, title: str, *, max_chars: int = 950, overlap: int = 100) -> list[tuple[str, str]]:
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    heading = title
    body: list[str] = []
    for line in lines:
        if HEADING_PATTERN.match(line):
            if body:
                sections.append((heading, body))
            heading, body = line, []
        else:
            body.append(line)
    if body:
        sections.append((heading, body))

    chunks: list[tuple[str, str]] = []
    for section, section_lines in sections:
        combined = f"{title}\n{section}\n{' '.join(section_lines)}".strip()
        start = 0
        while start < len(combined):
            end = min(len(combined), start + max_chars)
            if end < len(combined):
                boundary = combined.rfind(". ", start + max_chars // 2, end)
                if boundary > start:
                    end = boundary + 1
            chunk_text = combined[start:end].strip()
            if chunk_text:
                chunks.append((section, chunk_text))
            if end >= len(combined):
                break
            start = max(start + 1, end - overlap)
    return chunks


def load_document_chunks(source_docs: Path, registry_path: Path) -> list[DocumentChunk]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    output: list[DocumentChunk] = []
    for document in registry:
        path = source_docs / document["file_name"]
        if not path.exists():
            raise FileNotFoundError(f"Missing source document: {path}")
        reader = PdfReader(path)
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                extracted = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                extracted = page.extract_text() or ""
            text = _clean_text(extracted)
            for index, (section, chunk_text) in enumerate(_split_page(text, document["title"]), start=1):
                identity = f"{document['file_name']}|{page_number}|{section}|{index}|{chunk_text}"
                output.append(
                    DocumentChunk(
                        chunk_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                        text=chunk_text,
                        file_name=document["file_name"],
                        title=document["title"],
                        section=section,
                        page=page_number,
                        document_type=document["document_type"],
                        status=document["status"],
                        authority=document["authority"],
                        authority_rank=int(document["authority_rank"]),
                        account_id=document.get("account_id", ""),
                    )
                )
    return output


def source_fingerprint(chunks: list[DocumentChunk], model_name: str) -> str:
    payload = model_name + "|" + "|".join(chunk.chunk_id for chunk in chunks)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
