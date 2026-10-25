"""Loss-date-aware wording retrieval (the Week 3-4 pipeline, wired in).

The wording store is the versioned endorsement clauses. Ranking reuses the Week 3-4 hybrid
stack (FAISS + BM25 + RRF + cross-encoder, backend/retrieval.hybrid_search_per_doc) over an
index built from the clauses in force; when faiss/torch are not installed it falls back to a
small BM25 and says so in every result (`ranker`). The date filter is CODE: it runs BEFORE
ranking, so a clause that was not in force on the loss date can never be returned.
"""

import json
import math
import os
import re
import threading

from capstone.core.dates import BadDate, in_range, parse_date
from capstone.core.paths import DATA, REPORTS

_STORE = {"clauses": None}
_INDEX_CACHE = {}
_LOCK = threading.Lock()


def load_clauses():
    if _STORE["clauses"] is None:
        raw = json.loads((DATA / "endorsements.json").read_text(encoding="utf-8"))
        clauses = raw["clauses"]
        for i, c in enumerate(clauses):
            c["chunk_id"] = f"{c['endorsement_ref']}:{c['clause_id']}"
            c["page"] = 10 + i // 3
            c["anchor"] = f"reports/wording/{c['endorsement_ref']}.md#{_slug(c['clause_id'])}"
        _STORE["clauses"] = clauses
    return _STORE["clauses"]


def _slug(clause_id):
    return re.sub(r"[^a-z0-9]+", "-", clause_id.lower()).strip("-")


def versions_in_force(product, loss_date):
    day = parse_date(loss_date)
    return sorted({c["endorsement_version"] for c in load_clauses()
                   if c["product"] == product and in_range(day, c["effective_from"], c["effective_to"])})


def _doc_text(c):
    return f"{c['clause_id']} {c['title']}. {c['text']}"


def _tok(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _bm25_rank(query, pool, k1=1.5, b=0.75):
    docs = [_tok(_doc_text(c)) for c in pool]
    avg = sum(map(len, docs)) / max(1, len(docs))
    df = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    scored = []
    for i, d in enumerate(docs):
        s = 0.0
        for t in set(_tok(query)):
            tf = d.count(t)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg))
        scored.append((i, s))
    scored.sort(key=lambda x: -x[1])
    return [(i, s) for i, s in scored if s > 0]


def _hybrid_rank(query, pool, top_k):
    """Week 3-4 hybrid retriever over an index of exactly this pool. Raises ImportError if deps are missing."""
    import faiss
    import numpy as np
    import retrieval  # backend/retrieval.py

    key = tuple(c["chunk_id"] for c in pool)
    with _LOCK:
        if key not in _INDEX_CACHE:
            chunks = [_doc_text(c) for c in pool]
            emb = np.array(retrieval._get_embedding_model().encode(chunks, convert_to_numpy=True), dtype="float32")
            faiss.normalize_L2(emb)
            index = faiss.IndexFlatIP(emb.shape[1])
            index.add(emb)
            meta = [{"page_number": c["page"], "start_position": 0} for c in pool]
            _INDEX_CACHE[key] = (index, chunks, meta)
        index, chunks, meta = _INDEX_CACHE[key]
    results = retrieval.hybrid_search_per_doc(query, index, chunks, meta, top_k=top_k)
    return [(r["chunk_index"], r["score"]) for r in results]


def _rank(query, pool, top_k):
    if os.getenv("CAPSTONE_RANKER", "").lower() != "bm25":
        try:
            return "hybrid", _hybrid_rank(query, pool, top_k)[:top_k]
        except ImportError:
            pass
    return "bm25-fallback", [(i, s) for i, s in _bm25_rank(query, pool)[:top_k]]


def search_wording(query, product, loss_date, top_k=3, date_filter=True):
    """Return clauses ranked for `query`, restricted to the wording in force on `loss_date`.

    date_filter=False reproduces the pre-fix behaviour (best-ranked wording wins, any date);
    it exists so the before/after measurement is run on the same code path.
    """
    base = {"query": query, "product": product, "loss_date": str(loss_date), "date_filter": date_filter}
    try:
        day = parse_date(loss_date)
    except BadDate as exc:
        return {**base, "status": "BAD_DATE", "recoverable": True, "error": str(exc), "hits": []}
    pool = [c for c in load_clauses() if c["product"] == product]
    if not pool:
        return {**base, "status": "UNKNOWN_PRODUCT", "recoverable": True,
                "error": f"no wording for product {product!r}", "hits": []}
    considered = len(pool)
    if date_filter:
        pool = [c for c in pool if in_range(day, c["effective_from"], c["effective_to"])]
        if not pool:
            return {**base, "status": "NO_WORDING_IN_FORCE", "recoverable": True, "candidates_considered": considered,
                    "error": f"no {product} wording was in force on {day.isoformat()}; do not infer wording, refer to an adjuster",
                    "hits": []}
    ranker, ranked = _rank(query, pool, top_k)
    hits = []
    for rank, (idx, score) in enumerate(ranked, 1):
        c = pool[idx]
        hits.append({
            "rank": rank, "chunk_id": c["chunk_id"], "clause_id": c["clause_id"], "title": c["title"],
            "endorsement_version": c["endorsement_version"], "endorsement_ref": c["endorsement_ref"],
            "effective_from": c["effective_from"], "effective_to": c["effective_to"],
            "page": c["page"], "anchor": c["anchor"], "score": round(float(score), 4), "text": c["text"],
        })
    return {**base, "status": "ok", "recoverable": False, "ranker": ranker,
            "candidates_considered": considered, "candidates_in_force": len(pool), "hits": hits}


def render_wording_pages():
    """Write reports/wording/<ref>.md so citation anchors are real, clickable targets."""
    out = REPORTS / "wording"
    out.mkdir(parents=True, exist_ok=True)
    by_ref = {}
    for c in load_clauses():
        by_ref.setdefault(c["endorsement_ref"], []).append(c)
    for ref, cs in by_ref.items():
        c0 = cs[0]
        lines = [f"# {ref} (endorsement version {c0['endorsement_version']})", "",
                 f"In force {c0['effective_from']} to {c0['effective_to'] or 'open'}. SYNTHETIC wording for the capstone.", ""]
        for c in cs:
            lines += [f'<a id="{_slug(c["clause_id"])}"></a>', f"## {c['clause_id']} {c['title']}", "", c["text"], ""]
        (out / f"{ref}.md").write_text("\n".join(lines), encoding="utf-8")
    return sorted(by_ref)
