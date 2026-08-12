import faiss
import pickle
import numpy as np
import os
import requests
from sentence_transformers import SentenceTransformer


def load_env_file(env_path=".env"):
    """Load key/value settings from a local .env file without extra packages."""
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

print("Loading embedding model...")

embedding_model = SentenceTransformer(
    "sentence-transformers/all-MiniLM-L6-v2"
)


print("Embedding model loaded")

print("\nLoading FAISS index...")

index = faiss.read_index("vectors.index")

print("FAISS index loaded")

print("Loading document chunks...")

with open("chunks.pkl", "rb") as f:
    data = pickle.load(f)

chunks = data["chunks"]
metadata = data["metadata"]

print(f"Loaded {len(chunks)} chunks")


def query_to_vector(query):

    print("\nConverting question into vector...")

    vector = embedding_model.encode(
        [query],
        convert_to_numpy=True
    )

    vector = np.array(
        vector,
        dtype="float32"
    )

    faiss.normalize_L2(vector)

    return vector


def search_database(query, top_k=5):

    query_vector = query_to_vector(query)

    print("Searching FAISS database...")

    distances, indices = index.search(
        query_vector,
        top_k
    )

    results = []

    for rank, index_id in enumerate(indices[0]):

        if index_id == -1:
            continue

        results.append({
            "rank": rank + 1,
            "chunk": chunks[index_id],
            "metadata": metadata[index_id],
            "score": float(distances[0][rank])
        })

    return results


def generate_answer(query, results):

    context = ""

    for result in results:

        page_number = result["metadata"]["page_number"]

        context += (
            f"\n--- Document Chunk "
            f"{result['rank']} "
            f"(Page {page_number}) ---\n"
        )

        context += result["chunk"]
        context += "\n"

    prompt = f"""
You are a helpful question-answering assistant.

Answer the user's question using ONLY the
information provided in the document context.

If the answer cannot be found in the context,
say:

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

    print(f"\nSending context to Groq ({GROQ_MODEL})...")

    try:

        response = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.5
            },
            timeout=60
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

        return f"Ollama request failed: {e}"


if __name__ == "__main__":

    print("\nLOCAL RAG SYSTEM")

    print(
        "Embedding model: "
        "all-MiniLM-L6-v2"
    )

    print(
        "LLM: "
        f"Groq / {GROQ_MODEL}"
    )

    print("========================================")

    while True:

        query = input(
            "\nEnter your question "
            "(or type 'exit'): "
        )

        if query.lower().strip() == "exit":

            print("Goodbye!")

            break

        if not query.strip():

            print(
                "Please enter a question."
            )

            continue

        results = search_database(
            query,
            top_k=5
        )

        print("\nRetrieved chunks:")

        for result in results:

            page_number = (
                result["metadata"]["page_number"]
            )

            score = result["score"]

            print(
                f"\n[{result['rank']}] "
                f"Page: {page_number} "
                f"Score: {score:.4f}"
            )

            print(
                result["chunk"][:300]
            )

            print("...")

        answer = generate_answer(
            query,
            results
        )

        print("\n========================================")
        print("ANSWER")
        print("========================================")

        print(answer)
