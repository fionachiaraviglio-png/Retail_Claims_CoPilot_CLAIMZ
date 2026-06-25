"""Stage 7: Repair Process
No KPI defined — duration depends on parts availability and damage extent.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any

from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis
from models.enums import WorkflowStage
from .base_stage import BaseStage, StageResult


class RepairProcessStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.REPAIR_PROCESS

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.WORK_ORDER_CLOSURE

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_creacion_ot"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Vehicle repair in progress")
        claim.repair_notes = data.get("repair_notes", "Repair underway at workshop")

        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate a repair progress update for the customer.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "work_order_number": claim.work_order.number if claim.work_order else "N/A",
                "workshop_name": claim.work_order.workshop_name if claim.work_order else "Workshop",
                "estimated_completion_days": claim.work_order.estimated_completion_days if claim.work_order else None,
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        analysis = AgentAnalysis(
            agent_type="notification",
            stage=self.stage,
            result=notify_result.data,
            recommendation="Repair process initiated. Customer notified.",
        )
        claim = self._add_agent_analysis(claim, analysis)

        return self._build_stage_result(
            claim,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=["Repair process started. No KPI deadline for this stage."],
        )
