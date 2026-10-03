"""Bonus: the AgentCard the orchestrator would advertise, plus the failed case
mapped onto the A2A task lifecycle.

Writes results/a2a_bonus.md.
"""

import json

from race_config import FAILURE_INJECTION_CASE, MODEL, RESULTS_DIR

AGENT_CARD = {
    "name": "claims-squad-orchestrator",
    "description": (
        "Insurance claims triage. Decomposes one claim question, delegates the "
        "coverage/exclusions read and the adjuster-note summary to two narrow "
        "specialist agents, synthesises status + exclusion clause, and returns "
        "the structured claim contract. Hand-off between agents uses A2A; tool "
        "access inside each agent uses MCP."),
    "url": "https://claims.internal/a2a/claims-squad",
    "version": "1.0.0",
    "provider": {"organization": "Soft Suave - AI Engineering League"},
    "documentationUrl": "https://claims.internal/docs/claims-squad",
    "capabilities": {
        "streaming": True,
        "pushNotifications": True,
        "stateTransitionHistory": True,
    },
    "authenticationSchemes": [
        {"scheme": "bearer", "description": "OAuth2 client-credentials JWT, scope claims:triage"}
    ],
    "defaultInputModes": ["text/plain", "application/json"],
    "defaultOutputModes": ["application/json"],
    "skills": [
        {
            "id": "coverage-exclusions",
            "name": "Coverage & exclusions read",
            "description": "Answers whether the policy covers the loss and which "
                           "exclusion clause applies, quoting the clause id from "
                           "policy evidence only.",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
            "examples": ["{\"claim_id\": \"W6-002\"}"],
        },
        {
            "id": "adjuster-note-summary",
            "name": "Adjuster note summary",
            "description": "Extracts claim identity facts, key facts, denial "
                           "language and any clause id cited in the notes.",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
            "examples": ["{\"claim_id\": \"W6-002\"}"],
        },
        {
            "id": "claim-triage",
            "name": "Claim triage decision",
            "description": "Returns the full claim contract: status "
                           "(approved|excluded|rejected), exclusion clause and "
                           "computed payout.",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
            "examples": ["{\"claim_id\": \"W6-002\", \"question\": " +
                         "\"is this claim payable?\"}"],
        },
    ],
}


def main(argv=None):
    case = FAILURE_INJECTION_CASE
    md = []
    md.append("# Bonus - AgentCard + A2A task lifecycle (Week 10 Module 5)")
    md.append("")
    md.append("## 1. The AgentCard the orchestrator would publish")
    md.append("")
    md.append("Served at `/.well-known/agent.json` - the machine-readable card an "
              "A2A client reads *before* sending any work: skills (what it can do), "
              "input/output modes (how to talk to it) and auth (who may).")
    md.append("")
    md.append("```json")
    md.append(json.dumps(AGENT_CARD, indent=2, ensure_ascii=False))
    md.append("```")
    md.append("")
    md.append("The two specialists publish their own cards too "
              "(`coverage-exclusions`, `adjuster-note-summary`), each advertising "
              "only its narrow skill and only its narrow tool surface - the card "
              "is where 'narrow specialist' becomes a contract instead of a prompt.")
    md.append("")
    md.append("## 2. The failed case on the A2A task lifecycle")
    md.append("")
    md.append(f"Case `{case}`: coverage/exclusions worker returns HTTP 500, twice.")
    md.append("")
    md.append("| A2A task state | What happens here |")
    md.append("| --- | --- |")
    md.append("| `submitted` | Client posts the claim question; orchestrator returns task id. |")
    md.append("| `working` | Manager plans, summary worker returns, coverage worker is dispatched. |")
    md.append(f"| `working` -> attempt 1, 2 | Coverage worker answers HTTP 500 both times "
              "(the injected failure). |")
    md.append("| **`input-required`** | **Where this case SHOULD end:** the task pauses "
              "with a message asking the adjuster to confirm the exclusions for this "
              "claim; the partial facts are kept in the task state, not thrown away. |")
    md.append("| `completed` | Only after the adjuster replies - then synthesis runs and "
              "the claim contract is returned. |")
    md.append("| `failed` | What the current implementation does instead: no "
              "human-in-the-loop path exists, so a dead worker becomes a dead claim. |")
    md.append("")
    md.append(f"**Verdict for `{case}`: it should end `input-required` (paused, awaiting "
              "the adjuster's confirmation), not `failed`.** The claim already has its "
              "identity facts and its summary; only the coverage read is missing, and "
              "one human answer is enough to finish it - `failed` discards work the "
              "orchestrator actually did, and forces the client to restart the whole "
              "task from scratch.")
    md.append("")
    md.append("## 3. What A2A buys over a plain REST call to the worker")
    md.append("")
    md.append("A REST POST to the worker gives you one request/one response: a 500 is "
              "just a 500, there is no task identity, no state, no way to resume - the "
              "caller either retries blindly or gives up. A2A gives the exchange a "
              "**task id and a state machine** (`submitted` / `working` / "
              "`input-required` / `completed` / `failed`) plus an AgentCard that told "
              "the manager what the worker can do *before* it called, so the failure "
              "can be parked as `input-required` for a human, resumed later with the "
              "context intact, and notified via push - instead of being lost.")
    md.append("")
    md.append(f"Model under test: `{MODEL}`. MCP vs A2A in one line: **MCP connects an "
              "agent to tools; A2A connects an agent to other agents.**")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "a2a_bonus.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    (RESULTS_DIR / "agent_card.json").write_text(
        json.dumps(AGENT_CARD, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out} and {RESULTS_DIR / 'agent_card.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
