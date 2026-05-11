"""宏观知识库 - ChromaDB 向量存储"""

from pathlib import Path
from datetime import datetime

from loguru import logger

DATA_DIR = Path("C:/Users/Administrator/quant-terminal/data")
CHROMA_DIR = DATA_DIR / "chromadb"


def _get_collection():
    """获取 ChromaDB 集合（懒加载）"""
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name="macro_knowledge",
        metadata={"hnsw:space": "cosine"},
    )


def add_knowledge(
    text: str,
    source: str = "manual",
    category: str = "general",
    metadata: dict | None = None,
) -> str:
    """添加知识到向量库"""
    collection = _get_collection()
    doc_id = f"{source}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    meta = {
        "source": source,
        "category": category,
        "created_at": datetime.now().isoformat(),
        **(metadata or {}),
    }
    collection.add(documents=[text], ids=[doc_id], metadatas=[meta])
    logger.info(f"Added knowledge: {doc_id} ({len(text)} chars)")

    # 记录到 DuckDB
    from ..data.store import get_store
    store = get_store()
    store.conn.execute(
        "INSERT INTO macro_knowledge_log (doc_id, source, category, text_length) VALUES (?, ?, ?, ?)",
        [doc_id, source, category, len(text)],
    )
    return doc_id


def search_knowledge(query: str, n_results: int = 5, category: str | None = None) -> list[dict]:
    """搜索相关知识"""
    collection = _get_collection()
    where = {"category": category} if category else None
    results = collection.query(query_texts=[query], n_results=n_results, where=where)

    docs = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        distance = results["distances"][0][i] if results["distances"] else 0
        docs.append({
            "text": doc,
            "source": meta.get("source", ""),
            "category": meta.get("category", ""),
            "relevance": round(1 - distance, 3),
        })
    return docs


def get_knowledge_stats() -> dict:
    """获取知识库统计"""
    collection = _get_collection()
    return {"total_documents": collection.count()}
