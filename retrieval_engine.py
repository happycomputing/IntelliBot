import os, json
from typing import List, Dict, Tuple
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

IDX_DIR = "kb/index"

class RetrievalEngine:
    def __init__(self, similarity_threshold=0.52, top_k=4):
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self._loaded = False
        self.index = None
        self.meta = None
        self.model = None

    def load(self):
        """Load the FAISS index and embedding model"""
        if self._loaded:
            return
        
        if not os.path.exists(os.path.join(IDX_DIR, "faiss.index")):
            raise FileNotFoundError("No index found. Please crawl and index a website first.")
        
        self.index = faiss.read_index(os.path.join(IDX_DIR, "faiss.index"))
        with open(os.path.join(IDX_DIR, "meta.json"), "r", encoding="utf-8") as f:
            self.meta = json.load(f)
        self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        self._loaded = True

    def search(self, query: str) -> List[Tuple[float, Dict]]:
        """Search for relevant chunks in the knowledge base"""
        self.load()
        q = self.model.encode([query], convert_to_numpy=True, normalize_embeddings=True).astype("float32")
        sims, idxs = self.index.search(q, self.top_k)
        results = []
        for s, i in zip(sims[0].tolist(), idxs[0].tolist()):
            if i == -1:
                continue
            results.append((float(s), self.meta[i]))
        return results

    def format_answer(self, hits: List[Tuple[float, Dict]]) -> str:
        """Format search results into a coherent answer with sources"""
        parts = []
        used = set()
        for score, doc in hits:
            if score < self.similarity_threshold:
                continue
            url = doc["url"]
            if url in used:
                continue
            used.add(url)
            snippet = doc["text"].strip()
            snippet = (snippet[:600] + "…") if len(snippet) > 600 else snippet
            parts.append(f"{snippet}\n\nSource: {url}")
            if len(parts) >= 2:
                break
        return "\n\n---\n\n".join(parts)

    def get_answer(self, query: str) -> Dict:
        """Get grounded answer for a query"""
        if not query.strip():
            return {
                "answer": "Please ask a question.",
                "sources": [],
                "confidence": 0.0
            }
        
        try:
            hits = self.search(query)
            answer = self.format_answer(hits)
            
            if not answer:
                return {
                    "answer": "I only answer using information from the indexed website. I couldn't find anything relevant for your question.",
                    "sources": [],
                    "confidence": 0.0
                }
            
            max_score = max([h[0] for h in hits]) if hits else 0.0
            sources = [{"url": h[1]["url"], "score": h[0]} for h in hits if h[0] >= self.similarity_threshold][:2]
            
            return {
                "answer": answer,
                "sources": sources,
                "confidence": max_score
            }
        except Exception as e:
            return {
                "answer": f"Error retrieving answer: {str(e)}",
                "sources": [],
                "confidence": 0.0
            }

    def get_stats(self) -> Dict:
        """Get knowledge base statistics"""
        try:
            self.load()
            return {
                "total_chunks": len(self.meta),
                "indexed": True,
                "similarity_threshold": self.similarity_threshold,
                "top_k": self.top_k
            }
        except:
            return {
                "total_chunks": 0,
                "indexed": False,
                "similarity_threshold": self.similarity_threshold,
                "top_k": self.top_k
            }
