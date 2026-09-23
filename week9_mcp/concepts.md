# Week 9 MCP concept map

MCP (Model Context Protocol) is an open protocol for connecting an AI host to external capabilities through a common JSON-RPC interface. It separates an agent's reasoning from the systems that hold data or perform actions.

| Topic | In this practical |
|---|---|
| Host / client / server | `mcp_agent.py` is both the host (it owns orchestration) and MCP client. `policy_server.py` and `claims_system_server.py` are MCP servers. |
| Where the AI runs | A model call belongs in the host after it has received `tools/list` schemas. Neither FastMCP server calls an LLM or holds a model API key. |
| Tools / resources / prompts | Tools are model-invoked actions (`search_policy_documents`, `get_claim_status`). `policy://exclusions-summary` is a resource the app can attach as context. `coverage_review_guidance` is a reusable prompt template. |
| stdio / HTTP transports | The runnable local configuration uses stdio: one JSON-RPC message per stdin/stdout line. A remote production server normally uses Streamable HTTP over TLS. Transport changes the connection, not tool discovery semantics. |
| JSON-RPC handshake | Client `initialize` request -> server capability response -> `notifications/initialized` -> `tools/list` -> `tools/call`. The live, annotated wire is `submission/wire.json`. |
| Tool discovery | The host asks each configured server for `tools/list`, then allows a call only if the selected name appears in that response. The before/after count is evidence of runtime discovery. |
| FastMCP server | `FastMCP(...)`, `@mcp.tool`, `@mcp.resource`, and `@mcp.prompt` turn typed Python functions into MCP capabilities; `mcp.run(transport="stdio")` serves them. |
| Recoverable errors | The current policy tool returns a structured normal result with `recoverable: true`, stable code, and a correction. The old server raises opaque `Error 3`; the transcript compares them. |
| Remote MCP and auth | For Streamable HTTP, put the service behind HTTPS and validate OAuth/access tokens at the server/gateway. Use short-lived, audience-scoped, least-privilege tokens; never put a bearer token in a tool description, prompt, or trace. |

The claims `env` entry is intentionally a configuration placeholder. The local stdio mock does not validate it; production code must validate it and enforce claim/note authorization before tool execution.
