#!/usr/bin/env python3
import os, json, glob
from typing import List, Dict
import numpy as np
from sentence_transformers import SentenceTransformer
import faiss

RAW_DIR = "kb/raw"
IDX_DIR = "kb/index"

def index_kb(chunk_size=900, chunk_overlap=150, progress_callback=None):
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
        raw_files = glob.glob(os.path.join(RAW_DIR, "*.json"))
        if progress_callback:
            progress_callback('info', f"Loading {len(raw_files)} documents...")
        for fp in raw_files:
            with open(fp, "r", encoding="utf-8") as f:
                j = json.load(f)
                docs.extend(chunk_text(j["text"], j["url"]))
        return docs

    os.makedirs(IDX_DIR, exist_ok=True)
    
    if progress_callback:
        progress_callback('info', "Loading documents...")
    print("Loading docs…")
    docs = load_docs()
    if not docs:
        error_msg = "No documents found. Please add a URL or upload documents first."
        if progress_callback:
            progress_callback('error', error_msg)
        raise ValueError(error_msg)
    texts = [d["text"] for d in docs]
    
    chunk_msg = f"Loaded {len(texts)} chunks from documents"
    print(chunk_msg)
    if progress_callback:
        progress_callback('success', chunk_msg)

    if progress_callback:
        progress_callback('info', "Creating embeddings (this may take a while)...")
    print("Embedding…")
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    X = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False).astype("float32")

    if progress_callback:
        progress_callback('success', f"Created {X.shape[0]} embeddings with {X.shape[1]} dimensions")
        progress_callback('info', "Building FAISS index...")
    print("Indexing…")
    index = faiss.IndexFlatIP(X.shape[1])
    index.add(X)

    np.save(os.path.join(IDX_DIR, "embeddings.npy"), X)
    with open(os.path.join(IDX_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False)
    faiss.write_index(index, os.path.join(IDX_DIR, "faiss.index"))
    
    final_msg = f"Index built successfully! {len(texts)} chunks indexed."
    print(final_msg)
    if progress_callback:
        progress_callback('complete', final_msg)
    return {"total_chunks": len(texts), "dimension": X.shape[1]}

if __name__ == "__main__":
    index_kb()
