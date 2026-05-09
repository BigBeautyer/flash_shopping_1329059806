"""LangGraph StateGraph — the campaign workflow engine.

Flow:
  Planner → Selection → [HUMAN CHECKPOINT: selection_review]
    → Pricing → Copywriting → Review → [HUMAN CHECKPOINT: final_review]
    → Complete

Interrupt-before on checkpoint nodes ensures operator sees agent output
BEFORE deciding to approve/reject.
"""

import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.workflow.state import CampaignState
from app.agents.base import TaskProtocol
from app.agents.planner_agent import PlannerAgent
from app.agents.selection_agent import SelectionAgent
from app.agents.pricing_agent import PricingAgent
from app.agents.copywriting_agent import CopywritingAgent
from app.agents.review_agent import ReviewAgent
from app.core.config import settings

logger = logging.getLogger(__name__)

# Singleton agents
planner = PlannerAgent()
selection = SelectionAgent()
pricing = PricingAgent()
copywriting = CopywritingAgent()
review = ReviewAgent()


# ═══════════════ Agent Nodes ═══════════════

def planner_node(state: CampaignState) -> CampaignState:
    logger.info(f"[{state['campaign_id']}] Planner running")
    task = TaskProtocol(
        task_id=f"plan_{state['campaign_id']}", workflow_id=state["workflow_id"],
        agent_name="planner_agent", biz_goal=state["biz_goal"],
        input={
            "campaign_id": state["campaign_id"], "district_id": state["district_id"],
            "category_scope": state["category_scope"], "budget": state["budget"],
            "constraints": state["constraints"], "biz_goal": state["biz_goal"],
        },
        constraints=state["constraints"],
    )
    output = planner.invoke(task)
    state["planner_output"] = output.to_dict()
    state["task_queue"] = output.result.get("tasks", [])
    return state


def selection_node(state: CampaignState) -> CampaignState:
    logger.info(f"[{state['campaign_id']}] Selection running")
    task = TaskProtocol(
        task_id=f"sel_{state['campaign_id']}", workflow_id=state["workflow_id"],
        agent_name="selection_agent", biz_goal=state["biz_goal"],
        input={
            "district_id": state["district_id"], "category_scope": state["category_scope"],
            "campaign_id": state["campaign_id"], "constraints": state["constraints"],
            "rejected_skus": state.get("rejected_skus", []),
            "approved_skus": state.get("approved_skus", []),
        },
        constraints=state["constraints"],
    )
    output = selection.invoke(task)
    state["selection_output"] = output.to_dict()
    state["status"] = "awaiting_selection_review"
    return state


def pricing_node(state: CampaignState) -> CampaignState:
    logger.info(f"[{state['campaign_id']}] Pricing running")
    sel = state.get("selection_output", {}).get("result", {})
    selection_list = sel.get("selection_list", []) if isinstance(sel, dict) else []
    task = TaskProtocol(
        task_id=f"price_{state['campaign_id']}", workflow_id=state["workflow_id"],
        agent_name="pricing_agent", biz_goal=state["biz_goal"],
        input={
            "district_id": state["district_id"], "selection_list": selection_list,
            "constraints": state["constraints"], "budget": state["budget"],
        },
        constraints=state["constraints"],
    )
    output = pricing.invoke(task)
    state["pricing_output"] = output.to_dict()
    return state


def copywriting_node(state: CampaignState) -> CampaignState:
    logger.info(f"[{state['campaign_id']}] Copywriting running")
    sel = state.get("selection_output", {}).get("result", {})
    selection_list = sel.get("selection_list", []) if isinstance(sel, dict) else []
    task = TaskProtocol(
        task_id=f"copy_{state['campaign_id']}", workflow_id=state["workflow_id"],
        agent_name="copywriting_agent", biz_goal=state["biz_goal"],
        input={
            "district_id": state["district_id"], "selection_list": selection_list,
            "campaign_id": state["campaign_id"],
        },
        constraints=state["constraints"],
    )
    output = copywriting.invoke(task)
    state["copywriting_output"] = output.to_dict()
    return state


def review_node(state: CampaignState) -> CampaignState:
    logger.info(f"[{state['campaign_id']}] Final review running")
    sel = state.get("selection_output", {}).get("result", {})
    selection_list = sel.get("selection_list", []) if isinstance(sel, dict) else []
    pricing_list = state.get("pricing_output", {}).get("result", {}).get("pricing_list", [])
    copy_list = state.get("copywriting_output", {}).get("result", {}).get("copy_list", [])

    task = TaskProtocol(
        task_id=f"review_{state['campaign_id']}", workflow_id=state["workflow_id"],
        agent_name="review_agent", biz_goal=state["biz_goal"],
        input={
            "selection_list": selection_list, "pricing_list": pricing_list,
            "copy_list": copy_list, "constraints": state["constraints"],
        },
        constraints=state["constraints"],
    )
    output = review.invoke(task)
    state["final_review_output"] = output.to_dict()
    state["status"] = "awaiting_final_review"
    return state


# ═══════════════ Checkpoint Nodes (no-op, for interrupt) ═══════════════

def selection_checkpoint(_state: CampaignState) -> CampaignState:
    """No-op node. Interrupted before = operator sees selection results."""
    return _state


def final_checkpoint(state: CampaignState) -> CampaignState:
    """No-op node. Interrupted before = operator sees all outputs for final approval."""
    state["status"] = "completed"
    return state


# ═══════════════ Routing ═══════════════

def route_selection(state: CampaignState) -> Literal["selection_node", "pricing_node"]:
    """After checkpoint: approve → continue to pricing, reject → redo selection."""
    if state.get("selection_approved"):
        return "pricing_node"
    return "selection_node"  # rejected, redo


def route_final(state: CampaignState) -> Literal["review_node", "__end__"]:
    """After final checkpoint: approve → end, reject → redo review."""
    if state.get("final_approved"):
        state["status"] = "completed"
        return "__end__"
    return "review_node"


# ═══════════════ Graph ═══════════════

def build_workflow() -> StateGraph:
    wf = StateGraph(CampaignState)

    wf.add_node("planner", planner_node)
    wf.add_node("selection", selection_node)
    wf.add_node("selection_review", selection_checkpoint)  # no-op checkpoint
    wf.add_node("pricing", pricing_node)
    wf.add_node("copywriting", copywriting_node)
    wf.add_node("review", review_node)
    wf.add_node("final_review", final_checkpoint)  # no-op checkpoint

    wf.set_entry_point("planner")

    # Linear flow
    wf.add_edge("planner", "selection")
    wf.add_edge("selection", "selection_review")

    # Conditional: operator decision
    wf.add_conditional_edges("selection_review", route_selection, {
        "pricing_node": "pricing",
        "selection_node": "selection",
    })

    wf.add_edge("pricing", "copywriting")
    wf.add_edge("copywriting", "review")
    wf.add_edge("review", "final_review")

    # Conditional: final approval
    wf.add_conditional_edges("final_review", route_final, {
        "__end__": END,
        "review_node": "review",
    })

    # Compile with SQLite checkpointer
    # langgraph-checkpoint-sqlite 2.0+ requires context manager for from_conn_string
    import os
    import sqlite3
    db_path = settings.SQLITE_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return wf.compile(
        checkpointer=checkpointer,
        interrupt_before=["selection_review", "final_review"],
    )


campaign_graph = build_workflow()
