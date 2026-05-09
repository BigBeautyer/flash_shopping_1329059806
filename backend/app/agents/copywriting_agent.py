"""Copywriting Agent — generates multi-version marketing copy."""

import json
import re
from pathlib import Path

from app.agents.base import BaseAgent, TaskProtocol, AgentOutput
from app.services.data_service import MockDataService

PROMPT_PATH = Path(__file__).parent / "prompts" / "copywriting.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


class CopywritingAgent(BaseAgent):
    """Generates marketing copy tailored to district demographics."""

    def __init__(self, model: str | None = None):
        super().__init__(
            name="copywriting_agent",
            system_prompt=SYSTEM_PROMPT,
            tools=[],
            model=model,
            temperature=0.6,  # Higher creativity for copy
        )

    def invoke(self, task: TaskProtocol) -> AgentOutput:
        district_id = task.input.get("district_id", 1)
        ds = MockDataService()
        profile = ds.get_district_profile(district_id)
        self.system_prompt = SYSTEM_PROMPT + f"\n\n## 当前商圈特征\n{json.dumps(profile, ensure_ascii=False)}"
        return super().invoke(task)

    def _parse_output(self, raw: str) -> AgentOutput:
        try:
            data = self._extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            return self._fallback_output(str(e))

        result = data.get("result", {})
        copies = result.get("copy_list", [])

        return AgentOutput(
            result=result,
            evidence=data.get("evidence", [f"为 {len(copies)} 个商品生成了营销文案"]),
            confidence=data.get("confidence", 0.80),
            risk_level=data.get("risk_level", "low"),
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
