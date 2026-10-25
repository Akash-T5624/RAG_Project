"""MCP client for the agent: starts the server, discovers tools with tools/list at RUNTIME, calls them.

Reuses the Week 9 stdio JSON-RPC client. The tool names live nowhere in the agent: what the model is
offered is whatever tools/list returned. The agent loop is synchronous, so the async client runs on
its own event-loop thread.
"""

import asyncio
import json
import os
import sys
import threading

import capstone.core.paths as P
from week9_mcp.mcp_agent import ServerConfig, StdioJsonRpcClient

SERVER_SCRIPT = P.CAPSTONE / "mcp_layer" / "policy_server.py"
READ_TIMEOUT_S = 180  # first call may load the embedding model + cross-encoder


class _Client(StdioJsonRpcClient):
    """Week 9 client with a longer read timeout (its fixed 15 s is too short for a cold model load)."""

    async def _read_message(self):
        assert self.process and self.process.stdout
        while True:
            raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=READ_TIMEOUT_S)
            if not raw:
                assert self.process.stderr
                detail = (await self.process.stderr.read()).decode("utf-8", "replace")
                raise RuntimeError(f"{self.server.name} closed stdout: {detail[-800:]}")
            line = raw.decode("utf-8").strip()
            if line:
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    raise RuntimeError(f"non-JSON stdout from {self.server.name}: {line[:200]}")


class McpSession:
    def __init__(self, command=None, args=None, name="policy_wording", env=None):
        self.config = ServerConfig(name, command or sys.executable, args or [str(SERVER_SCRIPT)], {**os.environ, **(env or {})})
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.client = None
        self.tools = []

    def _run(self, coro, timeout=READ_TIMEOUT_S + 30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def start(self):
        self.thread.start()

        async def _boot():
            self.client = _Client(self.config)
            await self.client.__aenter__()
            await self.client.initialize()
            listed = await self.client.list_tools()
            return listed["result"]["tools"]

        self.tools = self._run(_boot())
        return self.tools

    def call(self, name, arguments):
        if name not in {t["name"] for t in self.tools}:
            raise ValueError(f"{name!r} was not returned by tools/list")
        reply = self._run(self.client.call_tool(name, arguments))["result"]
        if reply.get("isError"):
            return {"recoverable": True, "error": {"code": "MCP_TOOL_ERROR", "message": json.dumps(reply.get("content"))[:300]}}
        data = reply.get("structuredContent")
        if data is None:
            data = json.loads(reply["content"][0]["text"])
        return data["result"] if set(data) == {"result"} and isinstance(data["result"], dict) else data

    def wire(self):
        return self.client.messages if self.client else []

    def close(self):
        if self.client is not None:
            try:
                self._run(self.client.__aexit__(None, None, None), timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
