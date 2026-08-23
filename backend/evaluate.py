"""Evaluation driver for the Week 4 Practical.

Runs the golden set through:
  --stage baseline  -> pure FAISS semantic search (retrieval.semantic_search)
  --stage after     -> FAISS + BM25 + RRF(k=60) (retrieval.hybrid_search)

Records per-question: top-3 chunk_ids, scores, hit@3, retrieval latency, and
(optionally) the generated answer. Saves a JSON snapshot for later analysis.

Usage:
  python evaluate.py --stage baseline --out baseline_results.json
  python evaluate.py --stage after --out after_results.json --with-generation
"""

import argparse
import contextlib
import io
import json
import time
from statistics import median


def suppress_stdout():
    return contextlib.redirect_stdout(io.StringIO())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["baseline", "after"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--with-generation", action="store_true")
    args = parser.parse_args()

    with suppress_stdout():
        import retrieval

    with open("golden_set.jsonl", encoding="utf-8") as f:
        questions = [json.loads(line) for line in f]

    index, chunks, _ = retrieval.load_corpus()
    idx_by_text = {text: i for i, text in enumerate(chunks)}

    for q in questions:
        expected_idx = int(q["expected_chunk_id"].split("_")[1])
        if not (0 <= expected_idx < len(chunks)):
            raise SystemExit(
                f"Golden set error: {q['expected_chunk_id']} not in corpus"
            )

    # Warm-up: one untimed retrieval so the measured latencies reflect
    # steady-state per-query retrieval, not one-time init (model load,
    # corpus load, BM25 index build).
    warmup = questions[0]
    if args.stage == "baseline":
        retrieval.semantic_search(warmup["question"], top_k=5)
    else:
        retrieval.hybrid_search(warmup["question"], final_top_k=5)

    results = []

    for q in questions:
        t0 = time.perf_counter()

        if args.stage == "baseline":
            hits = retrieval.semantic_search(q["question"], top_k=5)
        else:
            hits = retrieval.hybrid_search(q["question"], final_top_k=5)

        latency_ms = (time.perf_counter() - t0) * 1000.0

        top_chunk_ids = []
        scores = []
        chunk_texts = []

        for hit in hits:
            if args.stage == "baseline":
                chunk_id = "chunk_%d" % idx_by_text[hit["chunk"]]
            else:
                chunk_id = hit["chunk_id"]
            top_chunk_ids.append(chunk_id)
            scores.append(hit["score"])
            chunk_texts.append(hit["chunk"])

        expected = q["expected_chunk_id"]
        hit_at_3 = 1 if expected in top_chunk_ids[:3] else 0

        answer = None
        if args.with_generation:
            with suppress_stdout():
                answer = query.generate_answer(q["question"], hits)

        results.append({
            "question_id": q["id"],
            "question": q["question"],
            "expected_chunk_id": expected,
            "category": q["category"],
            "top3_chunk_ids": top_chunk_ids[:3],
            "top5_chunk_ids": top_chunk_ids,
            "scores": scores,
            "hit_at_3": hit_at_3,
            "retrieval_latency_ms": round(latency_ms, 3),
            "retrieved_chunk_texts": chunk_texts,
            "answer": answer,
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"stage": args.stage, "results": results}, f, indent=2, ensure_ascii=False)

    total = len(results)
    hits = sum(r["hit_at_3"] for r in results)
    latencies = [r["retrieval_latency_ms"] for r in results]

    print(f"\n=== {args.stage.upper()} (saved to {args.out}) ===")
    for r in results:
        print(
            f"{r['question_id']}: hit@3={r['hit_at_3']} "
            f"exp={r['expected_chunk_id']} top3={r['top3_chunk_ids']} "
            f"lat={r['retrieval_latency_ms']:.1f}ms"
        )
    print(f"\nhit-rate@3 = {hits}/{total} = {hits / total * 100:.1f}%")
    print(f"p50 retrieval latency = {median(latencies):.2f} ms")


if __name__ == "__main__":
    main()