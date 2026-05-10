"""RAG API — knowledge base management and retrieval."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services.knowledge_base import KnowledgeBase, seed_knowledge_base

router = APIRouter(prefix="/api/rag", tags=["rag"])

# Lazy singleton — don't touch ChromaDB at import time (crashes on Vercel read-only FS)
_kb = None


def _get_kb():
    global _kb
    if _kb is None:
        _kb = seed_knowledge_base()
    return _kb


class IngestRequest(BaseModel):
    title: str
    content: str
    doc_type: str = Field(default="playbook", description="playbook / strategy / anomaly_fix / campaign_result")
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    doc_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


# ── Endpoints ──

@router.get("/stats")
def get_stats():
    """Get knowledge base statistics."""
    return _get_kb().get_stats()


@router.get("/search")
def search_docs(
    q: str = Query(..., description="Search query"),
    doc_type: str | None = Query(default=None, description="Filter by document type"),
    top_k: int = Query(default=5, ge=1, le=20),
):
    """Search the knowledge base."""
    results = _get_kb().retrieve(q, doc_type=doc_type, top_k=top_k)
    return {
        "query": q,
        "doc_type": doc_type,
        "results": results,
        "total_found": len(results),
    }


@router.post("/search")
def search_docs_post(req: SearchRequest):
    """Search the knowledge base (POST)."""
    results = _get_kb().retrieve(req.query, doc_type=req.doc_type, top_k=req.top_k)
    return {
        "query": req.query,
        "doc_type": req.doc_type,
        "results": results,
        "total_found": len(results),
    }


@router.post("/ingest")
def ingest_document(req: IngestRequest):
    """Ingest a single document into the knowledge base."""
    doc_id = _get_kb().ingest_document(
        title=req.title,
        content=req.content,
        doc_type=req.doc_type,
        tags=req.tags,
        metadata=req.metadata,
    )
    return {"doc_id": doc_id, "status": "ingested"}


@router.post("/seed")
def seed_kb():
    """Re-seed the knowledge base with default playbooks."""
    seed_knowledge_base(_get_kb())
    return {"status": "seeded", "stats": _get_kb().get_stats()}
