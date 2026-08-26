import argparse
import json
import sys
import time

import app  # loads .env + embedding model once; reuses the exact live pipeline
from trace_log import build_trace, save_trace, TRACE_DIR, DEMO_TRACE_DIR

# Model output can contain non-cp1252 characters (Windows console).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default="golden_set.jsonl",
                        help="JSONL file with a 'question' field per line")
    parser.add_argument("--repeats", type=int, default=1,
                        help="Times to repeat each question (default 1)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only use the first N questions")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between requests (be nice to the API)")
    parser.add_argument("--demo", action="store_true",
                        help="Write traces to traces_demo/ (curated bonus set)")
    args = parser.parse_args()

    with open(args.questions, encoding="utf-8") as f:
        questions = [json.loads(line)["question"] for line in f if line.strip()]
    if args.limit:
        questions = questions[:args.limit]

    registry = app._read_registry()
    if not registry:
        raise SystemExit("No documents uploaded. Upload a PDF via the app or /upload first.")

    doc_id = max(registry.items(), key=lambda kv: kv[1].get("created_at", ""))[0]
    index, chunks, metadata = app._load_index_for_doc(doc_id)
    if index is None:
        raise SystemExit(f"Index files missing for doc {doc_id}; re-upload the PDF.")

    target = DEMO_TRACE_DIR if args.demo else TRACE_DIR
    total = len(questions) * args.repeats
    print(f"Running {len(questions)} questions x {args.repeats} repeats "
          f"= {total} traces -> {target.name}/ (doc {doc_id})")

    done = 0
    for repeat in range(args.repeats):
        for q in questions:
            started_at = time.perf_counter()
            llm_messages = [{"role": "system", "content": app.SYSTEM_PROMPT},
                            {"role": "user", "content": q}]
            try:
                results = app._search_database(q, index, chunks, metadata)
                context = app._build_context(results)
                payload = [{
                    "role": "system",
                    "content": app.SYSTEM_PROMPT + "\n\n" + context,
                }, {"role": "user", "content": q}]
                answer = app._groq_complete(payload)
                error = None
            except Exception as exc:
                results, payload, answer = [], llm_messages, ""
                error = {"detail": str(exc)}

            latency_ms = (time.perf_counter() - started_at) * 1000.0
            trace = build_trace(
                endpoint="batch",
                session_id=None,
                doc_id=doc_id,
                prompt_version=app.PROMPT_VERSION,
                question=q,
                llm_messages=payload,
                retrieval_results=results,
                model=app.GROQ_MODEL,
                temperature=0.5,
                stream=False,
                raw_output=answer,
                latency_ms=latency_ms,
                error=error,
            )
            save_trace(trace, target)
            done += 1
            status = "ok" if not error else f"ERROR ({error['detail'][:60]})"
            print(f"[{done}/{total}] {trace['trace_id'][:8]}... {status}")
            if args.delay and done < total:
                time.sleep(args.delay)

    print(f"\nDone. {done} traces in {target}")


if __name__ == "__main__":
    main()
