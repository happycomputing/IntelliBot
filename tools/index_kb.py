#!/usr/bin/env python3
import os, json, glob
from typing import List, Dict
import numpy as np
from sentence_transformers import SentenceTransformer
import faiss

RAW_DIR = "kb/raw"
IDX_DIR = "kb/index"

def index_kb(chunk_size=900, chunk_overlap=150):
    """Build FAISS vector index from crawled documents"""
    
    def chunk_text(text: str, url: str):
        chunks = []
        i = 0
        while i < len(text):
            chunk = text[i:i+chunk_size]
            if chunk.strip():
                chunks.append({"url": url, "text": chunk.strip()})
            i += chunk_size - chunk_overlap
        return chunks

    def load_docs() -> List[Dict]:
        docs = []
        for fp in glob.glob(os.path.join(RAW_DIR, "*.json")):
            with open(fp, "r", encoding="utf-8") as f:
                j = json.load(f)
                docs.extend(chunk_text(j["text"], j["url"]))
        return docs

    os.makedirs(IDX_DIR, exist_ok=True)
    print("Loading docs…")
    docs = load_docs()
    if not docs:
        raise SystemExit("No crawled docs found. Run the crawler first.")
    texts = [d["text"] for d in docs]
    print(f"Chunks: {len(texts)}")

    print("Embedding…")
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    X = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=True).astype("float32")

    print("Indexing…")
    index = faiss.IndexFlatIP(X.shape[1])
    index.add(X)

    np.save(os.path.join(IDX_DIR, "embeddings.npy"), X)
    with open(os.path.join(IDX_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False)
    faiss.write_index(index, os.path.join(IDX_DIR, "faiss.index"))
    print(f"OK. Saved to {IDX_DIR}/")
    return {"chunks": len(texts), "dimension": X.shape[1]}

if __name__ == "__main__":
    index_kb()
