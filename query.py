import faiss
import pickle
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

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

    print("\nSending context to Llama 3.2...")

    try:

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=300
        )

        response.raise_for_status()

        data = response.json()

        return data["response"]

    except requests.exceptions.ConnectionError:

        return (
            "Could not connect to Ollama.\n\n"
            "Make sure Ollama is installed and running."
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
        "Llama 3.2"
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