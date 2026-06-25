"""Stage 5 (redesigned): Work Order Creation — Fecha_Creacion_OT
Phase 1 of the unified WO step.

VIOLET DIAMOND: WorkOrderDraftAgent analyzes workshop damage photos and creates
a preliminary WO in the Chilean insurance format (Sustituir/Reparar/Pintar/Trabajo Externo).
Each line item includes an AI recommendation (cover / do_not_cover) for the handler.

After this stage, the handler reviews and approves/rejects individual items via
POST /claims/{id}/work-order/review (Phase 2), then uploads the workshop budget
via POST /claims/{id}/work-order/budget (Phase 3 — SparePartsPurchaseStage).
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta
from typing import Any

from agents.work_order_draft_agent import WorkOrderDraftAgent
from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis, WorkOrder, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus
from .base_stage import BaseStage, StageResult


class WorkOrderCreationStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.WORK_ORDER_CREATION

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.SPARE_PARTS_PURCHASE

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_inspeccion"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Work order creation started — photo analysis")
        claim.fecha_creacion_ot = datetime.utcnow()

        # KPI: max 5 days (120h) from Damage Inspection
        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_inspeccion:
            elapsed = claim.fecha_creacion_ot - claim.fecha_inspeccion
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 120:
                compliance = KPIComplianceStatus.BREACHED
            elif elapsed_hours > 90:
                compliance = KPIComplianceStatus.AT_RISK

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=120,
            elapsed_hours=round(elapsed_hours, 1),
            message=f"WO creation {'on time' if compliance == KPIComplianceStatus.ON_TRACK else 'DELAYED'}: {elapsed_hours:.1f}h / 120h",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        # Initialize work order
        if claim.work_order is None:
            claim.work_order = WorkOrder()

        wo_number = f"WO-{datetime.utcnow().strftime('%Y%m')}-{str(uuid.uuid4())[:6].upper()}"
        claim.work_order.number = wo_number
        claim.work_order.created_at = claim.fecha_creacion_ot
        claim.work_order.workshop_name = data.get("workshop_name", claim.work_order.workshop_name or "Authorized Repair Center")
        claim.work_order.estimated_completion_days = data.get("estimated_completion_days", 10)
        claim.work_order.phase = "draft"

        messages = [
            f"Work Order {wo_number} created",
            f"WO Creation KPI: {elapsed_hours:.1f}h / 120h — {compliance.value}",
        ]

        # ── VIOLET DIAMOND: WorkOrderDraftAgent ─────────────────────────────
        photo_files: list[bytes] = data.get("photo_bytes_list", [])
        analysis: AgentAnalysis | None = None

        if photo_files:
            draft_agent = WorkOrderDraftAgent()
            line_items = await draft_agent.run_with_photos(
                photo_bytes_list=photo_files,
                context={
                    "claim_id": claim.id,
                    "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                    "damage_description": claim.damage.description if claim.damage else "",
                    "affected_areas": claim.damage.affected_areas if claim.damage else [],
                    "damage_severity": claim.damage.severity.value if claim.damage else "moderate",
                },
            )
            claim.work_order.line_items = line_items
            covered_count = sum(1 for i in line_items if i.ai_recommendation == "cover")
            not_covered_count = len(line_items) - covered_count

            messages.extend([
                f"AI generated {len(line_items)} line items: {covered_count} recommended cover, {not_covered_count} flagged",
                "⏳ Awaiting handler review — use POST /claims/{id}/work-order/review to approve line items",
            ])

            analysis = AgentAnalysis(
                agent_type="work_order_draft",
                stage=self.stage,
                result={
                    "line_items_count": len(line_items),
                    "covered": covered_count,
                    "not_covered": not_covered_count,
                    "wo_number": wo_number,
                },
                recommendation=(
                    f"Preliminary WO {wo_number} created with {len(line_items)} items. "
                    f"AI recommends covering {covered_count} items. "
                    f"Handler review required before budget upload."
                ),
                confidence=0.85,
            )
            claim = self._add_agent_analysis(claim, analysis)
        else:
            messages.append("ℹ️ No photos provided — WO draft is empty. Upload photos via POST /claims/{id}/work-order/analyze")

        # Notify workshop
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Notify the workshop that a preliminary work order is being prepared and they should prepare their budget.",
            context={
                "claim_id": claim.id,
                "work_order_number": wo_number,
                "workshop_name": claim.work_order.workshop_name,
                "customer_name": claim.claimant.name,
                "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
                "line_items_count": len(claim.work_order.line_items),
                "next_step": "Submit your budget/quotation via the claims portal",
                "budget_deadline": (claim.fecha_creacion_ot + timedelta(hours=240)).isoformat(),
            },
        )
        notifications = notify_result.data.get("notifications_sent", [])
        claim.notifications_sent.extend(notifications)

        return self._build_stage_result(
            claim,
            agent_analysis=analysis,
            notifications_sent=notifications,
            messages=messages,
        )
