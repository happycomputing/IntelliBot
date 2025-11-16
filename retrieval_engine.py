import os, json
from typing import List, Dict, Tuple
import numpy as np
from openai import OpenAI

class RetrievalEngine:
    def __init__(self, index_dir="kb/index", similarity_threshold=0.45, top_k=4):
        self.index_dir = index_dir
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self._loaded = False
        self.embeddings = None
        self.meta = None
        self.client = None

    def load(self):
        """Load the embeddings and OpenAI client"""
        if self._loaded:
            return
        
        if not os.path.exists(os.path.join(self.index_dir, "embeddings.npy")):
            raise FileNotFoundError("No index found. Please crawl and index a website first.")
        
        self.embeddings = np.load(os.path.join(self.index_dir, "embeddings.npy"))
        with open(os.path.join(self.index_dir, "meta.json"), "r", encoding="utf-8") as f:
            self.meta = json.load(f)
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._loaded = True

    def cosine_similarity(self, query_vec, doc_vecs):
        """Calculate cosine similarity between query and document vectors"""
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        doc_norms = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-10)
        similarities = np.dot(doc_norms, query_norm)
        return similarities

    def search(self, query: str) -> List[Tuple[float, Dict]]:
        """Search for relevant chunks in the knowledge base"""
        self.load()
        
        response = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=[query]
        )
        query_vec = np.array(response.data[0].embedding, dtype="float32")
        
        similarities = self.cosine_similarity(query_vec, self.embeddings)
        
        top_indices = np.argsort(similarities)[::-1][:self.top_k]
        
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            results.append((score, self.meta[idx]))
        
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
            title = doc.get("title") or url
            parts.append(f"{title}\n{snippet}\n\nSource: {url}")
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
