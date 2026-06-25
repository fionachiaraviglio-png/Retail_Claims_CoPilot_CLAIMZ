"""Stage 3: Damage Inspection — Fecha_Inspeccion
KPI: max 48 hours from Vehicle Intake.
VIOLET DIAMOND: AI analyzes vehicle damage to produce severity assessment.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any

from agents.damage_assessment_agent import DamageAssessmentAgent
from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis, DamageInfo, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus, DamageSeverity
from .base_stage import BaseStage, StageResult


class DamageInspectionStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.DAMAGE_INSPECTION

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.COVERAGE_ANALYSIS

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_ingreso_taller"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Damage inspection initiated")
        claim.fecha_inspeccion = datetime.utcnow()

        # KPI compliance check
        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_ingreso_taller:
            elapsed = claim.fecha_inspeccion - claim.fecha_ingreso_taller
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 48:
                compliance = KPIComplianceStatus.BREACHED
            elif elapsed_hours > 36:
                compliance = KPIComplianceStatus.AT_RISK

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=48,
            elapsed_hours=round(elapsed_hours, 1),
            message=f"Damage inspection {'on time' if compliance == KPIComplianceStatus.ON_TRACK else 'DELAYED'}: {elapsed_hours:.1f}h / 48h",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        # *** AI AGENT INTERVENTION: Damage Assessment ***
        damage_agent = DamageAssessmentAgent()
        ai_result = await damage_agent.run(
            "Analyze the vehicle damage and provide a comprehensive assessment.",
            context={
                "claim_id": claim.id,
                "vehicle": {
                    "make": claim.vehicle.make,
                    "model": claim.vehicle.model,
                    "year": claim.vehicle.year,
                    "color": claim.vehicle.color,
                },
                "reported_damage": data.get("damage_description", ""),
                "affected_areas": data.get("affected_areas", []),
                "photos_submitted": data.get("photos_submitted", False),
                "estimated_vehicle_market_value": data.get("vehicle_market_value", 20000),
            },
        )

        # Update claim damage info from AI assessment
        if claim.damage is None:
            claim.damage = DamageInfo()

        result_data = ai_result.data
        severity_str = result_data.get("severity", "moderate")
        try:
            severity = DamageSeverity(severity_str)
        except ValueError:
            severity = DamageSeverity.MODERATE

        claim.damage.severity = severity
        claim.damage.description = data.get("damage_description", "")
        claim.damage.affected_areas = data.get("affected_areas", result_data.get("affected_areas", []))
        claim.damage.estimated_cost_usd = result_data.get("estimated_cost_usd")
        claim.damage.photos_submitted = data.get("photos_submitted", False)
        claim.damage.ai_assessment = ai_result.recommendation
        claim.damage.repair_recommendations = result_data.get("repair_recommendations", [])

        analysis = AgentAnalysis(
            agent_type="damage_assessment",
            stage=self.stage,
            result=result_data,
            recommendation=ai_result.recommendation,
            confidence=ai_result.confidence,
        )
        claim = self._add_agent_analysis(claim, analysis)

        # Notify customer with damage summary
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate a damage inspection completion notification for the customer.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "damage_severity": claim.damage.severity.value,
                "estimated_cost_usd": claim.damage.estimated_cost_usd,
                "next_step": "Coverage analysis will be completed within 24 hours",
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        return self._build_stage_result(
            claim,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=[
                f"AI Damage Assessment: severity={claim.damage.severity.value}, est. cost=${claim.damage.estimated_cost_usd}",
                f"Inspection KPI: {elapsed_hours:.1f}h / 48h — {compliance.value}",
                f"Coverage analysis deadline: {(claim.fecha_inspeccion + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')} UTC",
            ],
        )
