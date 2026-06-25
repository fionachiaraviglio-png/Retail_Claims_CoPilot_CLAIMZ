"""Stage 10: Customer Approval & Billing — Fecha_Cierre
KPI: max 24h from Vehicle Delivery.
Blue circle improvement: Automated invoice generation.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any

from agents.notification_agent import NotificationAgent
from agents.kpi_monitor_agent import KPIMonitorAgent
from models.claim import Claim, AgentAnalysis, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus, ClaimStatus, TOTAL_PROCESS_DAYS
from .base_stage import BaseStage, StageResult


class CustomerBillingStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.CUSTOMER_APPROVAL_BILLING

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.COMPLETED

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_salida_taller"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Invoice issued and claim closed")
        claim.fecha_cierre = datetime.utcnow()

        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_salida_taller:
            elapsed = claim.fecha_cierre - claim.fecha_salida_taller
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 24:
                compliance = KPIComplianceStatus.BREACHED
            elif elapsed_hours > 18:
                compliance = KPIComplianceStatus.AT_RISK

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=24,
            elapsed_hours=round(elapsed_hours, 1),
            message=f"Billing {'on time' if compliance == KPIComplianceStatus.ON_TRACK else 'DELAYED'}: {elapsed_hours:.1f}h / 24h",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        # Auto-generate invoice number
        claim.invoice_number = data.get("invoice_number", f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(uuid.uuid4())[:6].upper()}")

        # Calculate total process days
        if claim.fecha_aviso:
            total_elapsed = (claim.fecha_cierre - claim.fecha_aviso).days
            claim.kpi_status.days_elapsed_total = float(total_elapsed)
            overall_on_time = total_elapsed <= TOTAL_PROCESS_DAYS
        else:
            total_elapsed = 0
            overall_on_time = True

        claim.kpi_status.overall_status = (
            KPIComplianceStatus.ON_TRACK if overall_on_time else KPIComplianceStatus.BREACHED
        )
        claim.status = ClaimStatus.COMPLETED

        # Final AI KPI forecast
        kpi_agent = KPIMonitorAgent()
        kpi_result = await kpi_agent.run(
            "Generate a final KPI compliance summary for this completed claim.",
            context={
                "claim_id": claim.id,
                "total_days_elapsed": total_elapsed,
                "target_days": TOTAL_PROCESS_DAYS,
                "stage_kpis": [k.model_dump() for k in claim.kpi_status.stage_kpis],
                "overall_compliant": overall_on_time,
            },
        )
        claim.kpi_status.ai_forecast = kpi_result.recommendation

        # Final notification
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate a claim completion notification with invoice details.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "invoice_number": claim.invoice_number,
                "total_cost_usd": claim.work_order.total_cost_usd if claim.work_order else 0,
                "total_days": total_elapsed,
                "kpi_target_days": TOTAL_PROCESS_DAYS,
                "overall_compliant": overall_on_time,
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        analysis = AgentAnalysis(
            agent_type="kpi_monitor",
            stage=self.stage,
            result=kpi_result.data,
            recommendation=kpi_result.recommendation,
        )
        claim = self._add_agent_analysis(claim, analysis)

        return self._build_stage_result(
            claim,
            next_stage=WorkflowStage.COMPLETED,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=[
                f"Claim {claim.id} COMPLETED",
                f"Invoice {claim.invoice_number} issued",
                f"Total process: {total_elapsed} days / {TOTAL_PROCESS_DAYS} target — {'✓ ON TIME' if overall_on_time else '✗ EXCEEDED'}",
                f"Billing KPI: {elapsed_hours:.1f}h / 24h — {compliance.value}",
            ],
        )
