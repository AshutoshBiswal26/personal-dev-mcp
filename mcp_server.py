"""A safe, discoverable MCP server for a small document collection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

MAX_DOCUMENT_CHARACTERS = 1_000_000
MAX_REPLACEMENT_CHARACTERS = 100_000
STORE_ENVIRONMENT_VARIABLE = "DOCUMENT_MCP_STORE"

DEFAULT_DOCUMENTS = {
    "deposition.md": "This deposition covers the testimony of Angela Smith, P.E.",
    "report.pdf": "The report details the state of a 20m condenser tower.",
    "financials.docx": "These financials outline the project's budget and expenditures",
    "outlook.pdf": "This document presents the projected future performance of the system",
    "plan.md": "The plan outlines the steps for the project's implementation.",
    "spec.txt": "These specifications define the technical requirements for the equipment",
}


class DocumentSummary(BaseModel):
    """Metadata returned when documents are listed."""

    doc_id: str
    media_type: str
    character_count: int
    revision: str


class DocumentList(BaseModel):
    """The discoverable document catalog."""

    documents: list[DocumentSummary]
    persistence_enabled: bool


class DocumentContents(BaseModel):
    """A document together with the revision needed for a safe edit."""

    doc_id: str
    media_type: str
    content: str
    character_count: int
    revision: str


class EditResult(BaseModel):
    """A structured acknowledgement of an applied edit."""

    doc_id: str
    replacements: int
    character_count: int
    previous_revision: str
    revision: str
    persisted: bool


mcp = FastMCP(
    "DocumentMCP",
    instructions=(
        "Discover documents with list_documents, read one with read_doc_contents, "
        "and pass the returned revision to edit_document. Edits use exact, "
        "case-sensitive matching."
    ),
    log_level="ERROR",
)

_store_path_value = os.environ.get(STORE_ENVIRONMENT_VARIABLE)
_store_path = Path(_store_path_value).expanduser().resolve() if _store_path_value else None
_lock = RLock()


def _load_documents() -> dict[str, str]:
    if _store_path is None or not _store_path.exists():
        return DEFAULT_DOCUMENTS.copy()

    try:
        data = json.loads(_store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load document store '{_store_path}': {exc}") from exc

    if not isinstance(data, dict) or not all(
        isinstance(doc_id, str) and isinstance(content, str)
        for doc_id, content in data.items()
    ):
        raise RuntimeError("The document store must be a JSON object of string ids to string contents")
    if not data:
        raise RuntimeError("The document store must contain at least one document")
    if any(not doc_id or len(doc_id) > 255 for doc_id in data):
        raise RuntimeError("Document ids must contain between 1 and 255 characters")
    if any(len(content) > MAX_DOCUMENT_CHARACTERS for content in data.values()):
        raise RuntimeError(
            f"Documents may not exceed {MAX_DOCUMENT_CHARACTERS:,} characters"
        )
    return data


def _persist_documents(documents: dict[str, str]) -> None:
    """Atomically persist documents when an operator configured a store path."""
    if _store_path is None:
        return

    _store_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_store_path.parent,
            prefix=f".{_store_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(documents, temporary_file, ensure_ascii=False, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, _store_path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not persist document store '{_store_path}': {exc}") from exc


def _revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _media_type(doc_id: str) -> str:
    return "text/markdown" if doc_id.lower().endswith(".md") else "text/plain"


def _require_document(doc_id: str) -> str:
    if not doc_id or len(doc_id) > 255:
        raise ValueError("doc_id must contain between 1 and 255 characters")
    try:
        return _documents[doc_id]
    except KeyError as exc:
        available = ", ".join(sorted(_documents))
        raise ValueError(f"Document '{doc_id}' was not found. Available ids: {available}") from exc


def _summary(doc_id: str, content: str) -> DocumentSummary:
    return DocumentSummary(
        doc_id=doc_id,
        media_type=_media_type(doc_id),
        character_count=len(content),
        revision=_revision(content),
    )


def _list_documents() -> DocumentList:
    with _lock:
        summaries = [_summary(doc_id, _documents[doc_id]) for doc_id in sorted(_documents)]
    return DocumentList(documents=summaries, persistence_enabled=_store_path is not None)


def _read_document(doc_id: str) -> DocumentContents:
    with _lock:
        content = _require_document(doc_id)
        return DocumentContents(
            doc_id=doc_id,
            media_type=_media_type(doc_id),
            content=content,
            character_count=len(content),
            revision=_revision(content),
        )


_documents = _load_documents()


@mcp.tool(
    name="list_documents",
    description="List available document ids, sizes, media types, and current revisions.",
    structured_output=True,
)
def list_documents() -> DocumentList:
    """Return the document catalog so callers do not need to guess ids."""
    return _list_documents()


@mcp.tool(
    name="read_doc_contents",
    description="Read a document and return its text plus the revision required for editing.",
    structured_output=True,
)
def read_document(
    doc_id: str = Field(min_length=1, max_length=255, description="Id of the document to read"),
) -> DocumentContents:
    """Read one document by id."""
    return _read_document(doc_id)


@mcp.tool(
    name="edit_document",
    description=(
        "Safely replace exact text in a document. Pass the revision from read_doc_contents. "
        "Multiple matches are rejected unless replace_all is explicitly true."
    ),
    structured_output=True,
)
def edit_document(
    doc_id: str = Field(
        min_length=1,
        max_length=255,
        description="Id of the document to edit",
    ),
    old_str: str = Field(
        min_length=1,
        max_length=MAX_REPLACEMENT_CHARACTERS,
        description="Exact, case-sensitive text to replace; it cannot be empty",
    ),
    new_str: str = Field(
        max_length=MAX_REPLACEMENT_CHARACTERS,
        description="Replacement text; use an empty string to delete the matched text",
    ),
    expected_revision: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 revision returned by read_doc_contents",
    ),
    replace_all: bool = Field(
        default=False,
        description="Replace every exact match; false requires exactly one match",
    ),
) -> EditResult:
    """Apply a concurrency-safe edit and return a structured acknowledgement."""
    if not old_str:
        raise ValueError("old_str cannot be empty")
    if len(old_str) > MAX_REPLACEMENT_CHARACTERS or len(new_str) > MAX_REPLACEMENT_CHARACTERS:
        raise ValueError(
            f"old_str and new_str may not exceed {MAX_REPLACEMENT_CHARACTERS:,} characters"
        )
    if old_str == new_str:
        raise ValueError("old_str and new_str must be different")

    global _documents
    with _lock:
        content = _require_document(doc_id)
        current_revision = _revision(content)
        if expected_revision != current_revision:
            raise ValueError(
                "Revision conflict: the document changed after it was read. "
                "Read it again before editing."
            )

        match_count = content.count(old_str)
        if match_count == 0:
            raise ValueError("old_str does not occur in the document")
        if match_count > 1 and not replace_all:
            raise ValueError(
                f"old_str occurs {match_count} times; provide a more specific value or set replace_all=true"
            )

        replacements = match_count if replace_all else 1
        updated_content = content.replace(old_str, new_str, -1 if replace_all else 1)
        if len(updated_content) > MAX_DOCUMENT_CHARACTERS:
            raise ValueError(
                f"The edit would exceed the {MAX_DOCUMENT_CHARACTERS:,}-character document limit"
            )

        updated_documents = _documents.copy()
        updated_documents[doc_id] = updated_content
        _persist_documents(updated_documents)
        _documents = updated_documents

        return EditResult(
            doc_id=doc_id,
            replacements=replacements,
            character_count=len(updated_content),
            previous_revision=current_revision,
            revision=_revision(updated_content),
            persisted=_store_path is not None,
        )


@mcp.resource(
    "document://catalog",
    name="document-catalog",
    description="JSON catalog of documents available from this server.",
    mime_type="application/json",
)
def document_catalog_resource() -> str:
    """Expose the catalog as a protocol-native MCP resource."""
    return _list_documents().model_dump_json(indent=2)


@mcp.resource(
    "document://documents/{doc_id}",
    name="document-by-id",
    description="Read-only text content for a document id from the catalog.",
    mime_type="text/plain",
)
def document_resource(doc_id: str) -> str:
    """Expose each document through a resource template."""
    return _read_document(doc_id).content


if __name__ == "__main__":
    # Stdout is reserved for MCP protocol messages. FastMCP diagnostics use stderr.
    mcp.run(transport="stdio")
