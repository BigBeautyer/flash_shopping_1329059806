"""Planner Agent — decomposes campaign goal into task list."""

import json
import re
from pathlib import Path
from app.agents.base import BaseAgent, TaskProtocol, AgentOutput


PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


class PlannerAgent(BaseAgent):
    """Orchestrator agent: campaign goal → task DAG + human checkpoints."""

    def __init__(self, model: str | None = None):
        super().__init__(
            name="planner_agent",
            system_prompt=SYSTEM_PROMPT,
            tools=[],  # Planner is pure reasoning, no tools needed
            model=model,
            temperature=0.2,
        )

    def _parse_output(self, raw: str) -> AgentOutput:
        """Parse LLM output into structured AgentOutput."""
        try:
            data = self._extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            return self._fallback_output(str(e))

        tasks = data.get("tasks", [])
        checkpoints = data.get("human_checkpoints", [])
        duration = data.get("estimated_duration", 0)

        evidence = [
            f"拆解为 {len(tasks)} 个子任务",
            f"设置 {len(checkpoints)} 个人审卡点",
            f"预估耗时 {duration} 小时",
        ]

        return AgentOutput(
            result={
                "tasks": tasks,
                "human_checkpoints": checkpoints,
                "estimated_duration": duration,
            },
            evidence=evidence,
            confidence=0.85,
            risk_level="low",
            recommended_action=f"共 {len(tasks)} 个任务，{len(checkpoints)} 个审核卡点，建议按顺序执行",
            need_human_review=True,  # Planner output always needs operator confirmation
        )

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Extract JSON from LLM output, handling markdown code blocks."""
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            return json.loads(match.group(1).strip())
        # Try raw parse
        raw = raw.strip()
        if raw.startswith("{"):
            return json.loads(raw)
        raise ValueError(f"Cannot extract JSON from output: {raw[:200]}")
