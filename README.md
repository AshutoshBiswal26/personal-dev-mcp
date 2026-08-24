# personal-dev-mcp

A small Model Context Protocol (MCP) project containing a document server built on
`FastMCP` and a stdio-based client that connects to it.

The server exposes a tiny in-memory document store so an MCP host (Kiro, Claude
Desktop, or the bundled client) can read and edit documents through tool calls.

## Project layout

| File | Purpose |
| --- | --- |
| `mcp_server.py` | `FastMCP` server named `DocumentMCP` that exposes document tools over stdio. |
| `mcp_client.py` | Client scaffold that sets up an MCP `ClientSession` over a stdio transport. |

## The document store

Documents live in a module-level `docs` dictionary in `mcp_server.py`, keyed by
document id. State is in-memory only, so edits are lost when the process exits.

Seeded ids: `deposition.md`, `report.pdf`, `financials.docx`, `outlook.pdf`,
`plan.md`, `spec.txt`.

## Tools

### `read_doc_contents`

Reads the contents of a document and returns it as a string.

| Argument | Type | Description |
| --- | --- | --- |
| `doc_id` | `str` | Id of the document to read. |

Raises `ValueError` if the id is not in the store.

### `edit_document`

Replaces a substring in a document's contents with new text.

| Argument | Type | Description |
| --- | --- | --- |
| `doc_id` | `str` | Id of the document that will be edited. |
| `old_str` | `str` | The text to replace. Must match exactly, including whitespace. |
| `new_str` | `str` | The new text to insert in place of the old text. |

Raises `ValueError` if the id is not in the store. The match is an exact string
replacement, so whitespace and casing matter.

## Requirements

- Python 3.10 or newer
- The `mcp` Python SDK (provides `mcp.server.fastmcp` and `mcp.client.stdio`)
- `pydantic` (used for `Field` argument metadata and `AnyUrl`)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install "mcp[cli]" pydantic
```

## Running the server

The server communicates over stdio, so it is normally launched by an MCP host
rather than run by hand. To register it with Kiro, add it to
`.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "document-mcp": {
      "command": "python",
      "args": ["mcp_server.py"],
      "disabled": false
    }
  }
}
```

Point `args` at the absolute path to `mcp_server.py` if the host does not start in
this directory.

## Known gaps

This is a work in progress. Before the server and client will run end to end:

- `mcp_server.py` uses `Field` for tool argument metadata but does not import it.
  Add `from pydantic import Field`.
- `mcp_server.py` has no entrypoint. Add a `mcp.run(transport="stdio")` call under
  an `if __name__ == "__main__":` guard.
- `mcp_client.py` currently contains only imports. The `ClientSession` and
  `stdio_client` wiring, tool discovery, and tool invocation still need to be
  implemented.
