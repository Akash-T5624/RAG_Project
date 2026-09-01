"""Hierarchical parent-context retrieval experiment (Week 6 main improvement).

Problem: fixed child chunks retrieved alone lose their parent/section context
("This must be completed within 7 days" without the parent "Vehicle Inspection"
heading is ambiguous). This module implements the controlled before/after:

  BASELINE : Document -> fixed child chunks -> hybrid retrieve top-k -> LLM
  AFTER    : Document -> parent/child structure -> retrieve child -> recover
             parent context (the page/section the child came from) -> combined
             child+parent context -> LLM

The test set (retrieval_tests.jsonl) is used UNCHANGED for both runs; only the
context assembly differs, so any before/after difference is attributable to the
hierarchical (parent-context) retrieval change.

Each case writes:
  - deterministic check: does the final answer contain the expected phrase?
  - whether the recovered parent context was actually used
  - LLM raw output

Usage:
    python hierarchical.py            # run baseline + after, write results/
    python hierarchical.py --answers  # (default) call Groq for the answers
    python hierarchical.py --no-llm   # cached answers only (re-run checks only)
"""

import json
import math
import pickle
import re
import sys
import time
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge as judge_mod  # reuses _chat / retry logic (same env + API)

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent / "backend"
PDF = BACKEND / "uploads" / "6332cc27-8fe9-4635-a34a-a7d2f6c1deb6_bike.pdf"

CHILD_SIZE = 300
CHILD_OVERLAP = 60
TOP_K = 3
RETRIEVAL_TOP_N = 12

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
RRF_K = 60

# Week-5 taxonomy modes this retrieval test exercises. Each case has exactly one.
# expected_phrase: deterministic substring the final answer must contain.

RETRIEVAL_CASES = [
    {
        "id": "rc_001",
        "mode": "retrieval_failure",
        "question": "what is the policy number?",
        "expected_phrases": ["DBTR10541714586"],
        "note": "policy number on page 4 schedule; exact-value retrieval",
    },
    {
        "id": "rc_002",
        "mode": "exact_value",
        "question": "what is the bike registration number?",
        "expected_phrases": ["TN09DB0750"],
    },
    {
        "id": "rc_003",
        "mode": "hierarchical_context",
        "question": "after an accident, what is the first step to claim?",
        "expected_any": ["inform acko", "contact acko", "step 1", "first step"],
        "note": "step 1 on page 3; child chunk says only 'STEP 1'",
    },
    {
        "id": "rc_004",
        "mode": "hierarchical_context",
        "question": "how many steps are in the acko claim process?",
        "expected_any": ["3", "three", "3 easy ways"],
        "note": "claim procedure heading + 3 steps on page 3 (parent context)",
    },
    {
        "id": "rc_005",
        "mode": "spelling_error",
        "question": "is repining a kesibilty for ths policy?",
        "expected_any": ["one click", "renewal"],
        "note": "deliberate typo variant of 'is renewal a possibility for this policy?'",
    },
    {
        "id": "rc_006",
        "mode": "grammar_variation",
        "question": "policy vehicle use for hire or reward covered?",
        "expected_any": ["not covered", "not payable", "is not", "excluded",
                         "does not cover", "not cover"],
        "note": "parent page 6 'Limitations as to use': hire or reward NOT covered",
    },
    {
        "id": "rc_007",
        "mode": "exact_value",
        "question": "what is the acko gstin number?",
        "expected_phrases": ["29AAOCA9055C1ZF"],
    },
    {
        "id": "rc_008",
        "mode": "retrieval_failure",
        "question": "how do i renew the bike policy?",
        "expected_any": ["one click", "renewal"],
        "note": "page 5 'One click renewal of your Policy!'",
    },
]


def load_pdf_pages() -> list[str]:
    import PyPDF2
    with open(PDF, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        pages = [p.extract_text() or "" for p in reader.pages]
    return pages


def build_children(pages: list[str]) -> list[dict]:
    """Fixed-size child chunks with overlap; parent = page index."""
    children = []
    for page_idx, page in enumerate(pages):
        step = CHILD_SIZE - CHILD_OVERLAP
        start = 0
        while start < max(len(page), 1):
            chunk = page[start:start + CHILD_SIZE]
            if chunk.strip():
                children.append({
                    "text": chunk,
                    "parent_page": page_idx,
                })
            if start + CHILD_SIZE >= len(page):
                break
            start += step
    return children


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25:
    def __init__(self, corpus):
        self.n = len(corpus)
        self.lens = [len(t) for t in corpus]
        self.avgdl = sum(self.lens) / self.n
        self.df = [{} for _ in range(self.n)]
        self.global_df = {}
        for i, doc in enumerate(corpus):
            for term in set(doc):
                self.df[i][term] = self.df[i].get(term, 0) + 1
                self.global_df[term] = self.global_df.get(term, 0) + 1
        for i, doc in enumerate(corpus):
            for term in doc:
                self.df[i][term] = self.df[i].get(term, 0)

    def idf(self, term):
        n = self.global_df.get(term, 0)
        return math.log(1 + (self.n - n + 0.5) / (n + 0.5))

    def score(self, query, doc_idx):
        dl = self.lens[doc_idx]
        denom = 1 - 0.75 + 0.75 * (dl / self.avgdl)
        s = 0.0
        for term in set(query):
            tf = self.df[doc_idx].get(term, 0)
            if not tf:
                continue
            s += self.idf(term) * (tf * 2.5) / (tf + 2.5 * denom)
        return s

    def rank(self, query, top_k=10):
        scored = sorted(((self.score(query, i), i) for i in range(self.n)),
                        reverse=True)
        return scored[:top_k]


def build_index(children):
    """Embed children (L2-normalized) into a small FAISS index."""
    model = SentenceTransformer(MODEL_NAME)
    texts = [c["text"] for c in children]
    emb = model.encode(texts, convert_to_numpy=True)
    emb = np.asarray(emb, dtype="float32")
    faiss.normalize_L2(emb)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    return index, model


def retrieve(question, children, index, model, top_k=TOP_K):
    """Hybrid: FAISS semantic + BM25 keyword + RRF -> top_k children."""
    qv = model.encode([question], convert_to_numpy=True).astype("float32")
    faiss.normalize_L2(qv)
    dist, idx = index.search(qv, RETRIEVAL_TOP_N)

    bm = BM25([tokenize(c["text"]) for c in children])
    kw = bm.rank(tokenize(question), top_k=RETRIEVAL_TOP_N)

    fused = {}
    for rank, i in enumerate(idx[0]):
        if i == -1:
            continue
        fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (score, i) in enumerate(kw):
        fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    return [{"child_idx": i, "rrf": s, "text": children[i]["text"],
             "parent_page": children[i]["parent_page"]}
            for i, s in ranked[:top_k]]


def build_context_baseline(retrieved):
    ctx = ""
    for rank, r in enumerate(retrieved):
        ctx += f"\n--- Document Chunk {rank + 1} ---\n{r['text']}\n"
    return ctx


def build_context_after(retrieved, pages):
    """Child chunks PLUS the parent page(s) that contain them (hierarchical)."""
    ctx = ""
    seen_pages = set()
    for rank, r in enumerate(retrieved):
        ctx += f"\n--- Document Chunk {rank + 1} ---\n{r['text']}\n"
        p = r["parent_page"]
        if p not in seen_pages:
            seen_pages.add(p)
            ctx += f"\n--- Parent section (page {p + 1}) ---\n{pages[p]}\n"
    return ctx


ANSWER_PROMPT = (
    "You are an insurance Q&A assistant. Answer using ONLY the document context.\n"
    "If the answer cannot be found in the context, say exactly:\n"
    '"I could not find the answer in the provided document."\n'
    "Document context:\n{context}\n\nQuestion:\n{question}\n\nAnswer:"
)


def answer_question(question, context):
    prompt = ANSWER_PROMPT.format(question=question, context=context)
    return judge_mod._chat(prompt, temperature=0.0)


def _norm(text: str) -> str:
    """Deterministic normalisation: lowercase, drop markdown/ASCII symbols, collapse spaces."""
    text = re.sub(r"[*_`#\[\]\{\}\|>~]", "", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def check(answer, case):
    """Deterministic correctness check: expected_phrases (all) OR expected_any."""
    a = _norm(answer)
    if case.get("expected_phrases"):
        if all(_norm(p) in a for p in case["expected_phrases"]):
            return True
    if case.get("expected_any"):
        if any(_norm(p) in a for p in case["expected_any"]):
            return True
    return False


def main():
    cache_path = HERE / "results" / "retrieval_answers_cache.json"
    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    pages = load_pdf_pages()
    children = build_children(pages)
    print(f"Built {len(children)} child chunks from {len(pages)} parent sections")

    index, model = build_index(children)

    baseline_out = []
    after_out = []
    for case in RETRIEVAL_CASES:
        q = case["question"]
        ret = retrieve(q, children, index, model)
        cb = build_context_baseline(ret)
        ca = build_context_after(ret, pages)
        key = case["id"]
        if key not in cache:
            cache[key] = {}
            cache[key]["baseline"] = answer_question(q, cb)
            cache[key]["after"] = answer_question(q, ca)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  {key} baseline/after answered; sleeping 2s (rate limit)")
            time.sleep(2)

        b = cache[key]["baseline"]
        a = cache[key]["after"]
        base_pass = check(b, case)
        after_pass = check(a, case)
        baseline_out.append({
            "id": case["id"], "mode": case["mode"], "question": q,
            "expected": case.get("expected_phrases") or case.get("expected_any"),
            "answer": b, "passed": base_pass,
            "retrieved_parents": list(set(r["parent_page"] for r in ret)),
        })
        after_out.append({
            "id": case["id"], "mode": case["mode"], "question": q,
            "expected": case.get("expected_phrases") or case.get("expected_any"),
            "answer": a, "passed": after_pass,
            "retrieved_parents": list(set(r["parent_page"] for r in ret)),
        })

    results_dir = HERE / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "baseline_results.json").write_text(
        json.dumps(baseline_out, indent=2, ensure_ascii=False), encoding="utf-8")
    (results_dir / "after_results.json").write_text(
        json.dumps(after_out, indent=2, ensure_ascii=False), encoding="utf-8")

    def passrate(out):
        return sum(1 for r in out if r["passed"])

    print("\n=== RETRIEVAL BEFORE/AFTER (hierarchical parent-context) ===")
    print(f"{'Mode':<26}{'Before':>8}{'After':>8}")
    modes = sorted(set(c["mode"] for c in RETRIEVAL_CASES))
    tb = ta = 0
    for m in modes:
        bm = [r for r in baseline_out if r["mode"] == m]
        am = [r for r in after_out if r["mode"] == m]
        bp = sum(1 for r in bm if r["passed"])
        ap = sum(1 for r in am if r["passed"])
        tb += bp; ta += ap
        print(f"{m:<26}{bp}/{len(bm):<7}{ap}/{len(am):<7}")
    print(f"{'TOTAL':<26}{tb}/{len(baseline_out):<7}{ta}/{len(after_out):<7}")
    print()
    for r in baseline_out:
        if not r["passed"]:
            print(f"  BASELINE MISS  {r['id']} ({r['mode']}): {r['answer'][:100]!r}")
    for r in after_out:
        if not r["passed"]:
            print(f"  AFTER MISS     {r['id']} ({r['mode']}): {r['answer'][:100]!r}")


if __name__ == "__main__":
    main()