"""Config-driven MCP host/client for the Week 9 practical.

The host owns model orchestration.  This sample deliberately stops at the MCP
boundary: `run_query` receives the tool selected by a model (or a test harness)
and verifies that it was discovered before calling it.  There is no tool list
in this module and no model call in either MCP server.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str]


def load_config(path: Path) -> list[ServerConfig]:
    """Load MCP server connection data; tool names are never configured here."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    servers = []
    for name, spec in raw["mcpServers"].items():
        if spec.get("transport") != "stdio":
            raise ValueError(f"{name}: this practical's local runner supports stdio only")

        def expand(value: str) -> str:
            return value.replace("{root}", str(ROOT)).replace("{python}", sys.executable)

        env = {}
        for key, value in spec.get("env", {}).items():
            if value.startswith("${") and value.endswith("}"):
                env[key] = os.getenv(value[2:-1], "demo-local-token")
            else:
                env[key] = value
        servers.append(ServerConfig(name, expand(spec["command"]),
                                    [expand(arg) for arg in spec["args"]], env))
    return servers


class StdioJsonRpcClient:
    """Small protocol client kept explicit so the wire evidence is inspectable."""

    def __init__(self, server: ServerConfig):
        self.server = server
        self.process = None
        self.messages: list[dict[str, Any]] = []
        self._id = 0

    async def __aenter__(self):
        environment = os.environ.copy()
        environment.update(self.server.env)
        self.process = await asyncio.create_subprocess_exec(
            self.server.command, *self.server.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=environment,
        )
        return self

    async def __aexit__(self, *_):
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def _read_message(self) -> dict[str, Any]:
        assert self.process and self.process.stdout
        while True:
            raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=15)
            if not raw:
                assert self.process.stderr
                detail = (await self.process.stderr.read()).decode("utf-8", "replace")
                raise RuntimeError(f"{self.server.name} closed stdout: {detail}")
            line = raw.decode("utf-8").strip()
            if line:
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    # A compliant server must use stdout only for JSON-RPC. Keep
                    # an unexpected line visible instead of silently accepting it.
                    raise RuntimeError(f"non-JSON stdout from {self.server.name}: {line}")

    async def send(self, message: dict[str, Any], *, response: bool = True) -> dict[str, Any] | None:
        assert self.process and self.process.stdin
        self.messages.append({"direction": "host -> server", "message": message})
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()
        if not response:
            return None
        result = await self._read_message()
        self.messages.append({"direction": "server -> host", "message": result})
        return result

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        reply = await self.send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        assert reply is not None
        if "error" in reply:
            raise RuntimeError(f"MCP JSON-RPC error: {reply['error']}")
        return reply

    async def initialize(self) -> dict[str, Any]:
        reply = await self.request("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "week9-claims-host", "version": "1.0.0"},
        })
        await self.send({"jsonrpc": "2.0", "method": "notifications/initialized"}, response=False)
        return reply

    async def list_tools(self) -> dict[str, Any]:
        return await self.request("tools/list", {})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self.request("tools/call", {"name": name, "arguments": arguments})


async def discover(config_path: Path) -> tuple[dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
    """Return names/schemas directly from tools/list for every configured server."""
    names, raw = {}, {}
    for server in load_config(config_path):
        async with StdioJsonRpcClient(server) as client:
            await client.initialize()
            response = await client.list_tools()
            tools = response["result"]["tools"]
            names[server.name] = [tool["name"] for tool in tools]
            raw[server.name] = tools
    return names, raw


async def run_query(config_path: Path, server_name: str, selected_tool: str,
                    arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a model-selected tool only after live tools/list discovery.

    In a production host, the LLM call happens immediately after discovery: its
    tool schema is built from `tools/list`, and its chosen name/arguments are
    passed here. The server itself never receives an LLM prompt or API key.
    """
    for server in load_config(config_path):
        if server.name != server_name:
            continue
        async with StdioJsonRpcClient(server) as client:
            await client.initialize()
            listed = await client.list_tools()
            discovered_names = [tool["name"] for tool in listed["result"]["tools"]]
            if selected_tool not in discovered_names:
                raise ValueError(f"{selected_tool!r} was not returned by tools/list for {server_name}")
            result = await client.call_tool(selected_tool, arguments)
            return {
                "query": "What is the status of claim CLM-2024-88120?",
                "server": server_name,
                "tool_selected_after_discovery": selected_tool,
                "arguments": arguments,
                "discovered_tools": discovered_names,
                "tool_result": result["result"],
                "json_rpc_trace": client.messages,
            }
    raise ValueError(f"server {server_name!r} is absent from {config_path}")
