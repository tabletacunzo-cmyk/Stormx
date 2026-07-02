import os, hashlib, time, threading
from typing import List, Dict, Optional

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

RAG_DIR = os.path.join(os.path.dirname(__file__), "rag_data")
_engines: Dict[str, "RAGEngine"] = {}
_lock = threading.Lock()


class RAGEngine:
    def __init__(self, project: str):
        self.project = project
        self.persist_dir = os.path.join(RAG_DIR, project, "chroma")
        os.makedirs(self.persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection_name = "docs"
        self._init_collection()

    def _init_collection(self):
        try:
            self.collection = self.client.get_collection(self.collection_name)
        except (NotFoundError, ValueError):
            self.collection = self.client.create_collection(self.collection_name)

    def ingest(
        self,
        content: str,
        filename: str = "untitled.txt",
        source: str = "manual",
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> dict:
        doc_id = hashlib.md5(content.encode()).hexdigest()[:12]
        existing = self.collection.get(where={"doc_id": doc_id})
        if existing and len(existing["ids"]) > 0:
            return {"status": "ok", "doc_id": doc_id, "note": "Già presente"}

        chunks = self._chunk_text(content, chunk_size, overlap)
        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "doc_id": doc_id,
                "filename": filename,
                "source": source,
                "idx": i,
                "created": time.time(),
            }
            for i in range(len(chunks))
        ]
        self.collection.add(documents=chunks, ids=ids, metadatas=metadatas)
        return {"status": "ok", "doc_id": doc_id, "chunks": len(chunks)}

    def query(self, query: str, top_k: int = 5) -> List[dict]:
        results = self.collection.query(query_texts=[query], n_results=top_k)
        formatted: List[dict] = []
        if results["ids"] and len(results["ids"][0]) > 0:
            for i in range(len(results["ids"][0])):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 0.0
                formatted.append({
                    "chunk_id": results["ids"][0][i],
                    "doc_id": meta.get("doc_id", ""),
                    "content": results["documents"][0][i],
                    "score": round(1.0 - distance, 4),
                })
        return formatted

    def query_context(self, query: str, top_k: int = 5) -> str:
        results = self.query(query, top_k)
        if not results:
            return ""
        return "\n---\n".join(r["content"] for r in results)

    def list_documents(self) -> List[dict]:
        all_data = self.collection.get()
        seen: dict = {}
        metadatas = all_data.get("metadatas") or []
        for i, meta in enumerate(metadatas):
            doc_id = meta.get("doc_id", "")
            if doc_id and doc_id not in seen:
                seen[doc_id] = {
                    "id": doc_id,
                    "filename": meta.get("filename", "unknown"),
                    "source": meta.get("source", ""),
                    "created": meta.get("created", 0),
                    "chunks": sum(1 for m in metadatas if m.get("doc_id") == doc_id),
                }
        return list(seen.values())

    def delete_document(self, doc_id: str):
        all_data = self.collection.get()
        ids_to_delete = [
            all_data["ids"][i]
            for i, meta in enumerate(all_data.get("metadatas") or [])
            if meta.get("doc_id") == doc_id
        ]
        if ids_to_delete:
            self.collection.delete(ids=ids_to_delete)

    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        words = text.split()
        chunks: List[str] = []
        i = 0
        while i < len(words):
            chunk = " ".join(words[i : i + chunk_size])
            chunks.append(chunk)
            i += chunk_size - overlap
            if i >= len(words):
                break
        return chunks


# ---- Module-level convenience API (thread-safe) ----


def _engine(project: str) -> RAGEngine:
    with _lock:
        if project not in _engines:
            _engines[project] = RAGEngine(project)
        return _engines[project]


def ingest(project: str, content: str, filename: str = "untitled.txt", source: str = "manual", chunk_size: int = 500, overlap: int = 50) -> dict:
    return _engine(project).ingest(content, filename, source, chunk_size, overlap)


def query(project: str, query_text: str, top_k: int = 5) -> List[dict]:
    return _engine(project).query(query_text, top_k)


def query_context(project: str, query_text: str, top_k: int = 5) -> str:
    return _engine(project).query_context(query_text, top_k)


def list_documents(project: str) -> List[dict]:
    return _engine(project).list_documents()


def delete_document(project: str, doc_id: str):
    _engine(project).delete_document(doc_id)
