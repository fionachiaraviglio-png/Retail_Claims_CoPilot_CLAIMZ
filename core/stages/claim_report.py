"""Stage 1: Claim Report — Fecha_Aviso
Blue circle improvement: Digital FNOL intake via API (this very API endpoint).
VIOLET DIAMOND (new): FNOLTriageAgent performs preliminary coverage assessment
against the policy PDF before the claim is dispatched to the workshop.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agents.fnol_triage_agent import FNOLTriageAgent
from agents.notification_agent import NotificationAgent
from models.claim import AgentAnalysis, Claim, FNOLData
from models.enums import WorkflowStage

from .base_stage import BaseStage, StageResult


class ClaimReportStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.CLAIM_REPORT

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.VEHICLE_INTAKE

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="FNOL received via ClearProcess API")
        claim.fecha_aviso = datetime.utcnow()
        run_fnol_triage = bool(data.get("run_fnol_triage", False))

        messages: list[str] = [
            f"Claim {claim.id} registered. FNOL date: {claim.fecha_aviso.date()}",
        ]

        # ── Store FNOL data from the digital form ────────────────────────────
        fnol_raw = data.get("fnol_data")
        if fnol_raw:
            if isinstance(fnol_raw, dict):
                claim.fnol_data = FNOLData(**fnol_raw)
            elif isinstance(fnol_raw, FNOLData):
                claim.fnol_data = fnol_raw

        # ── VIOLET DIAMOND: FNOL Triage Agent ────────────────────────────────
        triage_analysis: AgentAnalysis | None = None
        pdf_bytes: bytes | None = data.get("policy_pdf_bytes")

        if claim.fnol_data is not None and run_fnol_triage:
            triage_agent = FNOLTriageAgent()
            triage_result = await triage_agent.run_triage(
                fnol_data=claim.fnol_data,
                pdf_bytes=pdf_bytes,
            )
            claim.triage_result = triage_result

            decision_label = triage_result.preliminary_decision.value.upper()
            messages.append(
                f"AI Triage: {decision_label} (confidence {triage_result.confidence:.0%})"
            )

            if triage_result.risk_flags:
                messages.append(f"⚠️ Risk flags: {'; '.join(triage_result.risk_flags)}")
            if triage_result.exclusions_found:
                messages.append(
                    f"Exclusions identified: {'; '.join(triage_result.exclusions_found)}"
                )
            if triage_result.missing_info:
                messages.append(f"Missing info: {'; '.join(triage_result.missing_info)}")

            # Auto-approve when confidence >= 90% + covered + no risk flags
            if triage_result.auto_approval_eligible:
                claim.handler_approved = True
                messages.append(
                    "✅ AUTO-APPROVED: High confidence, no risk flags — claim dispatched to workshop"
                )
            else:
                claim.handler_approved = None  # awaiting handler review
                messages.append(
                    f"⏳ PENDING HANDLER REVIEW: {triage_result.handler_recommendation[:120]}"
                )

            triage_analysis = AgentAnalysis(
                agent_type="fnol_triage",
                stage=self.stage,
                result=triage_result.model_dump(mode="json"),
                recommendation=triage_result.handler_recommendation,
                confidence=triage_result.confidence,
            )
            claim = self._add_agent_analysis(claim, triage_analysis)
        elif claim.fnol_data is not None:
            messages.append(
                "ℹ️ FNOL cargado. Triage pendiente de ejecucion manual (run_fnol_triage=true)."
            )
        else:
            messages.append(
                "ℹ️ No FNOL form data provided — triage skipped. "
                "Submit via /claims/fnol for AI triage."
            )

        # ── Customer confirmation notification ────────────────────────────────
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate a claim confirmation notification for the customer "
            "and an internal alert for the handler.",
            context={
                "claim_id": claim.id,
                "customer_name": claim.claimant.name,
                "customer_email": claim.claimant.email,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "policy_number": claim.claimant.policy_number,
                "fecha_aviso": claim.fecha_aviso.isoformat(),
                "triage_decision": (
                    claim.triage_result.preliminary_decision.value
                    if claim.triage_result
                    else "not_run"
                ),
                "handler_approved": claim.handler_approved,
                "kpi_deadline": "Vehicle must be at workshop within 10 calendar days (if approved)",
            },
        )

        notify_analysis = AgentAnalysis(
            agent_type="notification",
            stage=self.stage,
            result=notify_result.data,
            recommendation=notify_result.recommendation,
        )
        claim = self._add_agent_analysis(claim, notify_analysis)
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        if claim.handler_approved is True:
            messages.append(
                f"KPI: Vehicle must arrive at workshop by {_deadline_str(claim.fecha_aviso, 240)}"
            )

        # Return triage analysis as the primary analysis for this stage
        return self._build_stage_result(
            claim,
            agent_analysis=triage_analysis or notify_analysis,
            notifications_sent=notifications,
            messages=messages,
        )


def _deadline_str(start: datetime, hours: int) -> str:
    deadline = start + timedelta(hours=hours)
    return deadline.strftime("%Y-%m-%d %H:%M UTC")
