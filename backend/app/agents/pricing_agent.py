"""Pricing Agent — suggests pricing strategies with rule-layer hard constraints."""

import json
import re
from pathlib import Path
from typing import Dict, List

from app.agents.base import BaseAgent, TaskProtocol, AgentOutput
from app.services.data_service import MockDataService

PROMPT_PATH = Path(__file__).parent / "prompts" / "pricing.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


def _get_competitor_context(district_id: int, categories: List[str]) -> dict:
    """Tool: fetch competitor data for pricing context."""
    ds = MockDataService()
    result = {}
    for cat in set(categories):
        comp = ds.get_competitor_data(district_id, cat)
        if not comp.empty:
            result[cat] = {
                "avg_discount": round(comp["discount_depth"].mean(), 3),
                "price_band": [
                    round(comp["price_band_low"].mean(), 2),
                    round(comp["price_band_high"].mean(), 2),
                ],
                "competitor_count": len(comp),
            }
    return result


def _apply_pricing_rules(
    products: List[dict], constraints: dict, competitor_ctx: dict
) -> List[dict]:
    """Apply hard pricing constraints (rule layer, ADR-005).

    Returns pricing suggestions with rule violations flagged.
    """
    margin_floor = constraints.get("gross_margin_floor", 0.15)
    results = []

    for p in products:
        sku_id = p.get("sku_id", "")
        cat = p.get("category", "")
        cost = p.get("cost_price") or p.get("original_price", 10) * 0.6
        original = p.get("original_price") or p.get("price", 10)

        # Get competitor price band for this category
        comp = competitor_ctx.get(cat, {})
        price_band_low = (comp.get("price_band", [0, 0]) or [0, 0])[0]
        price_band_high = (comp.get("price_band", [0, 100]) or [0, 100])[1]

        # Rule 1: Margin floor
        min_price = round(cost / (1 - margin_floor), 2)

        # Rule 2: Price band constraint
        suggested = max(min_price, original * 0.7)
        suggested = min(suggested, original * 1.0)  # don't exceed original

        if price_band_low > 0:
            suggested = max(suggested, price_band_low * 0.9)
            suggested = min(suggested, price_band_high * 1.1)

        suggested = round(suggested, 2)
        discount = round(1 - suggested / original, 3) if original > 0 else 0
        gm_after = round(1 - cost / suggested, 3) if suggested > 0 else 0

        # Risk assessment
        risks = []
        if gm_after < margin_floor:
            risks.append(f"毛利率 {gm_after:.1%} 低于底线 {margin_floor:.1%}")
        if abs(discount) > 0.30:
            risks.append(f"价格波动 {discount:.1%} 超过30%阈值")

        # Hot product check: top 10% by recommend_score
        is_hot = p.get("recommend_score", 0) >= 0.80

        results.append({
            "sku_id": sku_id,
            "name": p.get("name", sku_id),
            "category": cat,
            "cost_price": cost,
            "original_price": original,
            "suggested_price": suggested,
            "discount": discount,
            "gross_margin_after": gm_after,
            "risk_flags": risks,
            "need_human_review": is_hot or len(risks) > 0,
        })

    return results


class PricingAgent(BaseAgent):
    """Suggests pricing with rule-layer constraints."""

    def __init__(self, model: str | None = None):
        super().__init__(
            name="pricing_agent",
            system_prompt=SYSTEM_PROMPT,
            tools=[_get_competitor_context],
            model=model,
            temperature=0.2,
        )

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        district_id = task.input.get("district_id", 1)
        selection = task.input.get("selection_list", [])
        constraints = task.input.get("constraints", {})

        # Get competitor context
        categories = list(set(p.get("category", "") for p in selection if p.get("category")))
        competitor_ctx = _get_competitor_context(district_id, categories)

        # Apply rule-layer pricing
        priced = _apply_pricing_rules(selection, constraints, competitor_ctx)

        # Enrich task for LLM
        enriched_task = TaskProtocol(
            task_id=task.task_id,
            workflow_id=task.workflow_id,
            agent_name=task.agent_name,
            biz_goal=task.biz_goal,
            input={
                **task.input,
                "rule_pricing": priced,
                "competitor_context": competitor_ctx,
            },
            constraints=task.constraints,
            output_schema=task.output_schema,
        )
        return super().invoke(enriched_task)

    def _parse_output(self, raw: str) -> AgentOutput:
        try:
            data = self._extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            return self._fallback_output(str(e))

        result = data.get("result", {})
        pricing = result.get("pricing_list", [])

        return AgentOutput(
            result=result,
            evidence=data.get("evidence", [f"为 {len(pricing)} 个商品生成了定价建议"]),
            confidence=data.get("confidence", 0.75),
            risk_level=data.get("risk_level", "low"),
            recommended_action=data.get("recommended_action", ""),
            need_human_review=data.get("need_human_review", False),
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
