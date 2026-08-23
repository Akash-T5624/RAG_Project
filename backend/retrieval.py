"""Retrieval pipeline: FAISS semantic + BM25 keyword + RRF + MMR + Cross-Encoder reranking.

Pipeline stages:
  1. FAISS semantic search (cosine via L2-normalized IndexFlatIP)  → top 60
  2. BM25 keyword search (Okapi BM25)                             → top 60
  3. RRF fusion (reciprocal rank fusion, k=60)                    → 60–100 candidates
  4. MMR diversity selection (maximal marginal relevance)          → 20 candidates
  5. Cross-Encoder reranking (BAAI/bge-reranker-base)             → top 5

Why each stage exists:
  - FAISS semantic search: captures meaning/semantics, finds conceptually related text
  - BM25 keyword search: captures exact keyword matches that embeddings may miss
  - RRF fusion: combines rankings from both retrievers without requiring score
    normalization (semantic scores and BM25 scores are on incompatible scales)
  - MMR diversity: removes near-duplicate chunks so the LLM sees varied information
    rather than the same fact repeated in slightly different phrasings
  - Cross-Encoder reranking: a small, precise model that reads the full query+chunk
    pair jointly, catching relevance that bi-encoders (FAISS embeddings) miss because
    bi-encoders encode query and document independently
  - Final top-5: LLM context windows are limited; feeding fewer, higher-quality chunks
    reduces noise and improves answer accuracy

This module only changes HOW chunks are ranked/retrieved. It does not touch
chunking, embeddings, the FAISS index, the LLM, or the generation prompt.
"""

import math
import pickle
import re

import faiss
import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
RERANKER_MODEL_NAME = "BAAI/bge-reranker-base"

RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75

MMR_LAMBDA = 0.5
_embedding_model = None
_reranker = None
_index = None
_chunks = None
_metadata = None
_bm25 = None
_embeddings = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


def _get_reranker():
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(RERANKER_MODEL_NAME)
    return _reranker


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


def _load_embeddings():
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    index, _, _ = load_corpus()
    n = index.ntotal
    dim = index.d
    matrix = np.empty((n, dim), dtype="float32")
    for i in range(n):
        matrix[i] = index.reconstruct(i)
    _embeddings = matrix
    return _embeddings

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
    """Okapi BM25 over the existing chunks (pure python, no new deps)."""

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

def mmr_select(query, candidates, top_k=20, lambda_param=MMR_LAMBDA):
    if not candidates or not query or not query.strip():
        return []

    # Deduplicate by chunk_id (preserve first occurrence / highest RRF rank).
    seen = set()
    unique = []
    for c in candidates:
        if c["chunk_id"] not in seen:
            seen.add(c["chunk_id"])
            unique.append(c)
    candidates = unique

    # Clamp top_k to available candidates.
    top_k = min(top_k, len(candidates))
    if top_k == 0:
        return []

    # Get query embedding (L2-normalized).
    q_emb = query_to_vector(query).flatten()  # (dim,)

    # Build candidate embedding matrix from cached corpus embeddings.
    all_emb = _load_embeddings()
    idx_list = [c["chunk_idx"] for c in candidates]

    # Filter out candidates with missing / out-of-range embeddings.
    valid = []
    valid_idx = []
    for i, ci in enumerate(idx_list):
        if 0 <= ci < all_emb.shape[0]:
            valid.append(candidates[i])
            valid_idx.append(ci)

    if not valid:
        # Fallback: return top_k by RRF score when no embeddings are available.
        return [
            {
                "chunk_idx": c["chunk_idx"],
                "chunk_id": c["chunk_id"],
                "rrf_score": c["rrf_score"],
                "mmr_score": c["rrf_score"],
            }
            for c in candidates[:top_k]
        ]

    n_valid = len(valid)
    cand_emb = all_emb[valid_idx]  # (n_valid, dim) — already L2-normalized

    # Query–candidate cosine similarities (dot product of normalized vectors).
    q_cand_sim = cand_emb @ q_emb  # (n_valid,)

    # Chunk–chunk cosine similarity matrix (for diversity term).
    # Shape: (n_valid, n_valid).  Since vectors are L2-normalized,
    # dot product = cosine similarity.
    c_cand_sim = cand_emb @ cand_emb.T  # (n_valid, n_valid)

    # Greedy MMR selection.
    selected = []       # indices into valid/cand_emb
    remaining = set(range(n_valid))

    def _best():
        """Select the candidate with the highest MMR score."""
        best_i, best_score = -1, -1.0
        for i in remaining:
            rel = q_cand_sim[i]
            if selected:
                # Max similarity to any already-selected chunk.
                div = max(c_cand_sim[i][j] for j in selected)
            else:
                div = 0.0
            mmr = lambda_param * rel - (1.0 - lambda_param) * div
            if mmr > best_score:
                best_score = mmr
                best_i = i
        return best_i, best_score

    mmr_scores = []
    for _ in range(top_k):
        if not remaining:
            break
        best_i, best_score = _best()
        if best_i == -1:
            break
        selected.append(best_i)
        remaining.discard(best_i)
        mmr_scores.append((valid[best_i], float(best_score)))

    return [
        {
            "chunk_idx": c["chunk_idx"],
            "chunk_id": c["chunk_id"],
            "rrf_score": c["rrf_score"],
            "mmr_score": score,
        }
        for c, score in mmr_scores
    ]


def rerank_results(query, candidates, top_k=5):
    if not candidates or not query or not query.strip():
        return []

    # Clamp top_k to available candidates.
    top_k = min(top_k, len(candidates))

    reranker = _get_reranker()

    # Build (query, chunk_text) pairs for the cross-encoder.
    _, chunks, _ = load_corpus()
    pairs = [(query, chunks[c["chunk_idx"]]) for c in candidates]

    # CrossEncoder.predict() returns an array of relevance scores.
    scores = reranker.predict(pairs)

    # Pair each candidate with its rerank score, sort descending.
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)

    final = []
    for rank, (cand, rerank_score) in enumerate(scored[:top_k]):
        idx = cand["chunk_idx"]
        final.append({
            "rank": rank + 1,
            "chunk_idx": idx,
            "chunk_id": cand["chunk_id"],
            "chunk": chunks[idx],
            "metadata": _metadata[idx],
            "rrf_score": cand.get("rrf_score"),
            "mmr_score": cand.get("mmr_score"),
            "rerank_score": float(rerank_score),
            "score": float(rerank_score),
        })
    return final

def hybrid_search(
    query,
    semantic_top_k=60,
    bm25_top_k=60,
    rrf_k=RRF_K,
    mmr_top_k=20,
    final_top_k=5,
):
    
    # --- Guard: empty query ---
    if not query or not query.strip():
        return []

    # --- Stage 1: FAISS semantic search → top 60 ---
    semantic_results = semantic_search(query, top_k=semantic_top_k)

    # --- Stage 2: BM25 keyword search → top 60 ---
    bm25 = get_bm25()
    keyword_results = bm25.rank(tokenize(query), top_k=bm25_top_k)

    # --- Stage 3: RRF fusion → 60–100 candidates ---
    fused = rrf_fusion(semantic_results, keyword_results, k=rrf_k)

    # --- Guard: no fused results ---
    if not fused:
        return []

    # Take up to 100 candidates for the diversity / reranking stages.
    candidates = fused[:100]

    # --- Stage 4: MMR diversity selection → 20 chunks ---
    mmr_results = mmr_select(query, candidates, top_k=mmr_top_k)

    # --- Stage 5: Cross-Encoder reranking → top 5 ---
    final_results = rerank_results(query, mmr_results, top_k=final_top_k)

    return final_results
