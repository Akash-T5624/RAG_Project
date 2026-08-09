import faiss
import PyPDF2
import numpy as np
import pickle
from sentence_transformers import SentenceTransformer

print("Loading embedding model...")

embedding_model = SentenceTransformer(
    "sentence-transformers/all-MiniLM-L6-v2"
)

print("Embedding model loaded")

def pdf_to_vectors(pdf_path):

    print(f"\nReading PDF: {pdf_path}")

    with open(pdf_path, "rb") as f:

        pdf_reader = PyPDF2.PdfReader(f)

        total_pages = len(pdf_reader.pages)

        print(f"Total pages: {total_pages}")

        page_texts = []

        for page_num, page in enumerate(pdf_reader.pages):

            page_text = page.extract_text() or ""

            page_texts.append({
                "text": page_text,
                "page_number": page_num + 1
            })

    chunks = []
    chunk_metadata = []

    chunk_size = 500
    chunk_overlap = 50

    for page in page_texts:

        page_text = page["text"]
        page_number = page["page_number"]

        if not page_text.strip():
            continue

        step = chunk_size - chunk_overlap

        for start in range(
            0,
            len(page_text),
            step
        ):

            chunk_text = page_text[
                start:start + chunk_size
            ]

            if not chunk_text.strip():
                continue

            chunks.append(chunk_text)

            chunk_metadata.append({
                "page_number": page_number,
                "start_position": start
            })

    print(f"Created {len(chunks)} chunks")

    if not chunks:

        print("No text found in PDF.")

        return None, [], []

    print("\nCreating local embeddings...")

    embeddings = embedding_model.encode(
        chunks,
        show_progress_bar=True,
        convert_to_numpy=True
    )

    embeddings = np.array(
        embeddings,
        dtype="float32"
    )

    print(
        f"Vector shape: {embeddings.shape}"
    )

    faiss.normalize_L2(embeddings)

    print("Creating FAISS index...")

    dimension = embeddings.shape[1]

    index = faiss.IndexFlatIP(
        dimension
    )

    index.add(embeddings)

    print("Saving FAISS index...")

    faiss.write_index(
        index,
        "vectors.index"
    )

    print("Saving chunks and metadata...")

    with open("chunks.pkl", "wb") as f:

        pickle.dump(
            {
                "chunks": chunks,
                "metadata": chunk_metadata,
                "total_pages": total_pages
            },
            f
        )

    print("\nVECTOR DATABASE CREATED")

    print("Files created:")

    print("   vectors.index")
    print("   chunks.pkl")

    print(
        f"\nNumber of chunks: {len(chunks)}"
    )

    print(
        f"Vector dimension: "
        f"{embeddings.shape[1]}"
    )

    return (
        embeddings,
        chunks,
        chunk_metadata
    )


if __name__ == "__main__":

    pdf_file = "documents/first_world_war.pdf"

    embeddings, chunks, metadata = pdf_to_vectors(
        pdf_file
    )

    print("\nSetup completed!")