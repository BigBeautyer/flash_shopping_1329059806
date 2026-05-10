"""Application configuration loaded from environment variables."""

import os
from pydantic_settings import BaseSettings
from pathlib import Path

# Vercel serverless: read-only filesystem, only /tmp is writable
_IS_VERCEL = bool(os.environ.get("VERCEL"))

# Local project root (used for non-I/O path resolution)
_PROJECT_ROOT = Path(__file__).parent.parent

# Writable data directory: /tmp on Vercel, backend/app/data/ locally
if _IS_VERCEL:
    _DATA_DIR = Path("/tmp/data")
else:
    _DATA_DIR = _PROJECT_ROOT / "data"


class Settings(BaseSettings):
    # Project paths
    PROJECT_ROOT: Path = _PROJECT_ROOT
    DATA_DIR: Path = _DATA_DIR

    # Database (SQLite must live on writable filesystem)
    SQLITE_PATH: str = str(_DATA_DIR / "flash_sale.db")
    CHROMA_PATH: str = str(_DATA_DIR / "chroma")

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM — supports OpenAI and DeepSeek (OpenAI-compatible)
    LLM_PROVIDER: str = "deepseek"              # "openai" or "deepseek"
    LLM_API_KEY: str = ""                       # set via env: LLM_API_KEY=sk-xxx
    LLM_API_BASE: str = "https://api.deepseek.com"  # DeepSeek endpoint
    LLM_MODEL: str = "deepseek-chat"            # deepseek-chat (V3) or deepseek-reasoner (R1)
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096
    LLM_TIMEOUT: int = 120  # seconds for LLM API requests

    # Agent settings
    CONFIDENCE_THRESHOLD: float = 0.7
    MAX_RETRY_ON_PARSE_ERROR: int = 2

    # Mock data settings
    NUM_DISTRICTS: int = 5
    NUM_PRODUCTS: int = 200
    NUM_USERS: int = 10000
    NUM_MONTHS: int = 3
    NOISE_RATE: float = 0.05
    ANOMALY_RATE: float = 0.02

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
