import os
import uuid
import json
import logging
import datetime
import time
from pathlib import Path
from typing import List, Optional, Iterator

import faiss
import numpy as np
import pickle
import requests
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request
from fastapi.middleware.cors import CORSMiddleware

import trace_log
from pdf_reader import extract_pdf_chunks, embed_chunks, get_embedding_model
from retrieval import hybrid_search_per_doc

# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------

def load_env_file(env_path=".env"):
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT_SECONDS", "60"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
TOP_K = int(os.getenv("RAG_TOP_K", "5"))
CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://localhost:4173",
    ).split(",") if o.strip()
]

PROJECT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = PROJECT_DIR / "uploads"
INDEX_DIR = PROJECT_DIR / "indices"
CHAT_DIR = PROJECT_DIR / "chats"
DATA_DIR = PROJECT_DIR / "data"
FRONTEND_DIST = PROJECT_DIR.parent / "frontend" / "dist"
for d in (UPLOAD_DIR, INDEX_DIR, CHAT_DIR, DATA_DIR):
    d.mkdir(exist_ok=True)

REGISTRY_PATH = DATA_DIR / "documents.json"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rag-api")

SYSTEM_PROMPT = (
    "You are a helpful question-answering assistant.\n\n"
    "Answer the user's question using ONLY the information provided in the "
    "document context below. If the answer cannot be found in the context, say:\n"
    "\"I could not find the answer in the provided document.\"\n\n"
    "Do not make up information. Format answers clearly using markdown when helpful."
)

# Bump this whenever SYSTEM_PROMPT, retrieval strategy, or model params change,
# so every trace records exactly which pipeline version produced it.
PROMPT_VERSION = "v2-hierarchical-hybrid"

# ---------------------------------------------------------------------------
# FastAPI app setup
# ---------------------------------------------------------------------------

get_embedding_model()

app = FastAPI(
    title="RAG API",
    description="Backend API for the RAG-enabled ChatGPT-like application.",
    version="2.0.0",
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --- Lazy FAISS index cache (loaded on demand, survives restarts via disk) ---
_index_cache: dict = {}

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    doc_id: Optional[str] = None


class RenameRequest(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# Document registry (persistent)
# ---------------------------------------------------------------------------

def _read_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Document registry corrupted; starting fresh.")
        return {}


def _write_registry(registry: dict):
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    tmp.replace(REGISTRY_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_index_for_doc(doc_id: str):
    """Return cached or disk-loaded (index, chunks, metadata, page_texts) for a document."""
    if doc_id in _index_cache:
        cached = _index_cache[doc_id]
        return cached["index"], cached["chunks"], cached["metadata"], cached.get("page_texts", [])

    index_path = INDEX_DIR / f"{doc_id}_vectors.index"
    chunks_path = INDEX_DIR / f"{doc_id}_chunks.pkl"

    if not index_path.exists() or not chunks_path.exists():
        return None, None, None, []

    index = faiss.read_index(str(index_path))
    with open(chunks_path, "rb") as f:
        data = pickle.load(f)

    _index_cache[doc_id] = {
        "index": index,
        "chunks": data["chunks"],
        "metadata": data["metadata"],
        "page_texts": data.get("page_texts", []),
    }
    return index, data["chunks"], data["metadata"], data.get("page_texts", [])


def _query_to_vector(query: str):
    vector = get_embedding_model().encode([query], convert_to_numpy=True)
    vector = np.array(vector, dtype="float32")
    faiss.normalize_L2(vector)
    return vector


def _search_database(query: str, index, chunks, metadata, top_k: int = TOP_K):
    """Hybrid retrieval: FAISS semantic + BM25 keyword + RRF + Cross-Encoder rerank.

    Replaces the old FAISS-only search.  The hybrid pipeline catches:
      - spelling errors (BM25 partial matching)
      - exact-value lookups (BM25 keyword match for IDs, numbers)
      - semantic misses (FAISS catches conceptual similarity BM25 misses)
    """
    return hybrid_search_per_doc(query, index, chunks, metadata, top_k=top_k)


def _build_context(results, page_texts=None) -> str:
    """Build LLM context with hierarchical parent-page recovery.

    After each child chunk, the full parent page is appended so the LLM sees
    anaphoric references ("these items", "STEP 1") in their proper context.
    This fixes the hierarchical_context failure mode from the Week 5 taxonomy.
    """
    context = ""
    seen_pages = set()
    for result in results:
        page_number = result["metadata"]["page_number"]
        context += (
            f"\n--- Document Chunk {result['rank']} (Page {page_number}) ---\n"
            f"{result['chunk']}\n"
        )
        # Inject parent page context on first encounter for each page.
        if page_texts and page_number not in seen_pages:
            seen_pages.add(page_number)
            page_idx = page_number - 1  # page_number is 1-indexed
            if 0 <= page_idx < len(page_texts) and page_texts[page_idx].strip():
                context += (
                    f"\n--- Parent section (Page {page_number}) ---\n"
                    f"{page_texts[page_idx]}\n"
                )
    return context


def _load_history(session_id: str) -> dict:
    path = CHAT_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chat session not found.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_history(history: dict):
    path = CHAT_DIR / f"{history['session_id']}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)
    tmp.replace(path)


def _recent_llm_messages(history: dict, max_turns: int = 6) -> List[dict]:
    """Build recent conversation turns so follow-up questions have context."""
    msgs = history.get("messages", [])[-max_turns * 2:]
    out = []
    for m in msgs:
        role = "assistant" if m.get("role") == "assistant" else "user"
        out.append({"role": role, "content": m["content"][:2000]})
    return out


def _groq_stream(messages: List[dict]) -> Iterator[str]:
    """Yield answer tokens from Groq's streaming chat completions."""
    if not GROQ_API_KEY or GROQ_API_KEY == "paste_your_groq_api_key_here":
        raise RuntimeError(
            "Groq API key is not configured. Add GROQ_API_KEY to the .env file."
        )
    response = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.5,
            "stream": True,
        },
        timeout=GROQ_TIMEOUT,
        stream=True,
    )
    response.raise_for_status()
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        payload = raw_line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
            delta = chunk["choices"][0].get("delta", {})
            token = delta.get("content")
            if token:
                yield token
        except (json.JSONDecodeError, KeyError, IndexError):
            continue


def _groq_complete(messages: List[dict]) -> str:
    """Non-streaming completion used by the classic /chat endpoint."""
    if not GROQ_API_KEY or GROQ_API_KEY == "paste_your_groq_api_key_here":
        raise RuntimeError(
            "Groq API key is not configured. Add GROQ_API_KEY to the .env file."
        )
    response = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.5,
        },
        timeout=GROQ_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _sse(event_type: str, data) -> str:
    return f"data: {json.dumps({'type': event_type, **data}, ensure_ascii=False)}\n\n"


def _record_chat_trace(*, endpoint, session_id, doc_id, question,
                       llm_messages, results, raw_output, started_at,
                       error=None):
    """Write one complete, redacted-before-write trace to disk."""
    try:
        trace = trace_log.build_trace(
            endpoint=endpoint,
            session_id=session_id,
            doc_id=doc_id,
            prompt_version=PROMPT_VERSION,
            question=question,
            llm_messages=llm_messages,
            retrieval_results=results,
            model=GROQ_MODEL,
            temperature=0.5,
            stream=endpoint == "/chat/stream",
            raw_output=raw_output,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            error=error,
        )
        path = trace_log.save_trace(trace)
        trace_log.save_trace_jsonl(trace)
        logger.info("Trace %s written to %s + trace.jsonl", trace["trace_id"], path.name)
    except Exception:
        # Tracing must never break answering.
        logger.exception("Failed to write trace")


# ---------------------------------------------------------------------------
# Routes: health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health():
    return {
        "status": "ok",
        "model": GROQ_MODEL,
        "api_key_configured": bool(GROQ_API_KEY),
        "time": _now_iso(),
    }


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "message": "RAG API is running"}


# ---------------------------------------------------------------------------
# Routes: documents
# ---------------------------------------------------------------------------

@app.post("/upload", tags=["documents"])
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    safe_name = Path(file.filename).name  # strip any path components
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_MB} MB.",
        )

    doc_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{doc_id}_{safe_name}"
    with open(save_path, "wb") as buffer:
        buffer.write(contents)

    try:
        extracted = extract_pdf_chunks(str(save_path))
    except Exception as exc:
        save_path.unlink(missing_ok=True)
        logger.exception("PDF parsing failed for %s", safe_name)
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse this PDF: {exc}",
        ) from exc

    chunks = extracted["chunks"]
    metadata = extracted["metadata"]

    if not chunks:
        save_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="No text could be extracted from the PDF (scanned/image PDFs are not supported).",
        )

    embeddings = embed_chunks(chunks)

    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)
    faiss.write_index(index, str(INDEX_DIR / f"{doc_id}_vectors.index"))
    with open(INDEX_DIR / f"{doc_id}_chunks.pkl", "wb") as f:
        pickle.dump({
            "chunks": chunks,
            "metadata": metadata,
            "total_pages": extracted["total_pages"],
            "page_texts": extracted.get("page_texts", []),
        }, f)

    _index_cache[doc_id] = {
        "index": index,
        "chunks": chunks,
        "metadata": metadata,
        "page_texts": extracted.get("page_texts", []),
    }

    registry = _read_registry()
    registry[doc_id] = {
        "filename": safe_name,
        "chunks": len(chunks),
        "pages": extracted["total_pages"],
        "created_at": _now_iso(),
    }
    _write_registry(registry)

    logger.info("Uploaded %s as %s (%d chunks)", safe_name, doc_id, len(chunks))
    return JSONResponse(
        status_code=201,
        content={
            "doc_id": doc_id,
            "filename": safe_name,
            "chunks": len(chunks),
            "pages": extracted["total_pages"],
            "message": "PDF processed successfully.",
        },
    )


@app.get("/documents", tags=["documents"])
async def list_documents():
    registry = _read_registry()
    docs = [
        {
            "doc_id": doc_id,
            "filename": info["filename"],
            "pages": info.get("pages"),
            "chunks": info.get("chunks"),
            "created_at": info.get("created_at"),
        }
        for doc_id, info in sorted(
            registry.items(), key=lambda kv: kv[1].get("created_at", "")
        )
    ]
    return {"documents": docs}


@app.delete("/documents/{doc_id}", tags=["documents"])
async def delete_document(doc_id: str):
    registry = _read_registry()
    if doc_id not in registry:
        raise HTTPException(status_code=404, detail="Document not found.")

    filename = registry.pop(doc_id)["filename"]
    _write_registry(registry)
    _index_cache.pop(doc_id, None)

    removed = []
    candidates = [
        UPLOAD_DIR / f"{doc_id}_{filename}",
        INDEX_DIR / f"{doc_id}_vectors.index",
        INDEX_DIR / f"{doc_id}_chunks.pkl",
    ]
    for p in candidates:
        if p.exists():
            p.unlink()
            removed.append(p.name)

    # Detach chats that referenced this document
    for chat_file in CHAT_DIR.glob("*.json"):
        with open(chat_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("doc_id") == doc_id:
            data["doc_id"] = None
            _save_history(data)

    logger.info("Deleted document %s (%s)", doc_id, filename)
    return {"success": True, "doc_id": doc_id, "removed_files": removed}


# ---------------------------------------------------------------------------
# Routes: chat sessions
# ---------------------------------------------------------------------------

@app.post("/chat/new", tags=["chats"])
async def create_chat_session(request: Request):
    """Create a new empty chat session. Accepts ?doc_id=... query param."""
    doc_id = request.query_params.get("doc_id") or None
    session_id = str(uuid.uuid4())
    history = {
        "session_id": session_id,
        "title": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "doc_id": doc_id,
        "messages": [],
    }
    _save_history(history)
    return {
        "session_id": session_id,
        "title": history["title"],
        "created_at": history["created_at"],
        "doc_id": doc_id,
    }


@app.get("/chats", tags=["chats"])
async def list_chats():
    chats = []
    for f in CHAT_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            logger.warning("Skipping corrupt chat file %s", f.name)
            continue
        chats.append({
            "session_id": data["session_id"],
            "title": data.get("title") or "New chat",
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "doc_id": data.get("doc_id"),
            "message_count": len(data.get("messages", [])),
        })
    chats.sort(key=lambda c: c.get("updated_at") or c.get("created_at") or "", reverse=True)
    return {"chats": chats}


@app.get("/history/{session_id}", tags=["chats"])
async def get_history(session_id: str):
    return _load_history(session_id)


@app.delete("/history/{session_id}", tags=["chats"])
async def delete_chat(session_id: str):
    path = CHAT_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chat session not found.")
    path.unlink()
    return {"success": True, "session_id": session_id}


@app.patch("/history/{session_id}", tags=["chats"])
async def rename_chat(session_id: str, body: RenameRequest):
    history = _load_history(session_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty.")
    history["title"] = title[:100]
    _save_history(history)
    return {"success": True, "session_id": session_id, "title": history["title"]}


# ---------------------------------------------------------------------------
# Routes: chat (question answering)
# ---------------------------------------------------------------------------

def _resolve_doc_and_index(request: ChatRequest):
    registry = _read_registry()

    doc_id = request.doc_id
    if not doc_id:
        # Fall back to the session's bound document
        if request.session_id:
            try:
                doc_id = _load_history(request.session_id).get("doc_id")
            except HTTPException:
                doc_id = None
        # Last resort: most recently uploaded document
        if not doc_id and registry:
            doc_id = max(
                registry.items(),
                key=lambda kv: kv[1].get("created_at", ""),
            )[0]

    if not doc_id:
        raise HTTPException(
            status_code=400,
            detail="No document available. Upload a PDF first.",
        )

    if doc_id not in registry:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{doc_id}' not found. It may have been deleted.",
        )

    index, chunks, metadata, page_texts = _load_index_for_doc(doc_id)
    if index is None:
        raise HTTPException(
            status_code=500,
            detail="Document index files are missing. Please re-upload the PDF.",
        )
    return doc_id, index, chunks, metadata, page_texts


@app.post("/chat/stream", tags=["chats"])
async def chat_stream(request: ChatRequest):
    """Ask a question and stream the answer token-by-token over SSE."""
    started_at = time.perf_counter()
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    session_id = request.session_id or str(uuid.uuid4())

    # Create session lazily if it does not exist yet
    try:
        history = _load_history(session_id)
    except HTTPException:
        history = {
            "session_id": session_id,
            "title": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "doc_id": request.doc_id,
            "messages": [],
        }

    doc_id, index, chunks, metadata, page_texts = _resolve_doc_and_index(request)
    if not history.get("doc_id"):
        history["doc_id"] = doc_id

    results = _search_database(question, index, chunks, metadata)
    context = _build_context(results, page_texts)

    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context}]
    llm_messages += _recent_llm_messages(history)
    llm_messages.append({"role": "user", "content": question})

    stream_error = None

    def event_stream() -> Iterator[str]:
        nonlocal stream_error
        answer_parts = []
        try:
            yield _sse("meta", {"session_id": session_id, "doc_id": doc_id})
            yield _sse("sources", {
                "sources": [
                    {
                        "rank": r["rank"],
                        "page": r["metadata"]["page_number"],
                        "score": round(r["score"], 4),
                        "chunk": r["chunk"][:300],
                    }
                    for r in results
                ]
            })
            for token in _groq_stream(llm_messages):
                answer_parts.append(token)
                yield _sse("token", {"content": token})
            yield _sse("done", {})
        except requests.exceptions.ConnectionError:
            stream_error = "Could not reach the Groq API. Check your connection."
            yield _sse("error", {"detail": stream_error})
            return
        except requests.exceptions.HTTPError as exc:
            detail = getattr(exc.response, "text", "") or str(exc)
            stream_error = f"Groq API error: {detail}"
            yield _sse("error", {"detail": stream_error})
            return
        except RuntimeError as exc:
            stream_error = str(exc)
            yield _sse("error", {"detail": stream_error})
            return
        except Exception:
            logger.exception("Streaming chat failed")
            stream_error = "Unexpected server error."
            yield _sse("error", {"detail": stream_error})
            return
        finally:
            answer = "".join(answer_parts)
            if answer:
                now = _now_iso()
                history["messages"].append({"role": "user", "content": question, "timestamp": now})
                history["messages"].append({"role": "assistant", "content": answer, "timestamp": now})
                if not history.get("title"):
                    history["title"] = question[:60] + ("..." if len(question) > 60 else "")
                history["updated_at"] = now
                _save_history(history)
            _record_chat_trace(
                endpoint="/chat/stream",
                session_id=session_id,
                doc_id=doc_id,
                question=question,
                llm_messages=llm_messages,
                results=results,
                raw_output=answer,
                started_at=started_at,
                error={"detail": stream_error} if stream_error else None,
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat", tags=["chats"])
async def chat(request: ChatRequest):
    """Classic non-streaming question endpoint (kept for compatibility)."""
    started_at = time.perf_counter()
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    session_id = request.session_id or str(uuid.uuid4())
    try:
        history = _load_history(session_id)
    except HTTPException:
        history = {
            "session_id": session_id,
            "title": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "doc_id": request.doc_id,
            "messages": [],
        }

    doc_id, index, chunks, metadata, page_texts = _resolve_doc_and_index(request)
    if not history.get("doc_id"):
        history["doc_id"] = doc_id

    results = _search_database(question, index, chunks, metadata)
    context = _build_context(results, page_texts)

    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context}]
    llm_messages += _recent_llm_messages(history)
    llm_messages.append({"role": "user", "content": question})

    try:
        answer = _groq_complete(llm_messages)
    except RuntimeError as exc:
        _record_chat_trace(
            endpoint="/chat", session_id=session_id, doc_id=doc_id,
            question=question, llm_messages=llm_messages, results=results,
            raw_output="", started_at=started_at,
            error={"detail": str(exc)},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("Groq request failed")
        _record_chat_trace(
            endpoint="/chat", session_id=session_id, doc_id=doc_id,
            question=question, llm_messages=llm_messages, results=results,
            raw_output="", started_at=started_at,
            error={"detail": f"Groq request failed: {exc}"},
        )
        raise HTTPException(status_code=502, detail=f"Groq request failed: {exc}") from exc

    now = _now_iso()
    history["messages"].append({"role": "user", "content": question, "timestamp": now})
    history["messages"].append({"role": "assistant", "content": answer, "timestamp": now})
    if not history.get("title"):
        history["title"] = question[:60] + ("..." if len(question) > 60 else "")
    history["updated_at"] = now
    _save_history(history)

    _record_chat_trace(
        endpoint="/chat", session_id=session_id, doc_id=doc_id,
        question=question, llm_messages=llm_messages, results=results,
        raw_output=answer, started_at=started_at,
    )

    return {
        "session_id": session_id,
        "doc_id": doc_id,
        "question": question,
        "answer": answer,
        "sources": [
            {
                "rank": r["rank"],
                "page": r["metadata"]["page_number"],
                "score": round(r["score"], 4),
                "chunk": r["chunk"][:300],
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# Production: serve the built React frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    logger.info("Serving frontend build from %s", FRONTEND_DIST)
