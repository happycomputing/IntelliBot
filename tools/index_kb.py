#!/usr/bin/env python3
import os, json, glob
from typing import List, Dict
import numpy as np
from openai import OpenAI

RAW_DIR = "kb/raw"
IDX_DIR = "kb/index"

def index_kb(chunk_size=900, chunk_overlap=150, progress_callback=None):
    """Build vector index from crawled documents using OpenAI embeddings"""
    
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
                text_content = j.get("text") or j.get("content", "")
                if text_content:
                    docs.extend(chunk_text(text_content, j.get("url", "unknown")))
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
        progress_callback('info', "Creating embeddings with OpenAI...")
    print("Embedding with OpenAI...")
    
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    embeddings = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        if progress_callback:
            progress_callback('info', f"Embedding batch {i//batch_size + 1}/{(len(texts)-1)//batch_size + 1}...")
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=batch
        )
        embeddings.extend([e.embedding for e in response.data])
    
    X = np.array(embeddings, dtype="float32")
    
    if progress_callback:
        progress_callback('success', f"Created {X.shape[0]} embeddings with {X.shape[1]} dimensions")
        progress_callback('info', "Saving index...")
    print("Saving index…")

    np.save(os.path.join(IDX_DIR, "embeddings.npy"), X)
    with open(os.path.join(IDX_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False)
    
    config_file = "config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            config['similarity_threshold'] = 0.40
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            if progress_callback:
                progress_callback('info', "Updated similarity threshold to 0.40 for OpenAI embeddings")
        except Exception as e:
            print(f"Warning: Could not update config: {e}")
    
    final_msg = f"Index built successfully! {len(texts)} chunks indexed with OpenAI embeddings."
    print(final_msg)
    if progress_callback:
        progress_callback('complete', final_msg)
    return {"total_chunks": len(texts), "dimension": X.shape[1]}

if __name__ == "__main__":
    index_kb()
