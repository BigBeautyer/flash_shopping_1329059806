"""Base agent class, unified output spec (AgentOutput), and task protocol (TaskProtocol)."""

from __future__ import annotations
import json
import time
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Unified Output Spec (ADR-007)
# ═══════════════════════════════════════════════

class AgentOutput(BaseModel):
    """Every agent MUST return this structure."""
    result: Any = Field(description="Core output (type varies per agent)")
    evidence: List[str] = Field(default_factory=list, description="Decision evidence: data sources, rules cited")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence score 0-1")
    risk_level: str = Field(default="low", description="low / medium / high")
    recommended_action: str = Field(default="", description="Suggested action for operator")
    need_human_review: bool = Field(default=True, description="Whether human review is required")

    def to_dict(self) -> dict:
        return self.model_dump()


# ═══════════════════════════════════════════════
# Standardized Task Protocol (ADR-005)
# ═══════════════════════════════════════════════

class TaskProtocol(BaseModel):
    """Unified communication protocol between agents."""
    task_id: str
    workflow_id: str
    agent_name: str
    biz_goal: str = ""
    input: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    output_schema: List[str] = Field(default_factory=list)
    confidence_threshold: float = settings.CONFIDENCE_THRESHOLD
    human_review_required: bool = True
    parent_task_id: Optional[str] = None  # for chaining upstream context

    def to_dict(self) -> dict:
        return self.model_dump()


# ═══════════════════════════════════════════════
# Base Agent
# ═══════════════════════════════════════════════

class BaseAgent(ABC):
    """All agents inherit from this base.

    Provides:
    - Unified LLM initialization
    - Prompt template loading
    - Structured output parsing with retry
    - Run logging (AgentRunLog)
    - Streaming support
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: Optional[List[Callable]] = None,
        model: str | None = None,
        temperature: float | None = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or []

        # Use custom API base for DeepSeek (or any OpenAI-compatible provider)
        llm_kwargs = {
            "api_key": settings.LLM_API_KEY,
            "model": model or settings.LLM_MODEL,
            "temperature": temperature if temperature is not None else settings.LLM_TEMPERATURE,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "request_timeout": settings.LLM_TIMEOUT,
        }
        if settings.LLM_PROVIDER != "openai":
            llm_kwargs["base_url"] = settings.LLM_API_BASE

        self.llm = ChatOpenAI(**llm_kwargs)
        if self.tools:
            self.llm = self.llm.bind_tools(self.tools)

    def _build_messages(self, task: TaskProtocol) -> list:
        """Build message list from task protocol."""
        system = SystemMessage(content=self.system_prompt)
        human = HumanMessage(content=json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
        return [system, human]

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        """Synchronous invocation with retry on parse error."""
        start = time.time()
        messages = self._build_messages(task)

        for attempt in range(settings.MAX_RETRY_ON_PARSE_ERROR):
            try:
                response = self.llm.invoke(messages)
                output = self._parse_output(response.content)
                self._log_run(task.task_id, messages, output, latency_ms=(time.time() - start) * 1000)
                return output
            except Exception as e:
                logger.warning(f"[{self.name}] Parse attempt {attempt + 1} failed: {e}")
                if attempt == settings.MAX_RETRY_ON_PARSE_ERROR - 1:
                    return self._fallback_output(str(e))

        return self._fallback_output("max retries exceeded")

    def stream(self, task: TaskProtocol):
        """Streaming invocation — yields partial output chunks."""
        messages = self._build_messages(task)
        for chunk in self.llm.stream(messages):
            yield chunk.content

    @abstractmethod
    def _parse_output(self, raw: str) -> AgentOutput:
        """Parse LLM raw output into AgentOutput. Each agent implements its own."""
        ...

    def _fallback_output(self, error_msg: str) -> AgentOutput:
        """Return a safe fallback when parsing fails."""
        return AgentOutput(
            result=None,
            evidence=[f"Parse error: {error_msg}"],
            confidence=0.0,
            risk_level="high",
            recommended_action="人工审核 — Agent 输出解析失败，请手动处理",
            need_human_review=True,
        )

    def _log_run(self, task_id: str, messages: list, output: AgentOutput, latency_ms: float):
        """Log agent run to database (non-blocking, best-effort)."""
        try:
            from app.db.models import AgentRunLog, SessionLocal
            db = SessionLocal()
            log = AgentRunLog(
                run_id=f"{self.name}_{task_id}_{int(time.time())}",
                agent_name=self.name,
                task_id=task_id,
                input_protocol={"messages": [str(m) for m in messages]},
                output=output.to_dict(),
                tokens_used=0,  # approximate; real count needs tiktoken
                latency_ms=int(latency_ms),
            )
            db.add(log)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"[{self.name}] Failed to log run: {e}")
