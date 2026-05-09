"""Phase 3 API — PostMortem, AB Experiment, and Agent Replay endpoints."""

import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.postmortem_agent import PostMortemAgent
from app.agents.base import TaskProtocol
from app.services.ab_service import ABService
from app.services.data_service import MockDataService
from app.services.knowledge_base import KnowledgeBase
from app.db.models import SessionLocal, AgentRunLog, PlaybookCase, ABExperiment

router = APIRouter(prefix="/api/phase3", tags=["phase3"])

# Singletons
postmortem_agent = PostMortemAgent()
ab_service = ABService()
data_service = MockDataService()


# ═══════════════════════════════════════════════
# Request Models
# ═══════════════════════════════════════════════

class PostMortemRequest(BaseModel):
    campaign_id: str = Field(description="Campaign ID to review")
    district_id: int = Field(default=1, ge=1, le=5)
    days: int = Field(default=7, ge=1, le=90)
    expected_metrics: dict = Field(default_factory=dict, description="Expected GMV, CVR, ROI, etc.")
    operator_feedback: list = Field(default_factory=list)


class RunABRequest(BaseModel):
    experiment_name: str = Field(description="Experiment name")
    hypothesis: str = Field(default="", description="Hypothesis statement")
    control_district: int = Field(default=1, ge=1, le=5)
    treatment_district: int = Field(default=2, ge=1, le=5)
    metric: str = Field(default="gmv", description="gmv / cvr / ctr / aov")
    days: int = Field(default=7, ge=1, le=90)


class CompareDistrictsRequest(BaseModel):
    district_ids: list[int] = Field(default=[1, 2, 3, 4, 5])
    metric: str = Field(default="gmv")
    days: int = Field(default=7)


# ═══════════════════════════════════════════════
# PostMortem Endpoints
# ═══════════════════════════════════════════════

@router.post("/postmortem/run")
def run_postmortem(req: PostMortemRequest):
    """Run a campaign postmortem: compare expected vs actual, extract lessons."""
    task = TaskProtocol(
        task_id=f"pm_{req.campaign_id}_{uuid.uuid4().hex[:6]}",
        workflow_id=f"wf_pm_{req.campaign_id}",
        agent_name="postmortem_agent",
        biz_goal="活动复盘",
        input={
            "campaign_id": req.campaign_id,
            "district_id": req.district_id,
            "days": req.days,
            "expected_metrics": req.expected_metrics,
            "operator_feedback": req.operator_feedback,
            "campaign_goal": req.expected_metrics.get("goal", "gmv"),
        },
        constraints={},
    )
    try:
        output = postmortem_agent.invoke(task)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Postmortem agent failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"复盘分析失败: {str(e)[:200]}")
    result = output.to_dict()

    # Try to save playbook
    if isinstance(result.get("result"), dict):
        try:
            doc_id = postmortem_agent.save_playbook(result["result"])
            if doc_id:
                result["playbook_saved"] = True
                result["playbook_doc_id"] = doc_id
        except Exception:
            pass

    return result


@router.get("/postmortem/playbooks")
def list_playbooks(limit: int = Query(default=20, ge=1, le=100)):
    """List extracted playbooks from postmortems."""
    db = SessionLocal()
    try:
        cases = db.query(PlaybookCase).order_by(PlaybookCase.created_at.desc()).limit(limit).all()
        return {
            "total": len(cases),
            "playbooks": [
                {
                    "id": c.id, "title": c.title, "campaign_type": c.campaign_type,
                    "strategy_summary": c.strategy_summary, "tags": c.tags,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in cases
            ],
        }
    finally:
        db.close()


@router.get("/postmortem/playbooks/{playbook_id}")
def get_playbook(playbook_id: int):
    """Get a specific playbook by ID."""
    db = SessionLocal()
    try:
        case = db.query(PlaybookCase).filter(PlaybookCase.id == playbook_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Playbook not found")
        return {
            "id": case.id, "title": case.title, "campaign_type": case.campaign_type,
            "district_type": case.district_type, "strategy_summary": case.strategy_summary,
            "key_metrics": case.key_metrics, "tags": case.tags,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        }
    finally:
        db.close()


# ═══════════════════════════════════════════════
# AB Experiment Endpoints
# ═══════════════════════════════════════════════

@router.post("/ab/run")
def run_ab_experiment(req: RunABRequest):
    """Run a new AB experiment with statistical testing."""
    if req.control_district == req.treatment_district:
        raise HTTPException(status_code=400, detail="Control and treatment districts must be different")
    return ab_service.run_experiment(
        experiment_name=req.experiment_name,
        hypothesis=req.hypothesis,
        control_district=req.control_district,
        treatment_district=req.treatment_district,
        metric=req.metric,
        days=req.days,
    )


@router.get("/ab/experiments")
def list_ab_experiments(limit: int = Query(default=20, ge=1, le=100)):
    """List all AB experiments."""
    return {"experiments": ab_service.list_experiments(limit)}


@router.get("/ab/experiments/{experiment_id}")
def get_ab_experiment(experiment_id: int):
    """Get a specific AB experiment by ID."""
    exp = ab_service.get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return exp


@router.post("/ab/compare")
def compare_districts(req: CompareDistrictsRequest):
    """Run pairwise comparisons across multiple districts."""
    return {
        "comparisons": ab_service.comparison_matrix(req.district_ids, req.metric, req.days),
        "metric": req.metric,
        "period_days": req.days,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════
# Agent Replay Endpoints
# ═══════════════════════════════════════════════

@router.get("/replay/runs")
def list_agent_runs(
    agent_name: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List agent run logs with optional filtering."""
    db = SessionLocal()
    try:
        q = db.query(AgentRunLog)
        if agent_name:
            q = q.filter(AgentRunLog.agent_name == agent_name)
        if task_id:
            q = q.filter(AgentRunLog.task_id.like(f"%{task_id}%"))
        logs = q.order_by(AgentRunLog.created_at.desc()).limit(limit).all()
        return {
            "total": q.count(),
            "runs": [
                {
                    "id": log.id, "run_id": log.run_id, "agent_name": log.agent_name,
                    "task_id": log.task_id, "tokens_used": log.tokens_used,
                    "latency_ms": log.latency_ms,
                    "has_error": bool(log.error),
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in logs
            ],
        }
    finally:
        db.close()


@router.get("/replay/runs/{run_id}")
def get_agent_run(run_id: str):
    """Get a specific agent run log with full input/output."""
    db = SessionLocal()
    try:
        log = db.query(AgentRunLog).filter(AgentRunLog.run_id == run_id).first()
        if not log:
            raise HTTPException(status_code=404, detail="Run not found")

        # Parse output to extract key fields
        output = log.output or {}
        result = output.get("result", {})

        return {
            "run_id": log.run_id,
            "agent_name": log.agent_name,
            "task_id": log.task_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "latency_ms": log.latency_ms,
            "tokens_used": log.tokens_used,
            "error": log.error,
            "input": log.input_protocol,
            "output_summary": {
                "confidence": output.get("confidence", 0),
                "risk_level": output.get("risk_level", "low"),
                "recommended_action": output.get("recommended_action", ""),
                "evidence": output.get("evidence", []),
                "need_human_review": output.get("need_human_review", True),
            },
            "output_result": result,
        }
    finally:
        db.close()


@router.get("/replay/timeline")
def get_agent_timeline(task_id: str = Query(description="Task or campaign ID to trace")):
    """Get a chronological timeline of all agent runs for a task/campaign."""
    db = SessionLocal()
    try:
        logs = db.query(AgentRunLog).filter(
            AgentRunLog.task_id.like(f"%{task_id}%")
        ).order_by(AgentRunLog.created_at.asc()).all()

        timeline = []
        for log in logs:
            output = log.output or {}
            timeline.append({
                "run_id": log.run_id,
                "agent_name": log.agent_name,
                "step": len(timeline) + 1,
                "latency_ms": log.latency_ms,
                "confidence": output.get("confidence", 0),
                "risk_level": output.get("risk_level", "low"),
                "recommended_action": output.get("recommended_action", ""),
                "has_error": bool(log.error),
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        return {
            "task_id": task_id,
            "total_steps": len(timeline),
            "timeline": timeline,
        }
    finally:
        db.close()


@router.get("/replay/agents")
def list_agent_types():
    """List all agent types that have been run."""
    db = SessionLocal()
    try:
        agents = db.query(AgentRunLog.agent_name).distinct().all()
        return {"agents": [a[0] for a in agents]}
    finally:
        db.close()
