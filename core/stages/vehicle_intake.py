"""Stage 2: Vehicle Intake — Fecha_Ingreso_Taller
KPI: max 10 calendar days (240h) from Claim Report.
Blue circle improvement: Automated workshop notification on vehicle arrival.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any

from agents.notification_agent import NotificationAgent
from agents.kpi_monitor_agent import KPIMonitorAgent
from models.claim import Claim, AgentAnalysis, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus
from .base_stage import BaseStage, StageResult


class VehicleIntakeStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.VEHICLE_INTAKE

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.DAMAGE_INSPECTION

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_aviso"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Vehicle arrived at workshop")
        claim.fecha_ingreso_taller = datetime.utcnow()

        if claim.work_order is None:
            from models.claim import WorkOrder
            claim.work_order = WorkOrder()
        claim.work_order.workshop_name = data.get("workshop_name", "Authorized Repair Center")

        # KPI check: was the intake within 10 days?
        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_aviso:
            elapsed = claim.fecha_ingreso_taller - claim.fecha_aviso
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 240:
                compliance = KPIComplianceStatus.BREACHED
            elif elapsed_hours > 180:
                compliance = KPIComplianceStatus.AT_RISK

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=240,
            elapsed_hours=round(elapsed_hours, 1),
            message=f"Vehicle intake {'on time' if compliance == KPIComplianceStatus.ON_TRACK else 'DELAYED'}: {elapsed_hours:.1f}h / 240h allowed",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        # Notify customer and workshop
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate vehicle intake notifications: confirm receipt to customer and provide inspection deadline to workshop.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "workshop_name": claim.work_order.workshop_name,
                "intake_date": claim.fecha_ingreso_taller.isoformat(),
                "inspection_deadline": (claim.fecha_ingreso_taller + timedelta(hours=48)).isoformat(),
                "kpi_compliance": compliance.value,
            },
        )

        analysis = AgentAnalysis(
            agent_type="notification",
            stage=self.stage,
            result=notify_result.data,
            recommendation=notify_result.recommendation,
        )
        claim = self._add_agent_analysis(claim, analysis)
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        return self._build_stage_result(
            claim,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=[
                f"Vehicle registered at {claim.work_order.workshop_name}",
                f"Intake KPI: {elapsed_hours:.1f}h elapsed / 240h max — {compliance.value}",
                f"Damage inspection must complete by {(claim.fecha_ingreso_taller + timedelta(hours=48)).strftime('%Y-%m-%d %H:%M')} UTC",
            ],
        )
