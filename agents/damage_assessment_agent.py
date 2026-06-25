"""AI Agent: Damage Assessment (violet diamond at Damage Inspection stage)

Analyzes vehicle photos and descriptions to assess damage severity,
estimate repair costs, and generate recommendations.
"""
from __future__ import annotations
import json
from typing import Any

from .base_agent import BaseAgent, AgentResult


class DamageAssessmentAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return """You are an expert vehicle damage assessment specialist for insurance claims.
Your role is to analyze vehicle damage information and provide accurate, objective assessments.

When analyzing damage:
1. Assess severity (minor/moderate/severe/total_loss) based on the affected areas and descriptions
2. Identify all damaged components and systems
3. Estimate repair costs based on industry standards
4. Determine if the vehicle is a total loss (repair cost > 75% of vehicle value)
5. Recommend the appropriate repair approach

Always use the available tools to document your assessment systematically.
Return your final assessment as a JSON object with keys:
severity, affected_areas, estimated_cost_usd, is_total_loss, repair_recommendations, confidence"""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "assess_damage_severity",
                "description": "Assess the overall damage severity based on the affected components",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "affected_areas": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of damaged vehicle areas",
                        },
                        "damage_descriptions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Descriptions of each damage item",
                        },
                    },
                    "required": ["affected_areas", "damage_descriptions"],
                },
            },
            {
                "name": "estimate_repair_cost",
                "description": "Estimate repair costs based on damage type and vehicle",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "vehicle_make": {"type": "string"},
                        "vehicle_model": {"type": "string"},
                        "vehicle_year": {"type": "integer"},
                        "damage_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "component": {"type": "string"},
                                    "damage_type": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["vehicle_make", "vehicle_model", "vehicle_year", "damage_items"],
                },
            },
            {
                "name": "check_total_loss_threshold",
                "description": "Check if damage crosses the total loss threshold",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "estimated_repair_cost": {"type": "number"},
                        "vehicle_market_value": {"type": "number"},
                    },
                    "required": ["estimated_repair_cost", "vehicle_market_value"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "assess_damage_severity":
            areas = tool_input.get("affected_areas", [])
            high_severity_parts = {"engine", "frame", "airbag", "transmission", "axle"}
            is_severe = any(a.lower() in high_severity_parts for a in areas)
            severity = "severe" if is_severe else ("moderate" if len(areas) > 3 else "minor")
            return json.dumps({"severity": severity, "areas_assessed": len(areas)})

        if tool_name == "estimate_repair_cost":
            damage_items = tool_input.get("damage_items", [])
            # Simulate cost estimation based on component types
            cost_table = {
                "bumper": 800, "hood": 1200, "door": 1500, "fender": 900,
                "windshield": 600, "engine": 5000, "frame": 8000, "airbag": 2500,
                "transmission": 4000, "light": 400, "mirror": 300, "trunk": 1100,
            }
            total = 0.0
            for item in damage_items:
                component = item.get("component", "").lower()
                matched = next((v for k, v in cost_table.items() if k in component), 700)
                total += matched
            # Add 30% for labor
            total *= 1.3
            return json.dumps({"estimated_repair_cost_usd": round(total, 2)})

        if tool_name == "check_total_loss_threshold":
            repair_cost = tool_input.get("estimated_repair_cost", 0)
            market_value = tool_input.get("vehicle_market_value", 1)
            ratio = repair_cost / market_value if market_value > 0 else 0
            is_total_loss = ratio >= 0.75
            return json.dumps({"is_total_loss": is_total_loss, "repair_to_value_ratio": round(ratio, 2)})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _parse_result(self, text: str) -> AgentResult:
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return AgentResult(
                    success=True,
                    data=data,
                    recommendation=data.get("repair_recommendations", [text])[0] if isinstance(data.get("repair_recommendations"), list) else text,
                    confidence=float(data.get("confidence", 0.85)),
                    raw_response=text,
                )
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            pass
        return AgentResult(
            success=True,
            data={"assessment": text},
            recommendation=text,
            confidence=0.7,
            raw_response=text,
        )
