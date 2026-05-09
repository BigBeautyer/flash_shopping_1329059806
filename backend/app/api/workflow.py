"""Workflow API — create campaigns, get state, approve/reject at checkpoints."""

import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from langgraph.types import Command

from app.workflow.graph import campaign_graph
from app.workflow.state import CampaignState
from app.db.models import SessionLocal, Campaign, CampaignTask, CampaignApproval

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


# ── Request / Response Models ──

class CreateCampaignRequest(BaseModel):
    name: str
    goal_type: str = Field(default="gmv", description="new_user / clearance / gmv / roi")
    city: str = "北京"
    district_id: int = 1
    category_scope: list[str] = Field(default_factory=lambda: ["水果", "饮料"])
    budget: float = 5000
    constraints: dict = Field(default_factory=lambda: {
        "gross_margin_floor": 0.18,
        "inventory_min": 50,
    })


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""
    approved_skus: list[str] = Field(default_factory=list, description="SKU IDs explicitly approved")
    rejected_skus: list[str] = Field(default_factory=list, description="SKU IDs explicitly rejected")


# ── Endpoints ──

@router.post("/campaigns")
def create_campaign(req: CreateCampaignRequest):
    """Create a new campaign and start the workflow.

    Returns campaign_id and thread_id for tracking.
    """
    campaign_id = f"CAMP_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    workflow_id = f"WF_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    thread_id = campaign_id  # Use campaign_id as LangGraph thread_id

    # Persist campaign
    db = SessionLocal()
    try:
        campaign = Campaign(
            id=campaign_id, name=req.name, goal_type=req.goal_type,
            city=req.city, district=str(req.district_id), budget=req.budget,
            category_scope=req.category_scope, status="draft",
        )
        db.add(campaign)
        db.commit()
    finally:
        db.close()

    # Initialize state
    initial_state: CampaignState = {
        "campaign_id": campaign_id,
        "workflow_id": workflow_id,
        "planner_output": None,
        "selection_output": None,
        "pricing_output": None,
        "copywriting_output": None,
        "review_output": None,
        "final_review_output": None,
        "selection_approved": False,
        "final_approved": False,
        "approved_skus": [],
        "rejected_skus": [],
        "task_queue": [],
        "current_task_id": None,
        "district_id": req.district_id,
        "category_scope": req.category_scope,
        "budget": req.budget,
        "constraints": req.constraints,
        "biz_goal": req.goal_type,
        "status": "draft",
        "error_message": None,
    }

    # Start workflow execution — runs planner + selection, then pauses at selection_review
    config = {"configurable": {"thread_id": thread_id}}
    result = campaign_graph.invoke(initial_state, config)

    return {
        "campaign_id": campaign_id,
        "thread_id": thread_id,
        "status": result.get("status", "unknown"),
        "planner_output": result.get("planner_output"),
        "selection_output": result.get("selection_output"),
    }


@router.get("/campaigns/{campaign_id}/state")
def get_campaign_state(campaign_id: str):
    """Get current workflow state for a campaign."""
    config = {"configurable": {"thread_id": campaign_id}}
    try:
        state = campaign_graph.get_state(config)
        if state is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        # Return serializable state
        return {
            "campaign_id": campaign_id,
            "status": state.values.get("status", "unknown"),
            "next_nodes": state.next if state.next else [],
            "planner_output": state.values.get("planner_output"),
            "selection_output": state.values.get("selection_output"),
            "pricing_output": state.values.get("pricing_output"),
            "copywriting_output": state.values.get("copywriting_output"),
            "final_review_output": state.values.get("final_review_output"),
            "selection_approved": state.values.get("selection_approved", False),
            "final_approved": state.values.get("final_approved", False),
            "approved_skus": state.values.get("approved_skus", []),
            "rejected_skus": state.values.get("rejected_skus", []),
            "constraints": state.values.get("constraints", {}),
            "biz_goal": state.values.get("biz_goal", ""),
            "district_id": state.values.get("district_id", 1),
        }
    except Exception as e:
        # Fallback: try to get from latest snapshot
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/approve-selection")
def approve_selection(campaign_id: str, req: ApprovalRequest):
    """Operator approves or rejects the selection output.

    Approved → continues to pricing → copywriting → review → final_review checkpoint.
    Rejected → goes back to selection agent to redo.
    """
    config = {"configurable": {"thread_id": campaign_id}}
    state = campaign_graph.get_state(config)
    if state is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Update state with approval decision (include approved/rejected SKU lists)
    campaign_graph.update_state(
        config,
        {
            "selection_approved": req.approved,
            "status": "running" if req.approved else "draft",
            "approved_skus": req.approved_skus,
            "rejected_skus": req.rejected_skus,
        },
    )

    if not req.approved:
        # Rejected: resume → goes back to selection_node via route_selection
        result = campaign_graph.invoke(Command(resume={"selection_approved": False}), config)
        return {
            "campaign_id": campaign_id,
            "status": result.get("status", "draft"),
            "message": "选品已驳回，Agent 重新选品中",
        }

    # Approved (full or partial): filter selection list to only approved SKUs before continuing
    if req.approved_skus:
        # Filter selection_output to only approved SKUs so downstream agents only see them
        selection_output = state.values.get("selection_output") or {}
        sel_result = selection_output.get("result", {})
        full_list = sel_result.get("selection_list", [])
        filtered_list = [p for p in full_list if p.get("sku_id") in set(req.approved_skus)]
        campaign_graph.update_state(
            config,
            {"selection_output": {**selection_output, "result": {**sel_result, "selection_list": filtered_list}}},
        )

    # Resume → continues to pricing → copywriting → review → final_review checkpoint
    result = campaign_graph.invoke(Command(resume={"selection_approved": True}), config)
    return {
        "campaign_id": campaign_id,
        "status": result.get("status", "awaiting_final_review"),
        "pricing_output": result.get("pricing_output"),
        "copywriting_output": result.get("copywriting_output"),
        "final_review_output": result.get("final_review_output"),
        "approved_skus": req.approved_skus,
        "rejected_skus": req.rejected_skus,
        "message": f"已通过 {len(req.approved_skus)} 个商品，进入定价→文案→审核流程",
    }


@router.post("/campaigns/{campaign_id}/approve-final")
def approve_final(campaign_id: str, req: ApprovalRequest):
    """Operator approves or rejects the final review.

    Approved → campaign completed.
    Rejected → goes back to review agent.
    """
    config = {"configurable": {"thread_id": campaign_id}}
    state = campaign_graph.get_state(config)
    if state is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign_graph.update_state(
        config,
        {"final_approved": req.approved, "status": "completed" if req.approved else "draft"},
    )

    if not req.approved:
        result = campaign_graph.invoke(Command(resume={"final_approved": False}), config)
        return {
            "campaign_id": campaign_id,
            "status": result.get("status", "awaiting_final_review"),
            "message": "终审已驳回，Agent 重新审核中",
        }

    # Approved: end
    result = campaign_graph.invoke(Command(resume={"final_approved": True}), config)

    # Persist approval
    db = SessionLocal()
    try:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if campaign:
            campaign.status = "running"  # campaign is now live
            db.commit()
    finally:
        db.close()

    return {
        "campaign_id": campaign_id,
        "status": "completed",
        "message": "终审已通过，活动可以上线",
    }
