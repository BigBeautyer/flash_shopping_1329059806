"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.db.models import init_db
from app.core.config import settings
from app.api.dashboard import router as dashboard_router
from app.api.workflow import router as workflow_router
from app.api.diagnosis import router as diagnosis_router
from app.api.rag import router as rag_router
from app.api.phase3 import router as phase3_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB. Shutdown: cleanup."""
    init_db()
    yield


app = FastAPI(
    title="Flash Sale AI Platform",
    description="AI闪购运营中台 — Multi-Agent 决策工作流平台",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(dashboard_router)
app.include_router(workflow_router)
app.include_router(diagnosis_router)
app.include_router(rag_router)
app.include_router(phase3_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/config")
def get_config():
    return {
        "districts": settings.NUM_DISTRICTS,
        "products": settings.NUM_PRODUCTS,
        "users": settings.NUM_USERS,
        "months": settings.NUM_MONTHS,
        "llm_model": settings.LLM_MODEL,
    }
