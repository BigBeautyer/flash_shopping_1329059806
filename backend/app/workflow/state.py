"""Campaign workflow state schema for LangGraph StateGraph."""

from typing import List, Dict, Any, Optional, Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class CampaignState(TypedDict):
    """State object passed between agent nodes in the workflow graph.

    Uses LangGraph's Annotated reducers where needed for message accumulation.
    """
    # Campaign identity
    campaign_id: str
    workflow_id: str

    # Agent outputs (accumulated as workflow progresses)
    planner_output: Optional[Dict[str, Any]]    # Planner Agent result
    selection_output: Optional[Dict[str, Any]]   # Selection Agent result
    pricing_output: Optional[Dict[str, Any]]     # Pricing Agent result
    copywriting_output: Optional[Dict[str, Any]] # Copywriting Agent result
    review_output: Optional[Dict[str, Any]]      # Review Agent result (after selection)
    final_review_output: Optional[Dict[str, Any]] # Final review result

    # Human approval tracking
    selection_approved: bool
    final_approved: bool
    approved_skus: Optional[List[str]]   # SKU IDs operator explicitly approved
    rejected_skus: Optional[List[str]]   # SKU IDs operator explicitly rejected

    # Task queue (from Planner)
    task_queue: List[Dict[str, Any]]
    current_task_id: Optional[str]

    # Business context
    district_id: int
    category_scope: List[str]
    budget: float
    constraints: Dict[str, Any]
    biz_goal: str

    # Workflow status
    status: str  # drafting / awaiting_selection_review / awaiting_final_review / completed / rejected
    error_message: Optional[str]
