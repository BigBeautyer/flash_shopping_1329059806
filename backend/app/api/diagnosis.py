"""Diagnosis API — trigger diagnosis, view reports, real-time anomaly monitoring."""

import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.diagnosis_agent import DiagnosisAgent
from app.agents.base import TaskProtocol
from app.services.anomaly_detector import AnomalyDetector, DiagnosisReport
from app.services.funnel_attributor import FunnelAttributor
from app.services.data_service import MockDataService

router = APIRouter(prefix="/api/diagnosis", tags=["diagnosis"])

# Singleton
diagnosis_agent = DiagnosisAgent()
anomaly_detector = AnomalyDetector()
attributor = FunnelAttributor()
data_service = MockDataService()

# In-memory report store (Phase 3: move to DB)
_reports: dict[str, dict] = {}


# ── Request Models ──

class RunDiagnosisRequest(BaseModel):
    district_id: int = Field(default=1, ge=1, le=5)
    days: int = Field(default=30, ge=7, le=90)
    use_llm: bool = Field(default=True, description="Use LLM for root cause analysis")


# ── Endpoints ──

@router.post("/run")
def run_diagnosis(req: RunDiagnosisRequest):
    """Trigger a full diagnosis for a district.

    Returns: diagnosis report with anomalies, attribution, root causes, suggestions.
    """
    report_id = f"DIAG_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    if req.use_llm:
        # Full pipeline: anomaly → attribution → LLM
        task = TaskProtocol(
            task_id=f"diag_{report_id}",
            workflow_id=f"wf_diag_{report_id}",
            agent_name="diagnosis_agent",
            biz_goal="运营诊断",
            input={
                "district_id": req.district_id,
                "days": req.days,
            },
            constraints={},
        )
        output = diagnosis_agent.invoke(task)
        result = output.to_dict()
        result["report_id"] = report_id
    else:
        # Rule-engine only (fast, no LLM cost)
        anomalies, _ = anomaly_detector.detect(req.district_id, req.days, recent_window=3)
        attribution_result = attributor.attribute(anomalies)
        district_profile = data_service.get_district_profile(req.district_id)

        result = {
            "report_id": report_id,
            "district_id": req.district_id,
            "district_name": district_profile.get("name", f"商圈{req.district_id}"),
            "generated_at": datetime.utcnow().isoformat(),
            "period_days": req.days,
            "anomalies": [a.__dict__ for a in anomalies],
            "attribution": attribution_result,
            "result": {
                "diagnosis_summary": f"规则引擎诊断：发现 {len(anomalies)} 个异常，整体严重度 {attribution_result.get('overall_severity', 'info')}",
                "root_causes": attribution_result.get("attributions", []),
                "suggestions": [],
                "risk_assessment": f"共 {len(anomalies)} 个异常指标需要关注",
            },
            "evidence": [f"异动检测引擎: {len(anomalies)} 个异常", f"漏斗归因引擎: {len(attribution_result.get('attributions', []))} 条归因"],
            "confidence": min(1.0, len(anomalies) * 0.1) if anomalies else 0.5,
            "risk_level": attribution_result.get("overall_severity", "info"),
            "recommended_action": "请查看诊断报告",
            "need_human_review": True,
        }

    # Store report
    _reports[report_id] = result

    return result


@router.get("/anomalies")
def get_anomalies(
    district_id: int = Query(default=1, ge=1, le=5),
    days: int = Query(default=7, ge=1, le=30),
):
    """Quick rule-engine anomaly detection — suitable for real-time dashboard polling."""
    anomalies, trend_df = anomaly_detector.detect(district_id, days, recent_window=2)
    attribution_result = attributor.attribute(anomalies)

    return {
        "district_id": district_id,
        "period_days": days,
        "generated_at": datetime.utcnow().isoformat(),
        "anomalies": [a.__dict__ for a in anomalies],
        "attribution": attribution_result,
        "risk_level": attribution_result.get("overall_severity", "info"),
        "confidence": min(1.0, len(anomalies) * 0.1) if anomalies else 0.5,
    }


@router.get("/reports")
def list_reports(limit: int = Query(default=20, ge=1, le=100)):
    """List recent diagnosis reports."""
    reports = list(_reports.values())
    reports.sort(key=lambda r: r.get("generated_at", ""), reverse=True)
    reports = reports[:limit]

    return {
        "total": len(_reports),
        "reports": [
            {
                "report_id": r.get("report_id"),
                "district_id": r.get("district_id"),
                "generated_at": r.get("generated_at"),
                "risk_level": r.get("risk_level", "info"),
                "anomaly_count": len(r.get("anomalies", [])),
                "summary": (
                    r.get("result", {}).get("diagnosis_summary", "")
                    if isinstance(r.get("result"), dict) else ""
                ),
            }
            for r in reports
        ],
    }


@router.get("/reports/{report_id}")
def get_report(report_id: str):
    """Get a specific diagnosis report by ID."""
    report = _reports.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/districts")
def list_diagnosis_districts():
    """List all districts available for diagnosis."""
    districts_df = data_service.get_all_districts()
    if districts_df.empty:
        return []
    return districts_df.to_dict(orient="records")
