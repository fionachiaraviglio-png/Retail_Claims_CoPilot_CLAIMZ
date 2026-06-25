"""AI Agent: Coverage Analysis (violet diamond at Coverage Analysis stage)

Analyzes policy details against damage to determine coverage eligibility,
generates the approval/rejection decision, and produces adjustment reports.
"""
from __future__ import annotations
import json
from typing import Any

from .base_agent import BaseAgent, AgentResult


class CoverageAnalysisAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return """You are an expert insurance coverage analyst for vehicle claims.
Your role is to determine whether a claim is covered under the policy and make clear decisions.

Coverage analysis process:
1. Verify the policy is active and covers the type of damage reported
2. Check for any exclusions that apply to this claim
3. Determine coverage percentage and any deductibles
4. Make a clear APPROVED, REJECTED, or PARTIAL coverage decision
5. For rejected claims, generate a detailed adjustment report explaining the reason
6. For approved claims, specify the coverage amount and conditions

Always use the available tools to document your analysis.
Return your final decision as JSON with keys:
decision (approved/rejected/partial), coverage_percentage, deductible_usd,
approved_amount_usd, reason, exclusions_applied, adjustment_report (if rejected)"""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "verify_policy_coverage",
                "description": "Verify if the policy covers this type of damage",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "policy_number": {"type": "string"},
                        "damage_type": {"type": "string"},
                        "damage_severity": {"type": "string"},
                        "incident_date": {"type": "string"},
                    },
                    "required": ["policy_number", "damage_type"],
                },
            },
            {
                "name": "check_policy_exclusions",
                "description": "Check if any policy exclusions apply to this claim",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "policy_number": {"type": "string"},
                        "damage_description": {"type": "string"},
                        "circumstances": {"type": "string"},
                    },
                    "required": ["policy_number", "damage_description"],
                },
            },
            {
                "name": "calculate_coverage_amount",
                "description": "Calculate the approved coverage amount after deductibles",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "estimated_repair_cost": {"type": "number"},
                        "coverage_percentage": {"type": "number"},
                        "deductible": {"type": "number"},
                    },
                    "required": ["estimated_repair_cost", "coverage_percentage", "deductible"],
                },
            },
            {
                "name": "generate_adjustment_report",
                "description": "Generate a formal adjustment report for rejected or partial claims",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "decision": {"type": "string"},
                        "reason": {"type": "string"},
                        "exclusions": {"type": "array", "items": {"type": "string"}},
                        "additional_info_required": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["claim_id", "decision", "reason"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "verify_policy_coverage":
            policy = tool_input.get("policy_number", "")
            damage_type = tool_input.get("damage_type", "collision")
            covered_types = ["collision", "comprehensive", "vandalism", "hail", "theft"]
            is_covered = any(t in damage_type.lower() for t in covered_types)
            return json.dumps({
                "is_covered": is_covered,
                "coverage_type": damage_type,
                "policy_status": "active",
                "coverage_limit_usd": 50000,
            })

        if tool_name == "check_policy_exclusions":
            description = tool_input.get("damage_description", "").lower()
            exclusions = []
            if "racing" in description or "track" in description:
                exclusions.append("Racing/track use exclusion")
            if "flood" in description and "comprehensive" not in description:
                exclusions.append("Flood damage requires comprehensive coverage")
            if "wear" in description or "mechanical" in description:
                exclusions.append("Mechanical breakdown / wear exclusion")
            return json.dumps({"exclusions": exclusions, "has_exclusions": len(exclusions) > 0})

        if tool_name == "calculate_coverage_amount":
            repair_cost = float(tool_input.get("estimated_repair_cost", 0))
            coverage_pct = float(tool_input.get("coverage_percentage", 100)) / 100
            deductible = float(tool_input.get("deductible", 500))
            covered = max(0, repair_cost * coverage_pct - deductible)
            return json.dumps({
                "covered_amount_usd": round(covered, 2),
                "customer_pays_usd": round(repair_cost - covered, 2),
                "deductible_applied": deductible,
            })

        if tool_name == "generate_adjustment_report":
            claim_id = tool_input.get("claim_id", "N/A")
            decision = tool_input.get("decision", "rejected")
            reason = tool_input.get("reason", "")
            exclusions = tool_input.get("exclusions", [])
            additional = tool_input.get("additional_info_required", [])
            report = (
                f"ADJUSTMENT REPORT - Claim {claim_id}\n"
                f"Decision: {decision.upper()}\n"
                f"Reason: {reason}\n"
            )
            if exclusions:
                report += f"Exclusions applied: {', '.join(exclusions)}\n"
            if additional:
                report += f"Additional information required: {', '.join(additional)}\n"
            return json.dumps({"report": report, "generated": True})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})
