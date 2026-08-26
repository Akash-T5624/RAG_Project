import faiss
import PyPDF2
import numpy as np
import pickle

from sentence_transformers import SentenceTransformer

_model = None


def get_embedding_model():
    """Lazy-load the sentence transformer once and reuse it everywhere."""
    global _model
    if _model is None:
        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _model


def extract_pdf_chunks(pdf_path, chunk_size=500, chunk_overlap=100):
    """Read a PDF and return (page_texts, chunks, metadata) without side effects."""

    with open(pdf_path, "rb") as f:
        pdf_reader = PyPDF2.PdfReader(f)
        total_pages = len(pdf_reader.pages)

        page_texts = []
        for page_num, page in enumerate(pdf_reader.pages):
            page_text = page.extract_text() or ""
            page_texts.append({
                "text": page_text,
                "page_number": page_num + 1,
            })

    chunks = []
    chunk_metadata = []
    step = chunk_size - chunk_overlap

    for page in page_texts:
        page_text = page["text"]
        page_number = page["page_number"]

        if not page_text.strip():
            continue

        for start in range(0, len(page_text), step):
            chunk_text = page_text[start:start + chunk_size]
            if not chunk_text.strip():
                continue
            chunks.append(chunk_text)
            chunk_metadata.append({
                "page_number": page_number,
                "start_position": start,
            })

    return {
        "total_pages": total_pages,
        "chunks": chunks,
        "metadata": chunk_metadata,
    }


def embed_chunks(chunks):
    """Embed text chunks and L2-normalize them for FAISS inner-product search."""
    model = get_embedding_model()
    embeddings = model.encode(
        chunks,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    embeddings = np.array(embeddings, dtype="float32")
    faiss.normalize_L2(embeddings)
    return embeddings


def save_index_files(index, chunks, metadata, total_pages,
                     index_path="vectors.index", chunks_path="chunks.pkl"):
    """Persist a FAISS index plus its chunks/metadata to disk."""
    faiss.write_index(index, str(index_path))
    with open(chunks_path, "wb") as f:
        pickle.dump(
            {
                "chunks": chunks,
                "metadata": metadata,
                "total_pages": total_pages,
            },
            f,
        )


def pdf_to_vectors(pdf_path):
    """Backward-compatible helper used by main.py / app.py."""
    data = extract_pdf_chunks(pdf_path)

    if not data["chunks"]:
        return None, [], []

    embeddings = embed_chunks(data["chunks"])

    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    save_index_files(
        index,
        data["chunks"],
        data["metadata"],
        data["total_pages"],
    )

    return embeddings, data["chunks"], data["metadata"]


if __name__ == "__main__":

    pdf_file = "documents/LIC_Insurance.pdf"

    embeddings, chunks, metadata = pdf_to_vectors(pdf_file)
