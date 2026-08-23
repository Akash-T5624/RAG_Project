import os

os.environ.setdefault("PYTHONUNBUFFERED", "1")

import faiss
import pickle
import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from sentence_transformers import SentenceTransformer
from pathlib import Path
import uuid
import shutil
import json
import datetime

from pdf_reader import pdf_to_vectors

# -----------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------
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

PROJECT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = PROJECT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
INDEX_DIR = PROJECT_DIR / "indices"
INDEX_DIR.mkdir(exist_ok=True)
CHAT_DIR = PROJECT_DIR / "chats"
CHAT_DIR.mkdir(exist_ok=True)

print("Loading embedding model...")
embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
print("Embedding model loaded")

# -----------------------------------------------------------------------
# App
# -----------------------------------------------------------------------
app = FastAPI(
    title="RAG API",
    description="Backend API for the RAG-enabled ChatGPT-like application.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- In-memory document store (per uploaded document) ---
document_store: dict = {}


# -----------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None
    doc_id: str | None = None


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def _now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _query_to_vector(query: str):
    vector = embedding_model.encode([query], convert_to_numpy=True)
    vector = np.array(vector, dtype="float32")
    faiss.normalize_L2(vector)
    return vector


def _search_database(query: str, index, chunks, metadata, top_k: int = 5):
    query_vector = _query_to_vector(query)
    distance, indices = index.search(query_vector, top_k)
    results = []
    for rank, idx in enumerate(indices[0]):
        if idx == -1:
            continue
        results.append({
            "rank": rank + 1,
            "chunk": chunks[idx],
            "metadata": metadata[idx],
            "score": float(distance[0][rank]),
        })
    return results


def _generate_answer(query: str, results):
    context = ""
    for r in results:
        page_number = r["metadata"]["page_number"]
        context += f"\n--- Document Chunk {r['rank']} (Page {page_number}) ---\n"
        context += r["chunk"]
        context += "\n"

    prompt = f"""
You are a helpful question-answering assistant.

Answer the user's question using ONLY the information provided in the document context.

If the answer cannot be found in the context, say:

"I could not find the answer in the provided document."

Do not make up information.

Document context:
{context}

User question:
{query}

Answer:
"""

    if not GROQ_API_KEY or GROQ_API_KEY == "paste_your_groq_api_key_here":
        return (
            "Groq API key is not configured.\n\n"
            "Add your key to the GROQ_API_KEY setting in the .env file."
        )

    import requests
    try:
        response = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.ConnectionError:
        return (
            "Could not connect to the Groq API.\n\n"
            "Check your internet connection and try again."
        )
    except requests.exceptions.RequestException as e:
        return f"Groq request failed: {e}"


# -----------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "message": "RAG API is running"}


# -----------------------------------------------------------------------
# Document routes
# -----------------------------------------------------------------------
@app.post("/upload", tags=["documents"])
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    doc_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{doc_id}_{file.filename}"
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    embeddings, chunks, metadata = pdf_to_vectors(str(save_path))
    if embeddings is None:
        raise HTTPException(status_code=400, detail="No text could be extracted from the PDF.")

    index_path = INDEX_DIR / f"{doc_id}_vectors.index"
    chunks_path = INDEX_DIR / f"{doc_id}_chunks.pkl"

    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)
    faiss.write_index(index, str(index_path))

    with open(chunks_path, "wb") as f:
        pickle.dump({
            "chunks": chunks,
            "metadata": metadata,
            "total_pages": metadata[-1]["page_number"] if metadata else 0,
        }, f)

    document_store[doc_id] = {
        "filename": file.filename,
        "path": str(save_path),
        "chunks": chunks,
        "metadata": metadata,
        "dimension": dimension,
        "index": index,
    }

    return JSONResponse(
        status_code=201,
        content={
            "doc_id": doc_id,
            "filename": file.filename,
            "chunks": len(chunks),
            "message": "PDF processed successfully.",
        },
    )


@app.get("/documents", tags=["documents"])
async def list_documents():
    return {"documents": [
        {"doc_id": k, "filename": v["filename"]} for k, v in document_store.items()
    ]}


# -----------------------------------------------------------------------
# Chat session routes
# -----------------------------------------------------------------------
@app.post("/chat/new", tags=["chats"])
async def create_chat_session(doc_id: str | None = Form(default=None)):
    session_id = str(uuid.uuid4())
    title = f"Chat {session_id[:8]}"
    history = {
        "session_id": session_id,
        "title": title,
        "created_at": _now_iso(),
        "doc_id": doc_id,
        "messages": [],
    }
    path = CHAT_DIR / f"{session_id}.json"
    with open(path, "w") as f:
        json.dump(history, f)
    return history


@app.get("/chats", tags=["chats"])
async def list_chats():
    chats = []
    for f in sorted(CHAT_DIR.glob("*.json"), reverse=True):
        with open(f, "r") as fh:
            data = json.load(fh)
        chats.append({
            "session_id": data["session_id"],
            "title": data.get("title", "Untitled"),
            "created_at": data["created_at"],
            "doc_id": data.get("doc_id"),
            "message_count": len(data.get("messages", [])),
        })
    return {"chats": chats}


@app.get("/history/{session_id}", tags=["chats"])
async def get_history(session_id: str):
    path = CHAT_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chat session not found.")
    with open(path, "r") as f:
        return json.load(f)


@app.delete("/history/{session_id}", tags=["chats"])
async def delete_chat(session_id: str):
    path = CHAT_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chat session not found.")
    path.unlink()
    return {"success": True, "session_id": session_id}


# -----------------------------------------------------------------------
# Chat route
# -----------------------------------------------------------------------
@app.post("/chat", tags=["chats"])
async def chat(request: ChatRequest):
    doc_id = request.doc_id
    if not doc_id:
        if document_store:
            doc_id = list(document_store.keys())[-1]
        else:
            raise HTTPException(status_code=404, detail="No document available. Upload a PDF first.")

    if doc_id not in document_store:
        chunks_path = INDEX_DIR / f"{doc_id}_chunks.pkl"
        index_path = INDEX_DIR / f"{doc_id}_vectors.index"
        if not chunks_path.exists() or not index_path.exists():
            raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found.")
        index = faiss.read_index(str(index_path))
        with open(chunks_path, "rb") as f:
            data = pickle.load(f)
        document_store[doc_id] = {
            "chunks": data["chunks"],
            "metadata": data["metadata"],
            "index": index,
        }

    doc = document_store[doc_id]
    results = _search_database(
        request.question,
        doc["index"],
        doc["chunks"],
        doc["metadata"],
        top_k=5,
    )
    answer = _generate_answer(request.question, results)

    # Persist chat history
    session_id = request.session_id or str(uuid.uuid4())
    history_path = CHAT_DIR / f"{session_id}.json"
    history = {
        "session_id": session_id,
        "doc_id": doc_id,
        "title": "",
        "created_at": _now_iso(),
        "messages": [],
    }
    if history_path.exists():
        with open(history_path, "r") as f:
            history = json.load(f)

    history.setdefault("messages", []).append({
        "role": "user",
        "content": request.question,
        "timestamp": _now_iso(),
    })
    history["messages"].append({
        "role": "assistant",
        "content": answer,
        "timestamp": _now_iso(),
    })

    title = history.get("title", "")
    if not title:
        title = (request.question[:40] + "...") if len(request.question) > 40 else request.question
    history["title"] = title

    with open(history_path, "w") as f:
        json.dump(history, f)

    return {
        "session_id": session_id,
        "doc_id": doc_id,
        "question": request.question,
        "answer": answer,
        "sources": [
            {"rank": r["rank"], "page": r["metadata"]["page_number"], "score": r["score"], "chunk": r["chunk"][:300]}
            for r in results
        ],
    }
