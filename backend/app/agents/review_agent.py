"""Review Agent — machine review with rule checks, outputs pass/warn/block."""

import json
import re
from pathlib import Path

from app.agents.base import BaseAgent, TaskProtocol, AgentOutput

PROMPT_PATH = Path(__file__).parent / "prompts" / "review.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

# Banned words for copy compliance check (simplified for MVP)
BANNED_WORDS = ["最低价", "全网第一", "绝对最好", "第一品牌", "史上最低", "全国第一"]


def _run_rule_checks(selection: list, pricing: list, copies: list) -> dict:
    """Pre-LLM rule checks. Runs BEFORE LLM review to catch deterministic issues."""
    issues = []

    # Inventory check
    for item in selection or []:
        inv = item.get("inventory", 999)
        if inv == 0:
            issues.append({
                "sku_id": item.get("sku_id"), "type": "inventory_zero",
                "severity": "high", "description": f"库存为0",
                "suggestion": "从选品清单中移除"
            })
        elif inv < 50:
            issues.append({
                "sku_id": item.get("sku_id"), "type": "inventory_low",
                "severity": "medium", "description": f"库存仅{inv}件",
                "suggestion": "补货至50件以上或降低推荐权重"
            })

    # Gross margin check
    for item in pricing or []:
        gm = item.get("gross_margin_after", 0)
        if gm < 0.15:
            issues.append({
                "sku_id": item.get("sku_id"), "type": "margin_low",
                "severity": "medium", "description": f"毛利率 {gm:.1%} 偏低",
                "suggestion": "检查定价是否低于毛利底线"
            })

    # Banned word check
    for item in copies or []:
        for version_name, text in item.get("versions", {}).items():
            if not isinstance(text, str):
                continue
            for word in BANNED_WORDS:
                if word in text:
                    issues.append({
                        "sku_id": item.get("sku_id"),
                        "type": "banned_word",
                        "severity": "high",
                        "description": f"文案含违禁词'{word}'：{text[:50]}...",
                        "suggestion": f"替换'{word}'为合规表述"
                    })

    return {"rule_issues": issues, "rule_checks_passed": len(issues) == 0}


class ReviewAgent(BaseAgent):
    """Machine review agent: rule checks + LLM review for nuanced issues."""

    def __init__(self, model: str | None = None):
        super().__init__(
            name="review_agent",
            system_prompt=SYSTEM_PROMPT,
            tools=[_run_rule_checks],
            model=model,
            temperature=0.1,
        )

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        selection = task.input.get("selection_list", [])
        pricing = task.input.get("pricing_list", [])
        copies = task.input.get("copy_list", [])

        # Run rule checks first
        rule_results = _run_rule_checks(selection, pricing, copies)

        enriched_task = TaskProtocol(
            task_id=task.task_id,
            workflow_id=task.workflow_id,
            agent_name=task.agent_name,
            biz_goal=task.biz_goal,
            input={
                **task.input,
                "rule_check_results": rule_results,
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
        verdict = result.get("overall_verdict", "needs_revision")

        return AgentOutput(
            result=result,
            evidence=data.get("evidence", []),
            confidence=data.get("confidence", 0.85),
            risk_level="high" if verdict == "blocked" else "medium" if verdict == "needs_revision" else "low",
            recommended_action=data.get("recommended_action", ""),
            need_human_review=verdict != "pass",
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
