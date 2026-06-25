"""Stage 4: Coverage Analysis
KPI: 24h if approved, variable if rejected (awaiting additional info).
VIOLET DIAMOND: AI agent determines coverage eligibility and makes the decision.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any

from agents.coverage_analysis_agent import CoverageAnalysisAgent
from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus, CoverageDecision
from .base_stage import BaseStage, StageResult


class CoverageAnalysisStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.COVERAGE_ANALYSIS

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.WORK_ORDER_CREATION

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_inspeccion", "damage"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Coverage analysis started")
        analysis_start = datetime.utcnow()

        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_inspeccion:
            elapsed = analysis_start - claim.fecha_inspeccion
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 24:
                compliance = KPIComplianceStatus.BREACHED
            elif elapsed_hours > 18:
                compliance = KPIComplianceStatus.AT_RISK

        # *** AI AGENT INTERVENTION: Coverage Decision ***
        coverage_agent = CoverageAnalysisAgent()
        ai_result = await coverage_agent.run(
            "Analyze this insurance claim and determine coverage eligibility. Make a clear decision.",
            context={
                "claim_id": claim.id,
                "policy_number": claim.claimant.policy_number,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "damage_severity": claim.damage.severity.value if claim.damage else "unknown",
                "damage_description": claim.damage.description if claim.damage else "",
                "affected_areas": claim.damage.affected_areas if claim.damage else [],
                "estimated_cost_usd": claim.damage.estimated_cost_usd if claim.damage else 0,
                "incident_circumstances": data.get("incident_circumstances", "Collision"),
                "deductible_usd": data.get("deductible_usd", 500),
            },
        )

        result_data = ai_result.data
        decision_str = result_data.get("decision", "approved")
        try:
            decision = CoverageDecision(decision_str)
        except ValueError:
            decision = CoverageDecision.APPROVED

        claim.coverage_decision = decision
        claim.coverage_notes = ai_result.recommendation

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=24,
            elapsed_hours=round(elapsed_hours, 1),
            message=f"Coverage analysis {'on time' if compliance == KPIComplianceStatus.ON_TRACK else 'DELAYED'}: {elapsed_hours:.1f}h / 24h",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        agent_analysis = AgentAnalysis(
            agent_type="coverage_analysis",
            stage=self.stage,
            result=result_data,
            recommendation=ai_result.recommendation,
            confidence=ai_result.confidence,
        )
        claim = self._add_agent_analysis(claim, agent_analysis)

        # Notify customer of decision
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            f"Generate a {'coverage approval' if decision == CoverageDecision.APPROVED else 'coverage rejection'} notification.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "decision": decision.value,
                "coverage_notes": claim.coverage_notes,
                "approved_amount_usd": result_data.get("approved_amount_usd"),
                "next_step": "Work order will be created within 5 days" if decision == CoverageDecision.APPROVED else "Adjustment report has been generated",
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        next_stage = self.next_stage if decision in (CoverageDecision.APPROVED, CoverageDecision.PARTIAL) else None

        return self._build_stage_result(
            claim,
            next_stage=next_stage,
            agent_analysis=agent_analysis,
            notifications_sent=notifications,
            messages=[
                f"AI Coverage Decision: {decision.value.upper()}",
                f"Analysis KPI: {elapsed_hours:.1f}h / 24h — {compliance.value}",
                *(["Claim proceeding to Work Order Creation"] if next_stage else ["Claim placed on hold — adjustment report generated"]),
            ],
        )
