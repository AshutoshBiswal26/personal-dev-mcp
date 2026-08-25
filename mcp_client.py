"""Command-line MCP client for the bundled document server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.types import CallToolResult

DEFAULT_TIMEOUT_SECONDS = 30.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Connect to DocumentMCP over stdio and invoke its tools.",
    )
    parser.add_argument(
        "--server",
        type=Path,
        default=Path(__file__).with_name("mcp_server.py"),
        help="Path to the MCP server script (default: mcp_server.py beside this client)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"MCP request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--store",
        type=Path,
        help=(
            "Optional JSON store path for persistent edits; overrides "
            "the DOCUMENT_MCP_STORE environment variable"
        ),
    )

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools", help="Show tools advertised by the server")
    commands.add_parser("list", help="List available documents")

    read_parser = commands.add_parser("read", help="Read a document")
    read_parser.add_argument("doc_id", help="Document id returned by the list command")

    edit_parser = commands.add_parser("edit", help="Safely edit a document")
    edit_parser.add_argument("doc_id", help="Document id returned by the list command")
    edit_parser.add_argument("--old", required=True, help="Exact text to replace")
    edit_parser.add_argument("--new", required=True, help="Replacement text; may be empty")
    edit_parser.add_argument(
        "--revision",
        help="Expected revision; if omitted, the client reads the current revision first",
    )
    edit_parser.add_argument(
        "--replace-all",
        action="store_true",
        help="Replace every exact match instead of requiring a unique match",
    )
    edit_parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply the edit without an interactive confirmation",
    )
    return parser


def _text_from_result(result: CallToolResult) -> str:
    return "\n".join(
        text
        for item in result.content
        if isinstance((text := getattr(item, "text", None)), str)
    )


def _result_data(result: CallToolResult) -> dict[str, Any]:
    text = _text_from_result(result)
    if result.isError:
        raise RuntimeError(text or "The MCP tool returned an unspecified error")
    if result.structuredContent is not None:
        return result.structuredContent
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"result": text}
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}
    return {}


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


async def _call(session: ClientSession, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await session.call_tool(name, arguments=arguments)
    return _result_data(result)


async def _run_command(session: ClientSession, args: argparse.Namespace) -> None:
    tools_result = await session.list_tools()
    tools_by_name = {tool.name: tool for tool in tools_result.tools}

    if args.command == "tools":
        _print_json(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in tools_result.tools
            ]
        )
        return

    required_tool = {
        "list": "list_documents",
        "read": "read_doc_contents",
        "edit": "edit_document",
    }[args.command]
    if required_tool not in tools_by_name:
        raise RuntimeError(f"Server does not advertise required tool '{required_tool}'")

    if args.command == "list":
        _print_json(await _call(session, "list_documents"))
        return

    if args.command == "read":
        _print_json(await _call(session, "read_doc_contents", {"doc_id": args.doc_id}))
        return

    revision = args.revision
    if revision is None:
        if "read_doc_contents" not in tools_by_name:
            raise RuntimeError("Server cannot provide the revision required for a safe edit")
        current = await _call(session, "read_doc_contents", {"doc_id": args.doc_id})
        revision = current.get("revision")
        if not isinstance(revision, str):
            raise RuntimeError("Server read result did not contain a revision")

    if not args.yes:
        action = "all exact matches" if args.replace_all else "one unique exact match"
        print(f"Document: {args.doc_id}")
        print(f"Action: replace {action}")
        print(f"Old text: {args.old!r}")
        print(f"New text: {args.new!r}")
        if input("Apply this edit? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Edit cancelled.")
            return

    _print_json(
        await _call(
            session,
            "edit_document",
            {
                "doc_id": args.doc_id,
                "old_str": args.old,
                "new_str": args.new,
                "expected_revision": revision,
                "replace_all": args.replace_all,
            },
        )
    )


async def _run(args: argparse.Namespace) -> None:
    server_path = args.server.expanduser().resolve()
    if not server_path.is_file():
        raise FileNotFoundError(f"MCP server script was not found: {server_path}")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than zero")

    server_environment = get_default_environment()
    configured_store = args.store
    if configured_store is not None:
        server_environment["DOCUMENT_MCP_STORE"] = str(configured_store.expanduser().resolve())
    elif store_from_environment := os.environ.get("DOCUMENT_MCP_STORE"):
        server_environment["DOCUMENT_MCP_STORE"] = store_from_environment

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        cwd=server_path.parent,
        env=server_environment,
    )
    timeout = timedelta(seconds=args.timeout)

    async with stdio_client(server, errlog=sys.stderr) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timeout,
        ) as session:
            await session.initialize()
            await _run_command(session, args)


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
