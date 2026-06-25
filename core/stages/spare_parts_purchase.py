"""Stage 6 (redesigned): Work Order Budget Reconciliation — formerly Spare Parts Purchase
Phase 3 of the unified WO step.

VIOLET DIAMOND: WorkOrderReconciliationAgent reconciles the handler-approved
preliminary WO against the workshop's uploaded budget document.

It:
  1. Extracts all items from the workshop budget (PDF/images)
  2. Fuzzy-matches workshop item names to approved preliminary WO items
  3. Fills in actual costs from the workshop budget into each WO column
  4. Alerts on workshop items that don't match any approved item (included with flag)
  5. Calculates the WorkOrderSummary totals

Phase 2 (handler line-item review) happens via POST /claims/{id}/work-order/review
before this stage is triggered.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any

from agents.work_order_reconciliation_agent import WorkOrderReconciliationAgent
from agents.notification_agent import NotificationAgent
from models.claim import Claim, AgentAnalysis, WorkOrderSummary, KPIEntry
from models.enums import WorkflowStage, KPIComplianceStatus
from .base_stage import BaseStage, StageResult


class SparePartsPurchaseStage(BaseStage):

    @property
    def stage(self) -> WorkflowStage:
        return WorkflowStage.SPARE_PARTS_PURCHASE

    @property
    def next_stage(self) -> WorkflowStage:
        return WorkflowStage.REPAIR_PROCESS

    @property
    def required_fields(self) -> list[str]:
        return ["fecha_creacion_ot", "work_order"]

    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        data = data or {}
        claim = self._record_transition(claim, notes="Workshop budget reconciliation started")
        reconciliation_time = datetime.utcnow()

        # KPI: 24h for quotation from WO creation (original KPI kept for tracking)
        elapsed_hours = 0.0
        compliance = KPIComplianceStatus.ON_TRACK
        if claim.fecha_creacion_ot:
            elapsed = reconciliation_time - claim.fecha_creacion_ot
            elapsed_hours = elapsed.total_seconds() / 3600
            if elapsed_hours > 24:
                compliance = KPIComplianceStatus.AT_RISK   # budget upload timing
            # Not breached at this point — workshop has up to 10 days for purchase

        kpi_entry = KPIEntry(
            stage=self.stage,
            compliance_status=compliance,
            max_hours_allowed=240,   # 10 days total for purchase
            elapsed_hours=round(elapsed_hours, 1),
            message=f"Budget reconciliation at {elapsed_hours:.1f}h from WO creation",
        )
        claim.kpi_status.stage_kpis.append(kpi_entry)

        messages: list[str] = []
        analysis: AgentAnalysis | None = None

        budget_files: list[bytes] = data.get("budget_files", [])
        deductible = data.get("deductible", 0.0)

        if budget_files and claim.work_order and claim.work_order.line_items:
            # Get approved preliminary line items only
            approved_items = [
                item for item in claim.work_order.line_items
                if item.handler_approved is not False   # include True and None (not reviewed = tentative)
            ]

            if not approved_items:
                approved_items = claim.work_order.line_items  # fallback: use all

            # ── VIOLET DIAMOND: WorkOrderReconciliationAgent ─────────────────
            reconciliation_agent = WorkOrderReconciliationAgent()
            final_items, summary, alerts = await reconciliation_agent.run_reconciliation(
                approved_line_items=approved_items,
                budget_files=budget_files,
                deductible=deductible,
            )

            claim.work_order.line_items = final_items
            claim.work_order.summary = summary
            claim.work_order.unapproved_alerts = alerts
            claim.work_order.phase = "final"

            # Sync legacy cost fields from summary
            if summary.total_compania_neto > 0:
                claim.work_order.total_cost_usd = summary.total_compania_neto
                claim.work_order.parts_cost_usd = summary.repuestos
                claim.work_order.labor_cost_usd = summary.reparacion + summary.pintura

            unapproved_count = sum(1 for i in final_items if i.is_unapproved_alert)
            messages.extend([
                f"Final WO created with {len(final_items)} items",
                f"Total Compañía Neto: {summary.total_compania_neto:,.0f}",
                f"Subtotal Neto: {summary.subtotal_neto:,.0f} | Deducible: {summary.deducible_neto:,.0f}",
            ])

            if alerts:
                messages.append(
                    f"⚠️ {len(alerts)} UNAPPROVED ITEM ALERT(S): workshop charged for items not in approved WO"
                )
                for alert in alerts[:3]:  # cap at 3 in messages
                    messages.append(f"  — {alert}")

            analysis = AgentAnalysis(
                agent_type="work_order_reconciliation",
                stage=self.stage,
                result={
                    "final_items_count": len(final_items),
                    "unapproved_alerts": len(alerts),
                    "total_compania_neto": summary.total_compania_neto,
                    "subtotal_neto": summary.subtotal_neto,
                    "deducible_neto": summary.deducible_neto,
                },
                recommendation=(
                    f"Final WO reconciled. Total Compañía: {summary.total_compania_neto:,.0f}. "
                    f"{len(alerts)} unapproved item alert(s)."
                    if not alerts else
                    f"ALERTS: {len(alerts)} workshop items not in approved WO. Review before approval."
                ),
                confidence=0.9 if not alerts else 0.7,
            )
            claim = self._add_agent_analysis(claim, analysis)
        else:
            messages.append(
                "ℹ️ No budget document provided — upload via POST /claims/{id}/work-order/budget"
            )

        # Notify handler of final WO with any alerts
        notifier = NotificationAgent()
        notify_result = await notifier.run(
            "Generate a work order finalization notification highlighting any unapproved items.",
            context={
                "claim_id": claim.id,
                "work_order_number": claim.work_order.number if claim.work_order else "N/A",
                "total_compania_neto": claim.work_order.summary.total_compania_neto if claim.work_order and claim.work_order.summary else 0,
                "unapproved_alerts": claim.work_order.unapproved_alerts if claim.work_order else [],
                "action_required": bool(claim.work_order and claim.work_order.unapproved_alerts),
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
