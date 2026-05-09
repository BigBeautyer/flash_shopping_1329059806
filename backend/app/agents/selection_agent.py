"""Selection Agent — recommends products for a campaign.

Phase 2: LightGBM scoring with rule-based fallback (ADR-003).
"""

import json
import logging
import re
from pathlib import Path
from typing import List

from app.agents.base import BaseAgent, TaskProtocol, AgentOutput
from app.models.product_scoring import ProductScoringModel
from app.models.lightgbm_model import LightGBMScoringModel
from app.services.data_service import MockDataService
from app.services.knowledge_base import KnowledgeBase, seed_knowledge_base

logger = logging.getLogger(__name__)

# Lazy-init RAG knowledge base
_rag_kb: KnowledgeBase | None = None

def _get_rag_kb() -> KnowledgeBase:
    global _rag_kb
    if _rag_kb is None:
        _rag_kb = seed_knowledge_base()
    return _rag_kb

PROMPT_PATH = Path(__file__).parent / "prompts" / "selection.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

# Try LightGBM first, fallback to rule-based
_lgbm_model = LightGBMScoringModel()
if _lgbm_model.is_trained:
    SCORING_MODEL = _lgbm_model
    logger.info("[SelectionAgent] Using LightGBM scoring model")
else:
    SCORING_MODEL = ProductScoringModel()
    logger.info("[SelectionAgent] Using rule-based scoring (LightGBM not trained yet)")


def _get_available_skus(ds: MockDataService, category_scope: List[str]) -> List[str]:
    """Get all available SKU IDs for given categories."""
    products = ds.get_all_products()
    if products.empty:
        return []
    if category_scope:
        products = products[products["category"].isin(category_scope)]
    return products["sku_id"].tolist()


def _score_products_tool(district_id: int, category_scope: List[str], n: int = 30) -> dict:
    """Tool: score products using available model (LightGBM or rule-based)."""
    ds = MockDataService()
    sku_ids = _get_available_skus(ds, category_scope)
    if not sku_ids:
        return {"error": "No products found", "selection_list": []}

    top_n = SCORING_MODEL.get_top_n(district_id, sku_ids, n=n)
    return {"selection_list": top_n, "total_scored": len(sku_ids)}


class SelectionAgent(BaseAgent):
    """Recommends products based on district features, demand signals, and constraints."""

    def __init__(self, model: str | None = None):
        super().__init__(
            name="selection_agent",
            system_prompt=SYSTEM_PROMPT,
            tools=[_score_products_tool],
            model=model,
            temperature=0.3,
        )

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        """Override: enrich task input with model-scored products before LLM call."""
        district_id = task.input.get("district_id", 1)
        category_scope = task.input.get("category_scope", [])

        # Get model-scored products
        scored = _score_products_tool(district_id, category_scope, n=30)

        # Enrich task input for LLM
        enriched_input = {
            **task.input,
            "model_scored_products": scored.get("selection_list", []),
            "total_available": scored.get("total_scored", 0),
            "scoring_model": "LightGBM" if isinstance(SCORING_MODEL, LightGBMScoringModel) else "RuleBased",
        }
        # Add feature importance for explainability
        if isinstance(SCORING_MODEL, LightGBMScoringModel):
            fi = SCORING_MODEL.get_feature_importance()
            if fi:
                enriched_input["feature_importance"] = dict(list(fi.items())[:5])
        enriched_task = TaskProtocol(
            task_id=task.task_id,
            workflow_id=task.workflow_id,
            agent_name=task.agent_name,
            biz_goal=task.biz_goal,
            input=enriched_input,
            constraints=task.constraints,
            output_schema=task.output_schema,
        )

        # Also enrich system prompt with district profile + RAG context
        ds = MockDataService()
        profile = ds.get_district_profile(district_id)
        district_name = profile.get("name", f"商圈{district_id}")

        rag_context = _get_rag_kb().retrieve_for_context(
            campaign_goal=task.biz_goal,
            district_type=district_name,
            category_scope=category_scope,
            top_k=3,
        )

        self.system_prompt = (
            SYSTEM_PROMPT
            + f"\n\n## 当前商圈特征\n{json.dumps(profile, ensure_ascii=False)}"
            + (f"\n\n{rag_context}" if rag_context else "")
        )

        return super().invoke(enriched_task)

    def _parse_output(self, raw: str) -> AgentOutput:
        try:
            data = self._extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            return self._fallback_output(str(e))

        result = data.get("result", {})
        selection = result.get("selection_list", [])
        summary = result.get("summary", {})

        return AgentOutput(
            result=result,
            evidence=data.get("evidence", [f"推荐了 {len(selection)} 个商品"]),
            confidence=data.get("confidence", 0.7),
            risk_level=data.get("risk_level", "medium"),
            recommended_action=data.get("recommended_action", ""),
            need_human_review=data.get("need_human_review", True),
        )

    @staticmethod
    def _extract_json(raw: str) -> dict:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            return json.loads(match.group(1).strip())
        raw = raw.strip()
        if raw.startswith("{"):
            return json.loads(raw)
        raise ValueError(f"Cannot extract JSON from output: {raw[:200]}")
