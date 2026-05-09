"""Diagnosis Agent — anomaly detection + funnel attribution + LLM root cause analysis.

Architecture:
  Rule Engine → Funnel Attribution → LLM Explanation & Suggestions
  (AnomalyDetector)  (FunnelAttributor)  (DiagnosisAgent LLM)

Phase 2: full-depth — multi-method detection, per-metric config, severity grading.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from app.agents.base import BaseAgent, AgentOutput, TaskProtocol
from app.services.anomaly_detector import AnomalyDetector, DiagnosisReport
from app.services.funnel_attributor import FunnelAttributor
from app.services.metrics_service import MetricsService
from app.services.data_service import MockDataService

logger = logging.getLogger(__name__)

# Load system prompt
_prompt_path = Path(__file__).parent / "prompts" / "diagnosis.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8") if _prompt_path.exists() else "You are a diagnosis agent."


class DiagnosisAgent(BaseAgent):
    """Diagnosis Agent: rule engine layer + LLM reasoning layer.

    Rule engine handles anomaly detection and funnel attribution.
    LLM handles natural language explanation and suggestion generation.
    """

    def __init__(self, model: str | None = None):
        super().__init__(
            name="diagnosis_agent",
            system_prompt=SYSTEM_PROMPT,
            model=model,
            temperature=0.3,
        )
        self.detector = AnomalyDetector()
        self.attributor = FunnelAttributor()
        self.metrics = MetricsService()
        self.ds = MockDataService()

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        """Full diagnosis: detect → attribute → LLM explain.

        Task input should contain:
        - district_id: int
        - days: int (default 30)
        """
        district_id = task.input.get("district_id", 1)
        days = task.input.get("days", 30)

        # ── Layer 1: Rule Engine — anomaly detection ──
        logger.info(f"[diagnosis_agent] Detecting anomalies for district {district_id}, {days}d")
        anomalies, trend_df = self.detector.detect(district_id, days, recent_window=3)

        # ── Layer 2: Rule Engine — funnel attribution ──
        attribution_result = self.attributor.attribute(anomalies)

        # ── Layer 3: Build enriched context for LLM ──
        district_profile = self.ds.get_district_profile(district_id)
        district_name = district_profile.get("name", f"商圈{district_id}")

        # Recent trend summary for LLM context
        trend_summary = self._build_trend_summary(district_id, days)

        # Enrich task input with rule-engine results
        enriched_input = {
            **task.input,
            "district_name": district_name,
            "district_profile": district_profile,
            "anomalies": [a.__dict__ for a in anomalies][:20],  # cap for LLM context
            "attribution": attribution_result,
            "recent_trend_summary": trend_summary,
        }
        enriched_task = TaskProtocol(
            task_id=task.task_id,
            workflow_id=task.workflow_id,
            agent_name=self.name,
            biz_goal=task.biz_goal,
            input=enriched_input,
            constraints=task.constraints,
        )

        # ── Layer 4: LLM reasoning → natural-language diagnosis ──
        logger.info(f"[diagnosis_agent] LLM reasoning for district {district_id}")
        llm_output = super().invoke(enriched_task)

        # ── Merge rule-engine + LLM output ──
        merged_result = {
            "district_id": district_id,
            "district_name": district_name,
            "detected_at": datetime.utcnow().isoformat(),
            "period_days": days,
            "anomaly_summary": {
                "total_anomalies": len(anomalies),
                "by_severity": attribution_result.get("layer_summary", {}),
                "overall_severity": attribution_result.get("overall_severity", "info"),
            },
            "anomalies": [a.__dict__ for a in anomalies],
            "attribution": attribution_result.get("attributions", []),
            "trend_summary": trend_summary,
            # LLM-generated fields (fallback to rule-engine if LLM fails)
            "diagnosis_summary": (
                llm_output.result.get("diagnosis_summary", "")
                if isinstance(llm_output.result, dict) else ""
            ),
            "root_causes": (
                llm_output.result.get("root_causes", [])
                if isinstance(llm_output.result, dict) else []
            ),
            "suggestions": (
                llm_output.result.get("suggestions", [])
                if isinstance(llm_output.result, dict) else []
            ),
            "risk_assessment": (
                llm_output.result.get("risk_assessment", "")
                if isinstance(llm_output.result, dict) else ""
            ),
        }

        # Confidence: blend rule-engine confidence (1.0 for detected, 0 for none) with LLM confidence
        rule_confidence = min(1.0, len(anomalies) * 0.1) if anomalies else 0.5
        blended_confidence = round((rule_confidence + llm_output.confidence) / 2, 2)

        return AgentOutput(
            result=merged_result,
            evidence=llm_output.evidence + [
                f"异动检测引擎: 发现 {len(anomalies)} 个异常",
                f"漏斗归因引擎: 匹配 {len(attribution_result.get('attributions', []))} 条归因规则",
            ],
            confidence=blended_confidence,
            risk_level=attribution_result.get("overall_severity", llm_output.risk_level),
            recommended_action=llm_output.recommended_action or "请查看诊断报告并人工确认",
            need_human_review=True,
        )

    def diagnose_quick(self, district_id: int, days: int = 7) -> dict:
        """Quick diagnosis without LLM — rule-engine only. For real-time dashboards."""
        anomalies, _ = self.detector.detect(district_id, days, recent_window=1)
        attribution = self.attributor.attribute(anomalies)
        return {
            "district_id": district_id,
            "anomalies": [a.__dict__ for a in anomalies],
            "attribution": attribution,
            "generated_at": datetime.utcnow().isoformat(),
        }

    # ── Helpers ──

    def _build_trend_summary(self, district_id: int, days: int) -> dict:
        """Build a compact trend summary for LLM context."""
        trend = self.metrics.get_daily_trend(district_id, days)
        if not trend:
            return {"message": "无趋势数据"}

        latest = trend[-1] if trend else {}
        # Average of last 3 days vs average of prior period
        recent = trend[-3:] if len(trend) >= 3 else trend
        prior = trend[-7:-3] if len(trend) >= 7 else trend[:max(1, len(trend) // 2)]

        def avg(lst, key):
            vals = [d.get(key, 0) for d in lst if d.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else 0

        return {
            "latest": latest,
            "recent_3d_avg": {
                "gmv": avg(recent, "gmv"),
                "exposure_uv": avg(recent, "exposure_uv"),
                "click_uv": avg(recent, "click_uv"),
                "cart_uv": avg(recent, "cart_uv"),
                "payment_uv": avg(recent, "payment_uv"),
            },
            "prior_period_avg": {
                "gmv": avg(prior, "gmv"),
                "payment_uv": avg(prior, "payment_uv"),
            },
        }

    def _parse_output(self, raw: str) -> AgentOutput:
        """Parse LLM JSON output into AgentOutput."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```json" in raw:
                block = raw.split("```json")[1].split("```")[0].strip()
                data = json.loads(block)
            elif "```" in raw:
                block = raw.split("```")[1].split("```")[0].strip()
                data = json.loads(block)
            else:
                raise

        return AgentOutput(
            result=data.get("result", {}),
            evidence=data.get("evidence", []),
            confidence=data.get("confidence", 0.5),
            risk_level=data.get("risk_level", "medium"),
            recommended_action=data.get("recommended_action", ""),
            need_human_review=data.get("need_human_review", True),
        )
