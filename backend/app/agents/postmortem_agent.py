"""PostMortem Agent — campaign performance review & lesson extraction.

Phase 3: analyzes completed campaigns, compares expected vs actual metrics,
identifies what worked/didn't work, and extracts reusable playbook entries.

Architecture:
  Data Collection (MetricsService + AgentRunLog) → LLM Analysis → Playbook Extraction → RAG Store
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from app.agents.base import BaseAgent, AgentOutput, TaskProtocol
from app.services.metrics_service import MetricsService
from app.services.data_service import MockDataService
from app.services.knowledge_base import KnowledgeBase
from app.db.models import SessionLocal, AgentRunLog, Campaign

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "postmortem.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else "You are a postmortem agent."


class PostMortemAgent(BaseAgent):
    """Reviews completed campaigns and generates structured postmortem reports."""

    def __init__(self, model: str | None = None):
        super().__init__(
            name="postmortem_agent",
            system_prompt=SYSTEM_PROMPT,
            model=model,
            temperature=0.4,
        )
        self.metrics = MetricsService()
        self.ds = MockDataService()

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        campaign_id = task.input.get("campaign_id", "")
        district_id = task.input.get("district_id", 1)
        days = task.input.get("days", 7)

        # ── Collect data ──
        expected = task.input.get("expected_metrics", {})
        actual = self._collect_actual_metrics(district_id, days)
        agent_decisions = self._collect_agent_decisions(campaign_id)
        operator_feedback = task.input.get("operator_feedback", [])

        # Compare
        comparison = self._compare_metrics(expected, actual)

        # Enrich task for LLM
        enriched_input = {
            **task.input,
            "expected_metrics": expected,
            "actual_metrics": actual,
            "comparison": comparison,
            "agent_decisions": agent_decisions,
            "operator_feedback": operator_feedback,
        }
        enriched_task = TaskProtocol(
            task_id=task.task_id,
            workflow_id=task.workflow_id,
            agent_name=self.name,
            biz_goal=task.biz_goal,
            input=enriched_input,
            constraints=task.constraints,
        )

        llm_output = super().invoke(enriched_task)

        # ── Auto-extract playbook if LLM suggests ──
        playbook = {}
        if isinstance(llm_output.result, dict):
            playbook = llm_output.result.get("playbook_candidate", {})

        # ── Merge result ──
        merged_result = {
            "campaign_id": campaign_id,
            "district_id": district_id,
            "generated_at": datetime.utcnow().isoformat(),
            "comparison": comparison,
            "summary": llm_output.result.get("summary", "") if isinstance(llm_output.result, dict) else "",
            "goal_achievement": llm_output.result.get("goal_achievement", {}) if isinstance(llm_output.result, dict) else {},
            "analysis": llm_output.result.get("analysis", {}) if isinstance(llm_output.result, dict) else {},
            "lessons_learned": llm_output.result.get("lessons_learned", []) if isinstance(llm_output.result, dict) else [],
            "playbook_candidate": playbook,
        }

        return AgentOutput(
            result=merged_result,
            evidence=llm_output.evidence + [f"对比 {len(comparison)} 项指标"],
            confidence=llm_output.confidence,
            risk_level="low",
            recommended_action=llm_output.recommended_action or "查看复盘报告",
            need_human_review=True,
        )

    # ── Data Collection ──

    def _collect_actual_metrics(self, district_id: int, days: int) -> dict:
        """Collect actual performance metrics for the campaign period."""
        snapshot = self.metrics.get_dashboard_snapshot(district_id, days)
        return {
            "gmv": snapshot.get("gmv", 0),
            "aov": snapshot.get("aov", 0),
            "gross_margin": snapshot.get("gross_margin", 0),
            "sell_through_rate": snapshot.get("sell_through_rate", 0),
            "exposure_uv": snapshot.get("funnel", {}).get("exposure_uv", 0),
            "click_uv": snapshot.get("funnel", {}).get("click_uv", 0),
            "payment_uv": snapshot.get("funnel", {}).get("payment_uv", 0),
            "ctr": snapshot.get("funnel", {}).get("ctr", 0),
            "cvr": snapshot.get("funnel", {}).get("cvr", 0),
        }

    def _collect_agent_decisions(self, campaign_id: str) -> dict:
        """Collect agent run logs for this campaign."""
        db = SessionLocal()
        try:
            logs = db.query(AgentRunLog).filter(
                AgentRunLog.task_id.like(f"%{campaign_id}%")
            ).order_by(AgentRunLog.created_at).all()

            decisions = {}
            for log in logs:
                output = log.output or {}
                result = output.get("result", {})
                decisions[log.agent_name] = {
                    "run_id": log.run_id,
                    "summary": str(result)[:500] if result else "no output",
                    "confidence": output.get("confidence", 0),
                    "latency_ms": log.latency_ms,
                }
            return decisions
        finally:
            db.close()

    @staticmethod
    def _compare_metrics(expected: dict, actual: dict) -> dict:
        """Compare expected vs actual metrics."""
        comparison = {}
        for key in set(list(expected.keys()) + list(actual.keys())):
            exp_val = expected.get(key, 0)
            act_val = actual.get(key, 0)
            # Skip non-numeric values (e.g. "goal": "gmv")
            if not isinstance(exp_val, (int, float)) or not isinstance(act_val, (int, float)):
                continue
            if exp_val == 0 and act_val == 0:
                continue
            delta_pct = round((act_val - exp_val) / max(abs(exp_val), 1), 4) if exp_val != 0 else 0
            comparison[key] = {
                "expected": exp_val,
                "actual": act_val,
                "delta_pct": delta_pct,
                "status": "above" if delta_pct > 0.05 else "below" if delta_pct < -0.05 else "on_target",
            }
        return comparison

    # ── Playbook Extraction ──

    def save_playbook(self, report_result: dict, kb: KnowledgeBase | None = None) -> str | None:
        """Extract and save a playbook entry from the postmortem report."""
        playbook = report_result.get("playbook_candidate", {})
        if not playbook.get("should_save"):
            return None

        title = playbook.get("title", "未命名复盘")
        strategy = playbook.get("strategy_summary", "")
        if not strategy:
            return None

        # Save to DB
        db = SessionLocal()
        try:
            from app.db.models import PlaybookCase
            case = PlaybookCase(
                title=title,
                campaign_type=report_result.get("goal_achievement", {}).get("goal", ""),
                district_type="",
                strategy_summary=strategy,
                key_metrics=report_result.get("comparison", {}),
                tags=json.dumps(["自动提取", "复盘"]),
            )
            db.add(case)
            db.commit()
            db.refresh(case)
            doc_id = str(case.id)
        finally:
            db.close()

        # Also sync to RAG
        if kb is None:
            kb = KnowledgeBase()
        kb.ingest_document(
            title=title,
            content=strategy,
            doc_type="playbook",
            tags=["复盘", "自动提取"],
        )

        return doc_id

    def _parse_output(self, raw: str) -> AgentOutput:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            if "```json" in raw:
                data = json.loads(raw.split("```json")[1].split("```")[0].strip())
            elif "```" in raw:
                data = json.loads(raw.split("```")[1].split("```")[0].strip())
            else:
                return self._fallback_output("JSON parse failed")

        return AgentOutput(
            result=data.get("result", {}),
            evidence=data.get("evidence", []),
            confidence=data.get("confidence", 0.5),
            risk_level=data.get("risk_level", "low"),
            recommended_action=data.get("recommended_action", ""),
            need_human_review=data.get("need_human_review", True),
        )
