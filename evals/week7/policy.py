import math
import pickle
import re

from config import POLICY_INDEX_PATH, POLICY_TOP_K


def _tokenize(text):
    return re.findall(r"[a-z0-9]+", text.lower())


# Number-to-symbol helper so "exc 7.4" style text is searchable too.
_POLICY_CHUNKS = None


def _load_chunks():
    global _POLICY_CHUNKS
    if _POLICY_CHUNKS is None:
        with open(POLICY_INDEX_PATH, "rb") as f:
            data = pickle.load(f)
        _POLICY_CHUNKS = [
            {
                "text": data["chunks"][i],
                "page_number": data["metadata"][i]["page_number"],
                "section": _section_of(data["chunks"][i]),
            }
            for i in range(len(data["chunks"]))
        ]
    return _POLICY_CHUNKS


def _section_of(text):
    """Best-effort section label: the first 'N. Title' line of a chunk."""
    m = re.search(r"\n\s*(\d{1,2})\.\s*([A-Z][A-Za-z ,&'\-]{3,})", text)
    if m:
        return f"{m.group(1)}. {m.group(2).strip()}"
    return "policy"


def _bm25_build(corpus, k1=1.5, b=0.75):
    docs = [_tokenize(c["text"]) for c in corpus]
    doc_lens = [len(d) for d in docs]
    avgdl = sum(doc_lens) / max(1, len(docs))
    df = {}
    doc_freq = []
    for doc in docs:
        freqs = {}
        for term in doc:
            freqs[term] = freqs.get(term, 0) + 1
        doc_freq.append(freqs)
        for term in freqs:
            df[term] = df.get(term, 0) + 1
    n_docs = len(docs)

    def idf(term):
        return math.log(1 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))

    def score(qterms, idx):
        doc_len = doc_lens[idx]
        denom = 1 - b + b * (doc_len / avgdl)
        s = 0.0
        freqs = doc_freq[idx]
        for term in set(qterms):
            tf = freqs.get(term, 0)
            if tf == 0:
                continue
            s += idf(term) * (tf * (k1 + 1)) / (tf + k1 * denom)
        return s

    return score, n_docs


def search_policy(query, top_k=POLICY_TOP_K):
    """Return the top_k policy passages most relevant to the query.

    Returns a list of dicts: {rank, page_number, section, snippet, score}.
    Deterministic: no LLM, no randomness.
    """
    corpus = _load_chunks()
    if not query or not query.strip():
        return []
    score_fn, _ = _bm25_build(corpus)
    qterms = _tokenize(query)
    scored = [(i, score_fn(qterms, i)) for i in range(len(corpus))]
    scored.sort(key=lambda x: x[1], reverse=True)
    out = []
    for rank, (idx, s) in enumerate(scored[:top_k], 1):
        if s <= 0:
            continue
        chunk = corpus[idx]
        out.append({
            "rank": rank,
            "page_number": chunk["page_number"],
            "section": chunk["section"],
            "snippet": chunk["text"][:400].strip(),
            "score": round(float(s), 4),
        })
    return out


def policy_document():
    """The full policy text as a single block (used for the workflow evidence)."""
    return "\n\n".join(c["text"].strip() for c in _load_chunks())


if __name__ == "__main__":
    import json

    for q in ["flood water ingress engine", "tyres and tubes", "deductible excess",
              "driving without valid licence", "racing"]:
        print("Q:", q)
        print(json.dumps(search_policy(q), indent=2, ensure_ascii=False))
        print()