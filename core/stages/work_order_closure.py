"""Stage 8: Work Order Closure — Fecha_Cierre_OT
KPI: max 10 days (240h) from Work Order Creation.
Blue circle improvement: Digital work order sign-off.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any

from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus
from .base_stage import BaseStage, StageResult


class WorkOrderClosureStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.WORK_ORDER_CLOSURE

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.VEHICLE_DELIVERY

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_creacion_ot", "work_order"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Work order closed — repair complete")
        claim.fecha_cierre_ot = datetime.utcnow()

        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_creacion_ot:
            elapsed = claim.fecha_cierre_ot - claim.fecha_creacion_ot
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 240:
                compliance = KPIComplianceStatus.BREACHED
            elif elapsed_hours > 192:
                compliance = KPIComplianceStatus.AT_RISK

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=240,
            elapsed_hours=round(elapsed_hours, 1),
            message=f"Work order closure {'on time' if compliance == KPIComplianceStatus.ON_TRACK else 'DELAYED'}: {elapsed_hours:.1f}h / 240h",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        if claim.work_order:
            claim.work_order.closed_at = claim.fecha_cierre_ot
            if data.get("final_cost_usd"):
                claim.work_order.total_cost_usd = data["final_cost_usd"]

        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Notify the customer that their vehicle repair is complete and ready for delivery.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "work_order_number": claim.work_order.number if claim.work_order else "N/A",
                "final_cost_usd": claim.work_order.total_cost_usd if claim.work_order else 0,
                "next_step": "Vehicle delivery and signed acceptance receipt",
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        analysis = AgentAnalysis(
            agent_type="notification",
            stage=self.stage,
            result=notify_result.data,
            recommendation=f"Work order closed. Final cost: ${claim.work_order.total_cost_usd if claim.work_order else 0:.2f}",
        )
        claim = self._add_agent_analysis(claim, analysis)

        return self._build_stage_result(
            claim,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=[
                f"Work order {claim.work_order.number if claim.work_order else 'N/A'} closed",
                f"Final repair cost: ${claim.work_order.total_cost_usd if claim.work_order else 0:.2f}",
                f"WO Closure KPI: {elapsed_hours:.1f}h / 240h — {compliance.value}",
            ],
        )
