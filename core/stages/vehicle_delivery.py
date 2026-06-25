"""Stage 9: Vehicle Delivery — Fecha_Salida_Taller
Blue circle improvement: Digital acceptance receipt generation.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any

from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis
from models.enums import WorkflowStage
from .base_stage import BaseStage, StageResult


class VehicleDeliveryStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.VEHICLE_DELIVERY

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.CUSTOMER_APPROVAL_BILLING

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_cierre_ot"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Vehicle delivered to customer")
        claim.fecha_salida_taller = datetime.utcnow()
        claim.acceptance_receipt_signed = data.get("acceptance_receipt_signed", False)

        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate a vehicle delivery confirmation and billing preparation notice.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "delivery_date": claim.fecha_salida_taller.isoformat(),
                "acceptance_receipt_signed": claim.acceptance_receipt_signed,
                "billing_deadline": "Invoice will be issued within 24 hours",
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        analysis = AgentAnalysis(
            agent_type="notification",
            stage=self.stage,
            result=notify_result.data,
            recommendation="Vehicle delivered. Invoice preparation triggered.",
        )
        claim = self._add_agent_analysis(claim, analysis)

        return self._build_stage_result(
            claim,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=[
                f"Vehicle delivered to {claim.claimant.name}",
                f"Acceptance receipt: {'signed' if claim.acceptance_receipt_signed else 'pending signature'}",
                "Invoice must be issued within 24 hours",
            ],
        )
