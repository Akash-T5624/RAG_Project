"""A stand-alone MCP client for the other squad's reviewer. It imports NOTHING from this repo.

    python capstone/mcp_layer/fresh_eyes_client.py [--cmd python --args capstone/mcp_layer/policy_server.py]

It spawns the server over stdio, runs initialize + tools/list, calls the discovered tool for a
loss on 2024-08-11 and for one on 2025-01-01, and writes everything it received to
capstone/reports/mcp_wire_capture.json. Read that file to see exactly what left the server.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--cmd", default=sys.executable)
ap.add_argument("--args", nargs="*", default=[str(Path(__file__).with_name("policy_server.py"))])
ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "reports" / "mcp_wire_capture.json"))
a = ap.parse_args()

proc = subprocess.Popen([a.cmd, *a.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
wire, n = [], 0


def rpc(method, params=None, notify=False):
    global n
    msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
    if not notify:
        n += 1
        msg["id"] = n
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    wire.append({"direction": "client -> server", "message": msg})
    if notify:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise SystemExit("server closed stdout")
        reply = json.loads(line)
        if reply.get("id") == n:
            wire.append({"direction": "server -> client", "message": reply})
            return reply


rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "other-squad-client", "version": "0"}})
rpc("notifications/initialized", notify=True)
tools = rpc("tools/list", {})["result"]["tools"]
print("discovered tools:", [t["name"] for t in tools])
tool = next(t["name"] for t in tools if "wording" in t["name"] or "policy" in t["name"])
for date in ("2024-08-11", "2025-01-01"):
    r = rpc("tools/call", {"name": tool, "arguments": {"query": "flood water ingress engine damage", "product": "motor",
                                                        "loss_date": date, "trace_id": f"other-squad-{date}"}})
    body = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
    body = body.get("result", body)
    print(date, "->", body.get("status"), [(h["clause_id"], h["endorsement_version"]) for h in body.get("hits", [])],
          "| echoed trace id:", body.get("_server", {}).get("trace_id"))
proc.terminate()
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
Path(a.out).write_text(json.dumps(wire, indent=1), encoding="utf-8")
print("wire capture written to", a.out)
