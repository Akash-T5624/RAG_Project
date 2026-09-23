"""Legacy snapshot used only to reproduce the before-error transcript."""

from fastmcp import FastMCP

mcp = FastMCP("Policy document search (legacy)")


@mcp.tool
def search_policy_documents(query: str) -> dict:
    """Search policy."""
    raise ValueError("Error 3")


if __name__ == "__main__":
    mcp.run(transport="stdio")
