# Notes — Week 5 Error Analysis (Track D · Insurance claims)

Open coding date: <!-- YYYY-MM-DD -->
Pipeline version at sampling time: `v1-hybrid-rrf60`
Traces read from: `backend/traces/` (one JSON file per request)

## 1. Seeded random sample

> Paste the full output of `python sample_traces.py --seed <N> --count 20` here.

```text
(paste here)
```

## 2. Replay evidence (one trace, from the trace alone)

Trace replayed: `<trace_id>`
Replay status / missing fields:

```text
(paste the summary line + verdict from replay_trace.py)
```

Original vs replayed output: see [replay_evidence.md](replay_evidence.md)
(pasted below if you prefer one file).

```text
(paste original + replayed blocks here, or reference the file)
```

## 3. Redaction confirmation

Claimant names and claim/policy numbers are redacted **before** the trace file
is written — `trace_log.build_trace()` is the single choke point and
`save_trace()` refuses nothing but receives only its output. No post-write
scrubbing exists or is needed.

- [ ] Confirmed: redaction happens at write time, not after.

## 4. Open coding — 20 traces, one honest sentence each

Rules I followed: description of what I SAW only; no categories, no diagnoses,
no fixes; zero code changes made during this step. "I don't know why this
failed" is allowed.

| # | trace_id | What I saw (verbatim sentence) |
|---|----------|--------------------------------|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |
| 8 | | |
| 9 | | |
| 10 | | |
| 11 | | |
| 12 | | |
| 13 | | |
| 14 | | |
| 15 | | |
| 16 | | |
| 17 | | |
| 18 | | |
| 19 | | |
| 20 | | |

## 5. Prediction (committed BEFORE any fix)

See [prediction.md](prediction.md).

- Commit date:
- Commit hash:
- Committed before any pipeline change: yes/no

## 6. Why a public benchmark would not have caught my top modes (3 sentences)

1.
2.
3.

## 7. Bonus — random vs curated demo set (optional)

Top mode frequency in random sample: __ / 20 = __ %
Top mode frequency in demo set: __ / 10 = __ %

What my team has been telling itself for the last month:

<!-- paragraph -->
