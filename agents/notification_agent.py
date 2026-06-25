"""AI Agent: Notification Agent (blue circle improvements throughout the workflow)

Generates and dispatches context-aware notifications to customers,
workshops, and internal adjusters at key workflow milestones.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any

from .base_agent import BaseAgent, AgentResult
from config.settings import settings


class NotificationAgent(BaseAgent):

    _DISABLED_RESULT = AgentResult(
        success=True,
        data={"notifications_sent": [], "message_previews": [], "skipped": True},
        recommendation="Notifications disabled.",
        confidence=1.0,
        raw_response="",
    )

    def __init__(self, client=None):
        super().__init__(client)
        self.model = settings.fast_model  # Use faster model for notifications

    async def run(self, user_message: str, context=None, max_iterations: int = 10) -> AgentResult:
        if not settings.notifications_enabled:
            return self._DISABLED_RESULT
        return await super().run(user_message, context=context, max_iterations=max_iterations)

    @property
    def system_prompt(self) -> str:
        return """You are a professional communications specialist for an insurance company.
Your role is to craft clear, empathetic, and informative notifications for:
- Customers: Keep them informed without causing alarm; use simple language
- Workshops: Provide precise technical instructions and timelines
- Adjusters/Internal: Include all technical details and compliance flags

Notification guidelines:
- Customer messages: warm, clear, no jargon, include next steps and timeline
- Workshop messages: professional, detailed, include work order numbers and deadlines
- Internal alerts: technical, include KPI status, flags for action required

Use the available tools to format and dispatch notifications.
Return JSON with keys: notifications_sent (array), message_previews (array), success"""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "send_customer_notification",
                "description": "Send a notification to the claim customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "customer_email": {"type": "string"},
                        "subject": {"type": "string"},
                        "message": {"type": "string"},
                        "notification_type": {
                            "type": "string",
                            "enum": ["status_update", "action_required", "approval", "rejection", "completion"],
                        },
                        "next_steps": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["customer_name", "customer_email", "subject", "message", "notification_type"],
                },
            },
            {
                "name": "send_workshop_notification",
                "description": "Send a notification to the repair workshop",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workshop_name": {"type": "string"},
                        "work_order_number": {"type": "string"},
                        "subject": {"type": "string"},
                        "instructions": {"type": "string"},
                        "deadline_iso": {"type": "string"},
                        "priority": {"type": "string", "enum": ["normal", "high", "urgent"]},
                    },
                    "required": ["workshop_name", "subject", "instructions"],
                },
            },
            {
                "name": "send_internal_alert",
                "description": "Send an internal alert to adjusters or managers",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "alert_type": {
                            "type": "string",
                            "enum": ["kpi_breach", "coverage_decision", "escalation", "completion"],
                        },
                        "message": {"type": "string"},
                        "action_required": {"type": "boolean"},
                        "urgency": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    },
                    "required": ["claim_id", "alert_type", "message"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        timestamp = datetime.utcnow().isoformat()

        if tool_name == "send_customer_notification":
            preview = (
                f"[EMAIL → {tool_input.get('customer_email')}] "
                f"Subject: {tool_input.get('subject')} | "
                f"Type: {tool_input.get('notification_type')}"
            )
            return json.dumps({
                "sent": True,
                "channel": "email",
                "recipient": tool_input.get("customer_email"),
                "timestamp": timestamp,
                "preview": preview,
            })

        if tool_name == "send_workshop_notification":
            preview = (
                f"[WORKSHOP → {tool_input.get('workshop_name')}] "
                f"WO: {tool_input.get('work_order_number', 'N/A')} | "
                f"Priority: {tool_input.get('priority', 'normal')}"
            )
            return json.dumps({
                "sent": True,
                "channel": "workshop_portal",
                "recipient": tool_input.get("workshop_name"),
                "timestamp": timestamp,
                "preview": preview,
            })

        if tool_name == "send_internal_alert":
            preview = (
                f"[INTERNAL → Adjusters] Claim {tool_input.get('claim_id')} | "
                f"Alert: {tool_input.get('alert_type')} | "
                f"Urgency: {tool_input.get('urgency', 'medium')}"
            )
            return json.dumps({
                "sent": True,
                "channel": "internal_system",
                "timestamp": timestamp,
                "preview": preview,
                "action_required": tool_input.get("action_required", False),
            })

        return json.dumps({"error": f"Unknown tool: {tool_name}"})
