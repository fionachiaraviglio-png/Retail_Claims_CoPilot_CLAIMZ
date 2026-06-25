"""AI Agent: KPI Monitor (violet diamond throughout the workflow)

Continuously monitors claim progress against KPI targets,
predicts delays, and generates proactive alerts.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any

from .base_agent import BaseAgent, AgentResult
from models.enums import STAGE_KPI_HOURS, STAGE_ORDER, TOTAL_PROCESS_DAYS, WorkflowStage


class KPIMonitorAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return """You are an expert KPI monitoring specialist for insurance claim workflows.
Your role is to analyze claim timelines, identify compliance issues, and predict future delays.

KPI thresholds:
- Vehicle Intake: max 10 calendar days (240h) from Claim Report
- Damage Inspection: max 48 hours from Vehicle Intake
- Coverage Analysis: max 24 hours (if approved) from Damage Inspection
- Work Order Creation: max 5 days (120h) from Damage Inspection
- Spare Parts Quotation: max 24 hours from Work Order Creation
- Work Order Closure: max 10 days (240h) from Work Order Creation
- Customer Billing: max 24 hours from Vehicle Delivery
- Total process: 45 calendar days

Use the available tools to analyze each stage and generate a comprehensive KPI report.
Identify which stages are at risk (>75% of their allowed time used) or breached (>100%).
Return JSON with keys: overall_status, stage_analysis, days_elapsed_total,
predicted_completion_date, at_risk_stages, recommendations"""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "check_stage_kpi",
                "description": "Check KPI compliance for a specific workflow stage",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "stage": {"type": "string", "description": "Workflow stage name"},
                        "stage_start_iso": {"type": "string", "description": "ISO timestamp when stage started"},
                        "stage_end_iso": {"type": "string", "description": "ISO timestamp when stage ended, or null if ongoing"},
                        "max_hours_allowed": {"type": "integer"},
                    },
                    "required": ["stage", "stage_start_iso", "max_hours_allowed"],
                },
            },
            {
                "name": "predict_claim_completion",
                "description": "Predict when the claim will be completed based on current progress",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "claim_start_iso": {"type": "string"},
                        "current_stage": {"type": "string"},
                        "stages_completed": {"type": "integer"},
                        "average_stage_hours_so_far": {"type": "number"},
                    },
                    "required": ["claim_start_iso", "current_stage", "stages_completed"],
                },
            },
            {
                "name": "generate_kpi_alert",
                "description": "Generate an alert message for a KPI breach or risk",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "stage": {"type": "string"},
                        "alert_type": {"type": "string", "enum": ["at_risk", "breached"]},
                        "elapsed_hours": {"type": "number"},
                        "max_hours": {"type": "number"},
                        "claim_id": {"type": "string"},
                    },
                    "required": ["stage", "alert_type", "elapsed_hours", "max_hours"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "check_stage_kpi":
            max_hours = tool_input.get("max_hours_allowed", 24)
            start_str = tool_input.get("stage_start_iso", "")
            end_str = tool_input.get("stage_end_iso")
            try:
                start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else datetime.utcnow()
                elapsed_hours = (end - start).total_seconds() / 3600
                ratio = elapsed_hours / max_hours if max_hours else 0
                if ratio > 1.0:
                    status = "breached"
                elif ratio > 0.75:
                    status = "at_risk"
                else:
                    status = "on_track"
                return json.dumps({
                    "status": status,
                    "elapsed_hours": round(elapsed_hours, 1),
                    "max_hours": max_hours,
                    "utilization_pct": round(ratio * 100, 1),
                })
            except (ValueError, TypeError, AttributeError):
                return json.dumps({"status": "not_applicable", "error": "invalid dates"})

        if tool_name == "predict_claim_completion":
            claim_start_str = tool_input.get("claim_start_iso", "")
            avg_stage_hours = tool_input.get("average_stage_hours_so_far", 72)
            stages_completed = tool_input.get("stages_completed", 1)
            total_stages = len(STAGE_ORDER) - 1  # exclude COMPLETED
            remaining_stages = total_stages - stages_completed
            estimated_remaining_hours = remaining_stages * avg_stage_hours
            try:
                start = datetime.fromisoformat(claim_start_str.replace("Z", "+00:00"))
                elapsed_hours = (datetime.utcnow() - start).total_seconds() / 3600
                predicted_hours = elapsed_hours + estimated_remaining_hours
                predicted_days = predicted_hours / 24
                on_track = predicted_days <= TOTAL_PROCESS_DAYS
                return json.dumps({
                    "predicted_total_days": round(predicted_days, 1),
                    "target_days": TOTAL_PROCESS_DAYS,
                    "on_track": on_track,
                    "remaining_stages": remaining_stages,
                    "estimated_remaining_days": round(estimated_remaining_hours / 24, 1),
                })
            except (ValueError, TypeError):
                return json.dumps({"error": "invalid start date"})

        if tool_name == "generate_kpi_alert":
            stage = tool_input.get("stage", "")
            alert_type = tool_input.get("alert_type", "at_risk")
            elapsed = tool_input.get("elapsed_hours", 0)
            max_h = tool_input.get("max_hours", 0)
            claim_id = tool_input.get("claim_id", "N/A")
            if alert_type == "breached":
                msg = f"⚠️ KPI BREACH: Stage '{stage}' on claim {claim_id} exceeded limit by {elapsed - max_h:.1f}h ({elapsed:.1f}h / {max_h}h)"
            else:
                msg = f"⚡ KPI AT RISK: Stage '{stage}' on claim {claim_id} is at {elapsed/max_h*100:.0f}% of allowed time ({elapsed:.1f}h / {max_h}h)"
            return json.dumps({"alert_message": msg, "requires_action": alert_type == "breached"})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})
