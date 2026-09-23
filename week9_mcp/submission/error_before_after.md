# Same failing policy search: before and after

Failing arguments: `{"query": "unlisted astronomical peril"}`

## Before — legacy tool docstring and opaque failure

Tool description exposed by `tools/list`: `Search policy.`

Raw `tools/call` response: `{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"text": "Error calling tool 'search_policy_documents': Error 3", "type": "text"}], "isError": true}}`

Model-facing handling: “The tool returned an opaque failure. I cannot determine coverage and need a human/system retry.”

## After — docstring as a prompt and recoverable result

Tool description exposed by `tools/list`: “Search the first-party policy corpus for wording needed to answer a coverage question. … Treat a `recoverable: true` response as a retrieval miss … do not invent coverage.”

Raw `tools/call` response: `{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"text": "{\"recoverable\":true,\"error\":{\"code\":\"POLICY_CONTEXT_NOT_FOUND\",\"message\":\"No policy passage found for 'unlisted astronomical peril'. Try a coverage, exclusion, or deductible topic; do not infer policy terms from an empty search.\"},\"matches\":[]}", "type": "text"}], "isError": false, "structuredContent": {"recoverable": true, "error": {"code": "POLICY_CONTEXT_NOT_FOUND", "message": "No policy passage found for 'unlisted astronomical peril'. Try a coverage, exclusion, or deductible topic; do not infer policy terms from an empty search."}, "matches": []}}}`

Model-facing handling: “No policy wording was found for that topic. Please provide a coverage, exclusion, or deductible topic; I will not infer coverage from an empty search.”

The server returned a normal tool result containing `recoverable: true`, so the host can continue the same conversation instead of treating a lookup miss as a crashed system.
