# PDF RAG Application

A ChatGPT-style web application that lets you upload PDF documents and ask
questions about them. Answers are generated **only from the content of your
uploaded PDFs** (Retrieval-Augmented Generation), streamed token-by-token,
and always accompanied by the exact page sources they came from.

## Project Structure

```text
RAG_project/
├── backend/    # Python FastAPI server, RAG pipeline, and CLI mode
└── frontend/   # React (Vite) web interface
```

## Tech Stack

| Layer     | Technology                                                                 |
| --------- | -------------------------------------------------------------------------- |
| Frontend  | React 19 + Vite, axios (REST), native fetch (SSE streaming), react-markdown |
| Backend   | Python FastAPI, PyPDF2, sentence-transformers (`all-MiniLM-L6-v2`), FAISS  |
| LLM       | Groq API — `llama-3.3-70b-versatile` (configurable via `GROQ_MODEL`)       |

## Prerequisites

- **Python 3.10+**
- **Node.js 20+** and npm
- A free **Groq API key** → get one at <https://console.groq.com/keys>

---

## How to Run

### 1. Backend Setup

Open a terminal in the project root:

```powershell
cd backend

# Install Python dependencies
python -m pip install -r requirements.txt

# Create your .env file (copy the example and fill in your Groq key)
copy .env.example .env
```

Then open `backend/.env` and set your key:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Start the API server:

```powershell
uvicorn app:app --port 8000
```

The backend is now running at <http://localhost:8000>.
Interactive API docs are available at <http://localhost:8000/docs>.

> The first startup takes a minute — it downloads and loads the local
> embedding model (`all-MiniLM-L6-v2`).

### 2. Frontend Setup

Open a **second terminal** in the project root:

```powershell
cd frontend

# Install Node dependencies
npm install

# Start the dev server
npm run dev
```

Then open <http://localhost:5173> in your browser.

In development, Vite proxies all API routes (`/upload`, `/documents`, `/chat`,
`/chats`, `/history`, `/health`) to `http://localhost:8000`, so no CORS setup
is needed. To point the frontend at a different backend URL, set `VITE_API_URL`
in `frontend/.env.local`.

### 3. Using the App

1. Upload a PDF using the sidebar.
2. Ask questions about it in the chat box.
3. Answers stream in as Markdown with cited page sources underneath.

### Production Mode (optional)

Build the frontend once and let the backend serve it — no dev servers needed:

```powershell
cd frontend
npm run build        # outputs static files to frontend/dist/

cd ../backend
uvicorn app:app      # automatically serves frontend/dist when it exists
```

### CLI Mode (optional, no browser)

The original command-line pipeline still works. Put a PDF in
`backend/documents`, set its path in `pdf_reader.py` if needed, then run:

```powershell
cd backend
python main.py
```

`main.py` runs `pdf_reader.py` (builds the vector database) followed by
`query.py` (interactive Q&A loop). Type `exit` to quit.

---

## Complete Application Flow

The application has two entry points that share the same RAG pipeline:
the **web app** (frontend + FastAPI) and the **CLI mode** (`main.py`).

### High-Level Flow

```text
┌────────────────────────────────────────────────────────────────────┐
│                          INGESTION PHASE                           │
│                                                                    │
│  User uploads PDF ──► /upload endpoint                             │
│        │                                                           │
│        ▼                                                           │
│  pdf_reader.extract_pdf_chunks()                                   │
│    • PyPDF2 extracts text page by page                             │
│    • Each page split into overlapping chunks (500 chars / 100      │
│      overlap), tagged with page number + position                  │
│        │                                                           │
│        ▼                                                           │
│  embed_chunks()                                                    │
│    • all-MiniLM-L6-v2 encodes every chunk into a 384-dim vector    │
│    • Vectors are L2-normalized (cosine similarity ready)           │
│        │                                                           │
│        ▼                                                           │
│  FAISS IndexFlatIP built and persisted                             │
│    • indices/{doc_id}_vectors.index  (vectors)                     │
│    • indices/{doc_id}_chunks.pkl     (chunks + metadata)           │
│    • data/documents.json             (document registry)           │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                         QUERY PHASE                                │
│                                                                    │
│  User asks a question ──► /chat/stream endpoint                    │
│        │                                                           │
│        ▼                                                           │
│  Question embedded ──► FAISS similarity search (top-K=5 chunks)    │
│        │                                                           │
│        ▼                                                           │
│  Top chunks assembled into a context block inside SYSTEM_PROMPT    │
│  ("Answer ONLY from this context; say you can't find the answer    │
│   otherwise")                                                      │
│        │                                                           │
│        ▼                                                           │
│  Prompt + last 6 conversation turns sent to Groq                   │
│  (llama-3.3-70b-versatile) with streaming enabled                  │
│        │                                                           │
│        ▼                                                           │
│  Tokens stream back over SSE ──► rendered live in the UI           │
│  Q&A pair saved to chats/{session_id}.json                         │
└────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Detail

#### A. Document Ingestion (Upload)

1. The user selects a PDF in the frontend sidebar. `api.js` POSTs the file
   to `/upload`.
2. The backend validates the file (must be `.pdf`, ≤ 25 MB by default),
   assigns it a UUID `doc_id`, and saves it to `backend/uploads/`.
3. **Text extraction** — `pdf_reader.extract_pdf_chunks()` reads the PDF with
   PyPDF2, pulling text from each page separately so page numbers can be kept
   as metadata. Scanned/image-only PDFs produce no text and are rejected.
4. **Chunking** — each page's text is split into ~500-character chunks with a
   100-character overlap (so sentences spanning boundaries aren't lost).
   Every chunk records `{page_number, start_position}`.
5. **Embedding** — all chunks pass through the local
   `sentence-transformers/all-MiniLM-L6-v2` model, producing 384-dimension
   vectors that are L2-normalized so inner product = cosine similarity.
6. **Indexing** — vectors go into a FAISS `IndexFlatIP`. The index, chunks,
   and metadata are written to disk under `backend/indices/{doc_id}_*`, and
   the document is registered in `backend/data/documents.json`. An in-memory
   cache is warmed so the first question doesn't need a disk reload.
7. The UI refreshes its document list via `GET /documents`.

#### B. Chat Session Management

- Chats are stored as individual JSON files in `backend/chats/`, each holding
  a title, timestamps, the bound `doc_id`, and the full message history.
- `POST /chat/new` creates a session; `GET /chats` lists them;
  `PATCH /history/{id}` renames; `DELETE /history/{id}` removes.
- Deleting a document also detaches any chat sessions that referenced it.

#### C. Asking a Question (RAG Query)

1. The user sends a question; the frontend calls `POST /chat/stream`
   (SSE streaming version of `/chat`).
2. **Document resolution** — the backend picks the target document from
   (in order): the request's explicit `doc_id`, the chat session's bound
   document, or the most recently uploaded document.
3. **Retrieval** — the question is embedded with the same MiniLM model and
   searched against the document's FAISS index; the top 5 most similar
   chunks (with their page numbers and scores) are selected.
4. **Prompt construction** — retrieved chunks are formatted into a labeled
   context block and appended to the system prompt, which instructs the model
   to answer *only* from the provided context and admit when the answer isn't
   present (grounding / hallucination guard).
5. **Conversation memory** — up to the last 6 turns of the current session
   are included, so follow-up questions like "explain that further" work.
6. **Generation** — the message list goes to Groq's OpenAI-compatible
   `chat/completions` endpoint with `stream: true`.

#### D. Streaming the Answer Back

1. The backend wraps Groq's token stream in a Server-Sent Events response
   emitting typed events: `meta` (session/doc ids) → `sources` (the retrieved
   chunks with pages & scores) → `token` events (answer pieces) → `done`.
2. The frontend reads the SSE stream with native `fetch`, appending tokens to
   the message bubble in real time and rendering them as Markdown.
3. When the answer completes, the source cards (rank, page number, score,
   chunk preview) appear beneath it, letting the user verify claims against
   the actual PDF pages.
4. Finally, the backend persists the question + full answer to the session's
   JSON file and auto-titles new sessions from the first question.

#### E. Error Handling

- Missing/invalid Groq key, unreachable API, or upstream errors surface as
  `error` SSE events shown inline in the chat.
- Corrupted registries/chat files are logged and skipped gracefully rather
  than crashing the server.
- Writes to disk use atomic temp-file + replace to avoid partial files.

### Data Stored on Disk (all under `backend/`)

| Path                     | Contents                                    |
| ------------------------ | ------------------------------------------- |
| `uploads/`               | Original uploaded PDFs (`{doc_id}_{name}`)  |
| `indices/{doc_id}_vectors.index` | Per-document FAISS vector index     |
| `indices/{doc_id}_chunks.pkl`    | Per-document chunks + metadata      |
| `data/documents.json`    | Registry of all uploaded documents          |
| `chats/{session_id}.json`| One file per chat session (full history)    |

## Configuration (backend/.env)

| Variable               | Default                      | Purpose                       |
| ---------------------- | ---------------------------- | ----------------------------- |
| `GROQ_API_KEY`         | *(required)*                 | Your Groq API key             |
| `GROQ_MODEL`           | `llama-3.3-70b-versatile`    | Chat model used for answers   |
| `GROQ_TIMEOUT_SECONDS` | `60`                         | Upstream request timeout      |
| `MAX_UPLOAD_MB`        | `25`                         | Max PDF upload size           |
| `RAG_TOP_K`            | `5`                          | Chunks retrieved per question |
| `CORS_ORIGINS`         | localhost:5173, localhost:4173 | Allowed dev origins         |
| `LOG_LEVEL`            | `INFO`                       | Logging verbosity             |

---

## Week 7 Module 4 — Claims Agent vs Fixed Workflow Race

Same-task race between a hand-built ReAct claims agent and a deterministic
fixed workflow, on the insurance-claims evaluation set
(`evals/week6/eval_set.jsonl`, 27 claims). Both systems use the **same tools**
(`evals/week7/tools.py`), the **same output contract**, the **same evaluator**,
and the **same model configuration**.

### Run

```powershell
python run_agent.py W6-002       # one claim through the agent (Groq)
python run_workflow.py W6-002    # one claim through the fixed workflow
python race.py                   # full race: 10 claims x both systems
python test_budget.py            # budget-termination test -> budget_termination.log
```

### Artifacts

- `race.csv` — per-claim reproducibility master (latency, tokens, cost,
  termination reason, pass).
- `evals/week7/results/` — `race_metrics.json`, `per_claim_results.json`,
  `comparison_table.md`, `verdict.md` (final table + <150-word verdict),
  `tool_description_diff.md`, `budget_termination_max_tokens.log`.
- `budget_termination.log` — clean termination when `MAX_ITERS=1` is forced.
- `evals/week7/logs/` — per-claim agent/workflow logs (gitignored `*.log`).

### Result (2026-09-06)

| Metric | Agent | Fixed Workflow |
| --- | ---: | ---: |
| Pass rate | 100% (10/10) | 100% (10/10) |
| p50 latency | 31.7 s | 2.7 ms |
| Total tokens | 46,466 | 0 |
| Cost per claim | $0.004273 | $0.000000 |

### Documented assumptions (see `results/verdict.md`)

- Claim amounts are absent from the eval set; `DEFAULT_CLAIM_AMOUNT = 30,000`
  is used for every claim (`WEEK7_CLAIM_AMOUNT` overridable).
- The backend-configured `openai/gpt-oss-120b` is model-level rate limited on
  the shared key (429 + multi-minute cool-off); the race uses
  `qwen/qwen3.8-27b` on the same key for **both** systems (`WEEK7_MODEL`
  overridable), at Groq's list price $0.80/$4.00 per 1M tokens.
- The workflow is deterministic code with no LLM in its path, so 0 tokens /
  $0 is a property of the implementation, measured, not assumed.
- All four agent budgets (iterations, tokens, cost, wall-clock) are enforced
  inside the loop and their terminations are logged.
