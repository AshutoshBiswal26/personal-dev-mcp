# DocumentMCP

A working [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server and
command-line client for discovering, reading, and safely editing a small document
collection.

## What is MCP?

MCP is an open protocol that standardizes how AI applications connect to external
capabilities and context. An **MCP host** (such as Kiro) creates an MCP client,
connects to an **MCP server**, negotiates capabilities, discovers what the server
provides, and invokes those capabilities through typed protocol messages.

An MCP server can expose three main primitives:

- **Tools**: operations an AI can invoke, such as editing a document.
- **Resources**: data an AI can read, such as document contents.
- **Prompts**: reusable prompt templates. This server does not need custom prompts.

DocumentMCP communicates over **stdio**: the host launches `mcp_server.py` as a
child process and exchanges MCP messages over standard input/output. This is a
local one-client/one-server-process transport, not an HTTP API.

Learn more from the
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.12.0).

## Is this a valid MCP application?

Yes. It now implements the complete server/client lifecycle and was tested with
Python 3.11.9, MCP Python SDK 1.12.0, and Pydantic 2.11.7. It provides:

- MCP initialization and capability negotiation
- Tool discovery and invocation
- Structured tool inputs and outputs
- A discoverable document catalog
- Protocol-native resources and a resource template
- Exact-match edit validation and input-size limits
- SHA-256 revisions to reject stale concurrent edits
- In-process locking around document state
- Optional atomic JSON persistence
- Client timeouts, error handling, subprocess cleanup, and edit confirmation
- Stderr-only server diagnostics so stdout remains safe for MCP messages

It is a robust local/sample MCP, not a multi-user document management system. See
[Limitations](#limitations) before using it with important data.

## Project structure

Only the required project files are used:

| File | Purpose |
| --- | --- |
| `mcp_server.py` | FastMCP server, sample document store, tools, resources, validation, revisions, and optional persistence. |
| `mcp_client.py` | CLI client that launches the server, initializes an MCP session, discovers tools, and makes calls. |
| `README.md` | Setup, operation, architecture, and safety documentation. |

## MCP capabilities

### Tools

| Tool | Purpose |
| --- | --- |
| `list_documents` | Returns ids, media types, character counts, revisions, and persistence status. |
| `read_doc_contents` | Returns a document's text and its current SHA-256 revision. |
| `edit_document` | Applies a validated exact-text replacement using the revision returned by a read. |

`edit_document` requires `doc_id`, `old_str`, `new_str`, and
`expected_revision`. It replaces one unique match by default. If the text occurs
multiple times, the call fails unless `replace_all=true` is explicitly supplied.
Empty search strings, no-op replacements, missing text, stale revisions, unknown
ids, and oversized values are rejected.

### Resources

| Resource | Purpose |
| --- | --- |
| `document://catalog` | JSON catalog of available documents. |
| `document://documents/{doc_id}` | Read-only resource template for document text. |

The list/read tools provide structured metadata and revisions, while resources
provide a protocol-native read interface for MCP hosts.

## Requirements and installation

- Python 3.10 or newer (tested with Python 3.11.9)
- MCP Python SDK 1.12.0 (includes the required Pydantic dependency)

From PowerShell in this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "mcp[cli]==1.12.0"
```

## Use the bundled client

Global options such as `--store`, `--server`, and `--timeout` must appear before
the command.

Discover the server's tool schemas:

```powershell
python mcp_client.py tools
```

List documents:

```powershell
python mcp_client.py list
```

Read a document:

```powershell
python mcp_client.py read deposition.md
```

Safely edit a document. The client reads the latest revision automatically and
asks for confirmation:

```powershell
python mcp_client.py edit deposition.md --old "Angela Smith" --new "Angela Jones"
```

For automation, skip the prompt with `--yes`. Only use `--replace-all` when every
exact occurrence should change:

```powershell
python mcp_client.py edit plan.md --old "the" --new "a" --replace-all --yes
```

To enforce a revision obtained earlier, pass it explicitly:

```powershell
python mcp_client.py edit deposition.md `
  --old "Angela Smith" `
  --new "Angela Jones" `
  --revision "<64-character SHA-256 revision>" `
  --yes
```

The client returns exit code `0` on success, `1` for validation, protocol, or
runtime errors, and `130` when interrupted.

## Persistence

By default, state is in memory and resets when the server process exits. This is
appropriate for testing and for a host session that keeps one server process
alive.

Use `--store` to persist changes atomically to a JSON file:

```powershell
python mcp_client.py --store .\documents.json list
python mcp_client.py --store .\documents.json edit deposition.md `
  --old "Angela Smith" --new "Angela Jones" --yes
python mcp_client.py --store .\documents.json read deposition.md
```

If the file does not exist, the server starts with the six sample records and
creates it after the first successful edit. Writes use a temporary file followed
by `os.replace`, so a failed write is not reported as a successful edit.

The server can also receive an absolute store path through the
`DOCUMENT_MCP_STORE` environment variable.

## Configure Kiro

Create or edit `.kiro/settings/mcp.json` and use absolute paths. Point `command`
at the virtual environment's Python executable so the SDK is always available:

```json
{
  "mcpServers": {
    "document-mcp": {
      "command": "C:\\Users\\USER\\Desktop\\personal-dev-mcp\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\USER\\Desktop\\personal-dev-mcp\\mcp_server.py"
      ],
      "env": {
        "DOCUMENT_MCP_STORE": "C:\\Users\\USER\\Desktop\\personal-dev-mcp\\documents.json"
      },
      "disabled": false
    }
  }
}
```

Remove the `env` block if persistence is not wanted. After saving the MCP config,
reconnect the server from Kiro's MCP Server view if necessary. Kiro can then
discover the three tools and two resource definitions directly.

## Safety model

1. Read a document and retain its returned revision.
2. Send the exact old and new text plus that revision to `edit_document`.
3. The server locks state, verifies the revision and match count, validates the
   resulting size, persists when configured, and only then reports success.
4. If another edit changed the document first, the revision check fails. Read the
   latest content, review it, and retry deliberately.

Limits are 255 characters for document ids, 100,000 characters for each
replacement value, and 1,000,000 characters per resulting document.

## Limitations

- The seeded `.pdf` and `.docx` ids contain sample plain text. This project does
  not parse real PDF or Word files.
- Documents cannot currently be created, deleted, uploaded, or imported through
  MCP; the available ids come from the seed data or configured JSON store.
- Stdio is intended for a locally launched server. There is no network transport,
  authentication, authorization, or per-user access control.
- Persistence uses one JSON file and an in-process lock. It is not designed for
  multiple server processes writing the same file simultaneously.
- Tool access granted by an MCP host allows document mutation. Keep confirmation
  enabled for interactive edits and grant tool permissions deliberately.

For real multi-user or sensitive documents, add authenticated Streamable HTTP,
a transactional database, authorization and audit logging, backups, and proper
PDF/DOCX extraction rather than extending the sample dictionary directly.
