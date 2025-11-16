#!/usr/bin/env python3
import os
import json
import glob
import math
import hashlib
import datetime
from typing import List, Dict, Any, Tuple, Optional, Callable
import numpy as np
from openai import OpenAI


ProgressCallback = Optional[Callable[[str, str], None]]


def _notify(callback: ProgressCallback, kind: str, message: str) -> None:
  if callback:
    callback(kind, message)


def _load_documents(raw_dir: str, progress_callback: ProgressCallback = None) -> List[Dict[str, Any]]:
  doc_paths = glob.glob(os.path.join(raw_dir, "*.json"))
  documents: List[Dict[str, Any]] = []
  _notify(progress_callback, "info", f"Loading {len(doc_paths)} knowledge documents...")
  for path in doc_paths:
    try:
      with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
      doc["__path"] = path
      documents.append(doc)
    except Exception:
      _notify(progress_callback, "warning", f"Skipping malformed document: {path}")
  return documents


def _paragraphs(text: str) -> List[str]:
  blocks = []
  current = []
  for line in text.splitlines():
    stripped = line.strip()
    if not stripped:
      if current:
        blocks.append(" ".join(current))
        current = []
      continue
    current.append(stripped)
  if current:
    blocks.append(" ".join(current))
  return blocks


def _chunk_document(
  doc: Dict[str, Any],
  target_size: int,
  overlap: int
) -> List[Dict[str, Any]]:
  paragraphs = _paragraphs(doc.get("text", ""))
  if not paragraphs:
    return []

  max_size = max(target_size, 300)
  overlap = min(overlap, max_size // 2)
  chunks = []
  buffer: List[str] = []
  current_len = 0

  for para in paragraphs:
    para_len = len(para)
    if current_len + para_len > max_size and buffer:
      chunk_text = " ".join(buffer).strip()
      if chunk_text:
        chunks.append(chunk_text)
      if overlap > 0 and buffer:
        overlap_text = " ".join(buffer)[-overlap:]
        buffer = [overlap_text, para]
        current_len = len(overlap_text) + para_len
      else:
        buffer = [para]
        current_len = para_len
      continue
    buffer.append(para)
    current_len += para_len

  final = " ".join(buffer).strip()
  if final:
    chunks.append(final)

  structured = []
  headings = doc.get("headings") or {}
  primary_heading = ""
  for level in ("h1", "h2", "h3"):
    items = headings.get(level) or []
    if items:
      primary_heading = items[0]
      break

  doc_hash = doc.get("content_hash") or hashlib.sha1(doc.get("text", "").encode("utf-8")).hexdigest()
  for idx, chunk_text in enumerate(chunks):
    chunk_hash = hashlib.sha1(f"{doc_hash}::{idx}::{chunk_text}".encode("utf-8")).hexdigest()
    structured.append({
      "doc_hash": doc_hash,
      "chunk_index": idx,
      "chunk_hash": chunk_hash,
      "text": chunk_text,
      "url": doc.get("url") or doc.get("label") or "",
      "title": doc.get("title") or primary_heading or doc.get("label") or "Untitled",
      "source_type": doc.get("source_type") or "unknown",
      "meta_description": doc.get("meta_description") or "",
      "headings": headings,
      "token_estimate": math.ceil(len(chunk_text) / 4),
      "extracted_at": doc.get("extracted_at"),
      "content_type": doc.get("content_type"),
      "status_code": doc.get("status_code", 200)
    })
  return structured


def _load_existing_index(index_dir: str) -> Tuple[List[Dict[str, Any]], np.ndarray]:
  meta_path = os.path.join(index_dir, "meta.json")
  embeddings_path = os.path.join(index_dir, "embeddings.npy")
  if not os.path.exists(meta_path) or not os.path.exists(embeddings_path):
    return [], np.empty((0,), dtype="float32")
  try:
    with open(meta_path, "r", encoding="utf-8") as handle:
      metadata = json.load(handle)
    embeddings = np.load(embeddings_path)
    return metadata, embeddings
  except Exception:
    return [], np.empty((0,), dtype="float32")


def _reuse_embeddings(metadata: List[Dict[str, Any]], embeddings: np.ndarray) -> Tuple[Dict[str, np.ndarray], int]:
  reused = {}
  count = 0
  if not metadata or embeddings.size == 0:
    return reused, count
  for idx, meta in enumerate(metadata):
    reused[meta["chunk_hash"]] = embeddings[idx]
    count += 1
  return reused, count


def index_kb(
  chunk_size: int = 900,
  chunk_overlap: int = 150,
  progress_callback: ProgressCallback = None,
  raw_dir: str = "kb/raw",
  index_dir: str = "kb/index",
  config_path: str = "config.json"
) -> Dict[str, Any]:
  """
  Build a vector index from knowledge documents using OpenAI embeddings with caching.
  """
  os.makedirs(index_dir, exist_ok=True)
  documents = _load_documents(raw_dir, progress_callback)
  if not documents:
    error_msg = "No documents found. Please crawl or upload knowledge sources first."
    _notify(progress_callback, "error", error_msg)
    raise ValueError(error_msg)

  cache_meta, cache_embeddings = _load_existing_index(index_dir)
  embedding_cache, cached_count = _reuse_embeddings(cache_meta, cache_embeddings)

  _notify(progress_callback, "info", f"Loaded {cached_count} cached embeddings")

  all_chunks: List[Dict[str, Any]] = []
  new_chunks: List[Dict[str, Any]] = []
  cached_hits = 0

  for doc in documents:
    chunks = _chunk_document(doc, chunk_size, chunk_overlap)
    if not chunks:
      continue
    for chunk in chunks:
      all_chunks.append(chunk)
      if chunk["chunk_hash"] in embedding_cache:
        cached_hits += 1
        chunk["_embedding"] = embedding_cache[chunk["chunk_hash"]]
      else:
        new_chunks.append(chunk)

  _notify(progress_callback, "info", f"{len(all_chunks)} chunks prepared ({cached_hits} reused)")

  texts_to_embed = [chunk["text"] for chunk in new_chunks]
  api_key = os.environ.get("OPENAI_API_KEY")
  if not api_key:
    raise RuntimeError("OPENAI_API_KEY not configured")
  client = OpenAI(api_key=api_key)

  embeddings = []
  batch_size = 64
  for start in range(0, len(texts_to_embed), batch_size):
    batch = texts_to_embed[start:start + batch_size]
    if not batch:
      continue
    batch_index = start // batch_size + 1
    total_batches = (len(texts_to_embed) - 1) // batch_size + 1
    _notify(progress_callback, "info", f"Embedding batch {batch_index}/{total_batches}...")
    response = client.embeddings.create(model="text-embedding-3-small", input=batch)
    vectors = [np.array(item.embedding, dtype="float32") for item in response.data]
    embeddings.extend(vectors)

  for chunk, vector in zip(new_chunks, embeddings):
    chunk["_embedding"] = vector

  vector_list = []
  metadata = []
  for chunk in all_chunks:
    vector = chunk.get("_embedding")
    if vector is None:
      continue
    vector_list.append(vector)
    metadata.append({
      "chunk_hash": chunk["chunk_hash"],
      "doc_hash": chunk["doc_hash"],
      "text": chunk["text"],
      "url": chunk["url"],
      "title": chunk["title"],
      "source_type": chunk["source_type"],
      "meta_description": chunk["meta_description"],
      "headings": chunk["headings"],
      "token_estimate": chunk["token_estimate"],
      "extracted_at": chunk["extracted_at"],
      "content_type": chunk["content_type"],
      "status_code": chunk["status_code"]
    })

  if not vector_list:
    raise RuntimeError("No embeddings generated")

  matrix = np.stack(vector_list).astype("float32")
  np.save(os.path.join(index_dir, "embeddings.npy"), matrix)
  with open(os.path.join(index_dir, "meta.json"), "w", encoding="utf-8") as handle:
    json.dump(metadata, handle, ensure_ascii=False, indent=2)

  if config_path and os.path.exists(config_path):
    try:
      with open(config_path, "r", encoding="utf-8") as config_file:
        config = json.load(config_file)
      config["similarity_threshold"] = 0.40
      with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)
      _notify(progress_callback, "info", "Updated similarity threshold to 0.40 for OpenAI embeddings")
    except Exception as exc:
      _notify(progress_callback, "warning", f"Could not update config: {exc}")

  reused_embeddings = cached_hits
  new_embeddings = len(embeddings)
  total_chunks = len(metadata)

  summary = {
    "total_chunks": total_chunks,
    "dimension": int(matrix.shape[1]),
    "new_embeddings": new_embeddings,
    "reused_embeddings": reused_embeddings,
    "last_indexed_at": datetime.datetime.utcnow().isoformat() + "Z"
  }
  stats_path = os.path.join(index_dir, "stats.json")
  try:
    with open(stats_path, "w", encoding="utf-8") as stats_file:
      json.dump(summary, stats_file, indent=2)
  except Exception:
    pass
  _notify(progress_callback, "complete", f"Index built successfully ({total_chunks} chunks, {new_embeddings} new embeddings)!")
  return summary


if __name__ == "__main__":
  index_kb()
