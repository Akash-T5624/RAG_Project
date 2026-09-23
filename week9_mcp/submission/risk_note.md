Writer: the claims platform team supplied claims_system_server.py; treat it as third-party code until reviewed and pinned.
Reach: its configured token can read claim status and adjuster-note history for every claim exposed by that service.
Logs: the host trace records server name, tool name, arguments, and returned data; claim numbers and notes need redaction/retention controls.
Stolen token: an attacker could query open-claim status and sensitive adjuster notes remotely within the token's scope.
Decision: do not ship remote access until least-privilege, short-lived tokens, TLS/OAuth validation, audit review, and note-level authorization are in place.
