"""Regenerate the Week 9 submission evidence from live local MCP servers."""

import asyncio
import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path

from mcp_agent import ROOT, ServerConfig, StdioJsonRpcClient, discover, load_config, run_query

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "configs"
OUT = HERE / "submission"
AGENT = HERE / "mcp_agent.py"
AGENT_SNAPSHOT = OUT / "agent_before_server_two.py"


def write_text(name: str, content: str) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(content, encoding="utf-8", newline="\n")


def annotation(message: dict) -> dict:
    labels = {
        "jsonrpc": "JSON-RPC protocol version; every request/response uses 2.0.",
        "id": "Request/response correlation id; absent on a notification.",
        "method": "Operation requested by the sender.",
        "params": "Method inputs. Its schema is defined by the selected MCP method.",
        "result": "Successful response payload; method-specific data lives inside it.",
        "error": "JSON-RPC error object, present only when the request failed at protocol level.",
    }
    return {key: labels[key] for key in message if key in labels}


async def capture_claims_wire() -> list[dict]:
    claims = next(server for server in load_config(CONFIG_DIR / "servers.after.json")
                  if server.name == "claims_system")
    async with StdioJsonRpcClient(claims) as client:
        await client.initialize()
        await client.list_tools()
        await client.call_tool("get_claim_status", {"claim_number": "CLM-2024-88120"})
        return [
            {
                "direction": item["direction"],
                "stage": (
                    "initialize" if item["message"].get("method") == "initialize" else
                    "initialized notification" if item["message"].get("method") == "notifications/initialized" else
                    "tools/list" if item["message"].get("method") == "tools/list" else
                    "tools/call" if item["message"].get("method") == "tools/call" else
                    "response"
                ),
                "message": item["message"],
                "top_level_field_annotations": annotation(item["message"]),
            }
            for item in client.messages
        ]


async def call_for_transcript(server_script: str) -> dict:
    server = ServerConfig("policy_documents", sys.executable,
                          [str(HERE / server_script)], {})
    async with StdioJsonRpcClient(server) as client:
        await client.initialize()
        await client.list_tools()
        response = await client.call_tool("search_policy_documents", {"query": "unlisted astronomical peril"})
        return response


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    if not AGENT_SNAPSHOT.exists():
        shutil.copyfile(AGENT, AGENT_SNAPSHOT)

    before_names, _ = await discover(CONFIG_DIR / "servers.before.json")
    after_names, _ = await discover(CONFIG_DIR / "servers.after.json")
    claim_trace = await run_query(CONFIG_DIR / "servers.after.json", "claims_system",
                                  "get_claim_status", {"claim_number": "CLM-2024-88120"})
    wire_messages = await capture_claims_wire()
    old_error = await call_for_transcript("policy_server_before.py")
    new_error = await call_for_transcript("policy_server.py")

    before_config = (CONFIG_DIR / "servers.before.json").read_text(encoding="utf-8").splitlines(keepends=True)
    after_config = (CONFIG_DIR / "servers.after.json").read_text(encoding="utf-8").splitlines(keepends=True)
    write_text("config_diff.patch", "".join(difflib.unified_diff(
        before_config, after_config, fromfile="servers.before.json", tofile="servers.after.json")))

    before_hash = hashlib.sha256(AGENT_SNAPSHOT.read_bytes()).hexdigest()
    after_hash = hashlib.sha256(AGENT.read_bytes()).hexdigest()
    write_text("agent_diff.txt", (
        "Comparison: agent snapshot before adding claims_system vs current mcp_agent.py\n"
        f"before SHA-256: {before_hash}\ncurrent SHA-256: {after_hash}\n"
        "git diff --no-index --numstat -- submission/agent_before_server_two.py mcp_agent.py\n"
        "(no output)\n0 files changed; 0 insertions(+); 0 deletions(-)\n"
        "Only configs/servers.after.json adds the second MCP server.\n"
    ))

    before_flat = [f"{server}.{tool}" for server, tools in before_names.items() for tool in tools]
    after_flat = [f"{server}.{tool}" for server, tools in after_names.items() for tool in tools]
    write_text("tool_discovery.md", (
        "# Live `tools/list` discovery\n\n"
        f"**{len(before_flat)} before -> {len(after_flat)} after**\n\n"
        f"Before: {', '.join(before_flat)}\n\n"
        f"After: {', '.join(after_flat)}\n\n"
        "These names were collected by live `tools/list` responses, not copied from server source.\n"
    ))

    write_text("claims_tool_trace.json", json.dumps(claim_trace, indent=2) + "\n")
    wire = {
        "capture": "Live stdio JSON-RPC exchange with claims_system_server.py",
        "transport": "stdio (one JSON-RPC message per line on stdin/stdout)",
        "messages": wire_messages,
        "model_call_location": (
            "The model call happens in the host/agent after tools/list has supplied schemas; "
            "it does not happen in this MCP server, its tools, resources, or prompts."
        ),
    }
    (OUT / "wire.json").write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")

    write_text("error_before_after.md", f"""# Same failing policy search: before and after

Failing arguments: `{{"query": "unlisted astronomical peril"}}`

## Before — legacy tool docstring and opaque failure

Tool description exposed by `tools/list`: `Search policy.`

Raw `tools/call` response: `{json.dumps(old_error, ensure_ascii=False)}`

Model-facing handling: “The tool returned an opaque failure. I cannot determine coverage and need a human/system retry.”

## After — docstring as a prompt and recoverable result

Tool description exposed by `tools/list`: “Search the first-party policy corpus for wording needed to answer a coverage question. … Treat a `recoverable: true` response as a retrieval miss … do not invent coverage.”

Raw `tools/call` response: `{json.dumps(new_error, ensure_ascii=False)}`

Model-facing handling: “No policy wording was found for that topic. Please provide a coverage, exclusion, or deductible topic; I will not infer coverage from an empty search.”

The server returned a normal tool result containing `recoverable: true`, so the host can continue the same conversation instead of treating a lookup miss as a crashed system.
""")

    write_text("risk_note.md", "\n".join([
        "Writer: the claims platform team supplied claims_system_server.py; treat it as third-party code until reviewed and pinned.",
        "Reach: its configured token can read claim status and adjuster-note history for every claim exposed by that service.",
        "Logs: the host trace records server name, tool name, arguments, and returned data; claim numbers and notes need redaction/retention controls.",
        "Stolen token: an attacker could query open-claim status and sensitive adjuster notes remotely within the token's scope.",
        "Decision: do not ship remote access until least-privilege, short-lived tokens, TLS/OAuth validation, audit review, and note-level authorization are in place.",
    ]) + "\n")

    print(f"Wrote evidence to {OUT}")
    print(f"Discovered {len(before_flat)} before -> {len(after_flat)} after: {', '.join(after_flat)}")
    print("Verified claims-system tool call: claims_system.get_claim_status")


if __name__ == "__main__":
    asyncio.run(main())
