"""Supporting infrastructure for the ONE retrieval change:

BEFORE:  semantic FAISS retrieval (all-MiniLM-L6-v2 + IndexFlatIP cosine)
AFTER:   semantic FAISS retrieval + BM25 keyword retrieval + RRF fusion (k=60)

This module only changes HOW chunks are ranked/retrieved. It does not touch
chunking, embeddings, the FAISS index, the LLM, or the generation prompt.
"""

import math
import pickle
import re

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75

_embedding_model = None
_index = None
_chunks = None
_metadata = None
_bm25 = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


def load_corpus():
    global _index, _chunks, _metadata
    if _index is not None:
        return _index, _chunks, _metadata

    _index = faiss.read_index("vectors.index")
    with open("chunks.pkl", "rb") as f:
        data = pickle.load(f)
    _chunks = data["chunks"]
    _metadata = data["metadata"]
    return _index, _chunks, _metadata


def chunk_id(index_id):
    """chunk_id is the positional index into chunks.pkl (what FAISS uses)."""
    return f"chunk_{index_id}"


def query_to_vector(query):
    model = _get_embedding_model()
    vector = model.encode([query], convert_to_numpy=True)
    vector = np.array(vector, dtype="float32")
    faiss.normalize_L2(vector)
    return vector


def tokenize(text):
    """Lowercase alphanumeric tokenization used for BM25."""
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25:
    """Okapi BM25 over the existing 72 chunks (pure python, no new deps)."""

    def __init__(self, corpus, k1=BM25_K1, b=BM25_B):
        self.k1 = k1
        self.b = b
        self.corpus = [tokenize(doc) for doc in corpus]
        self.doc_lens = [len(doc) for doc in self.corpus]
        self.avgdl = sum(self.doc_lens) / len(self.doc_lens)
        self.df = {}
        self.doc_freq = []
        for doc in self.corpus:
            term_freqs = {}
            for term in doc:
                term_freqs[term] = term_freqs.get(term, 0) + 1
            self.doc_freq.append(term_freqs)
            for term in term_freqs:
                self.df[term] = self.df.get(term, 0) + 1
        self.doc_count = len(self.corpus)

    def idf(self, term):
        n = self.df.get(term, 0)
        return math.log(1 + (self.doc_count - n + 0.5) / (n + 0.5))

    def score(self, query_terms, doc_idx):
        doc_len = self.doc_lens[doc_idx]
        denom = 1 - self.b + self.b * (doc_len / self.avgdl)
        score = 0.0
        freqs = self.doc_freq[doc_idx]
        for term in set(query_terms):
            tf = freqs.get(term, 0)
            if tf == 0:
                continue
            score += self.idf(term) * (tf * (self.k1 + 1)) / (tf + self.k1 * denom)
        return score

    def rank(self, query_terms, top_k=60):
        scores = []
        for i in range(self.doc_count):
            scores.append((i, self.score(query_terms, i)))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


def get_bm25():
    global _bm25
    if _bm25 is None:
        _, chunks, _ = load_corpus()
        _bm25 = BM25(chunks)
    return _bm25


def semantic_search(query, top_k=60):
    """FAISS semantic retrieval over the existing corpus (current retriever)."""
    index, _, _ = load_corpus()
    query_vector = query_to_vector(query)
    distances, indices = index.search(query_vector, top_k)

    results = []
    for rank, index_id in enumerate(indices[0]):
        if index_id == -1:
            continue
        results.append({
            "rank": rank + 1,
            "chunk_idx": int(index_id),
            "chunk_id": chunk_id(int(index_id)),
            "score": float(distances[0][rank]),
            "chunk": _chunks[index_id],
        })
    return results


def rrf_fusion(semantic_results, keyword_results, k=RRF_K):
    """Reciprocal Rank Fusion. Combines RANKS, never raw scores."""
    fused = {}
    for rank, result in enumerate(semantic_results):
        cid = result["chunk_id"]
        fused.setdefault(cid, {"score": 0.0, "chunk_idx": result["chunk_idx"]})
        fused[cid]["score"] += 1.0 / (k + result["rank"])

    for rank, (doc_idx, _bm25_score) in enumerate(keyword_results):
        cid = chunk_id(doc_idx)
        if cid not in fused:
            fused[cid] = {"score": 0.0, "chunk_idx": doc_idx}
        fused[cid]["score"] += 1.0 / (k + rank + 1)

    fused_list = [
        {"chunk_idx": v["chunk_idx"], "chunk_id": cid, "rrf_score": v["score"]}
        for cid, v in fused.items()
    ]
    fused_list.sort(key=lambda x: x["rrf_score"], reverse=True)
    for rank, result in enumerate(fused_list):
        result["rank"] = rank + 1
    return fused_list


def hybrid_search(query, semantic_top_k=60, bm25_top_k=60, rrf_k=RRF_K, final_top_k=5):
    """AFTER retrieval: FAISS + BM25 + RRF(k=60), then top results for generation."""
    semantic_results = semantic_search(query, top_k=semantic_top_k)

    bm25 = get_bm25()
    keyword_results = bm25.rank(tokenize(query), top_k=bm25_top_k)

    fused = rrf_fusion(semantic_results, keyword_results, k=rrf_k)

    final = []
    for result in fused[:final_top_k]:
        idx = result["chunk_idx"]
        final.append({
            "rank": result["rank"],
            "chunk_idx": idx,
            "chunk_id": result["chunk_id"],
            "score": result["rrf_score"],
            "chunk": _chunks[idx],
            "metadata": _metadata[idx],
        })
    return final