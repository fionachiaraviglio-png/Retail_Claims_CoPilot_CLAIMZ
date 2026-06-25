"""ClaimWorkflow: main orchestrator that routes claims through all stages.

Each call to advance() moves the claim one stage forward, invoking
the appropriate AI agents and recording the transition.
"""
from __future__ import annotations
import logging
from typing import Any

from models.claim import Claim
from models.enums import WorkflowStage, STAGE_ORDER
from core.stages import (
    BaseStage,
    StageResult,
    ClaimReportStage,
    VehicleIntakeStage,
    DamageInspectionStage,
    CoverageAnalysisStage,
    WorkOrderCreationStage,
    SparePartsPurchaseStage,
    RepairProcessStage,
    WorkOrderClosureStage,
    VehicleDeliveryStage,
    CustomerBillingStage,
)

logger = logging.getLogger(__name__)


class ClaimWorkflow:
    """Orchestrates the end-to-end claim processing workflow.

    Usage:
        workflow = ClaimWorkflow()
        result = await workflow.advance(claim, data={...})
    """

    def __init__(self):
        self._stages: dict[WorkflowStage, BaseStage] = {
            WorkflowStage.CLAIM_REPORT: ClaimReportStage(),
            WorkflowStage.VEHICLE_INTAKE: VehicleIntakeStage(),
            WorkflowStage.DAMAGE_INSPECTION: DamageInspectionStage(),
            WorkflowStage.COVERAGE_ANALYSIS: CoverageAnalysisStage(),
            WorkflowStage.WORK_ORDER_CREATION: WorkOrderCreationStage(),
            WorkflowStage.SPARE_PARTS_PURCHASE: SparePartsPurchaseStage(),
            WorkflowStage.REPAIR_PROCESS: RepairProcessStage(),
            WorkflowStage.WORK_ORDER_CLOSURE: WorkOrderClosureStage(),
            WorkflowStage.VEHICLE_DELIVERY: VehicleDeliveryStage(),
            WorkflowStage.CUSTOMER_APPROVAL_BILLING: CustomerBillingStage(),
        }

    async def advance(
        self,
        claim: Claim,
        data: dict[str, Any] | None = None,
    ) -> StageResult:
        """Advance the claim to its next stage or process the current one."""
        stage_handler = self._stages.get(claim.current_stage)

        if stage_handler is None:
            if claim.current_stage == WorkflowStage.COMPLETED:
                raise ValueError(f"Claim {claim.id} is already completed.")
            raise ValueError(f"No handler for stage: {claim.current_stage}")

        errors = stage_handler.validate_entry(claim)
        if errors:
            raise ValueError(f"Stage entry validation failed: {'; '.join(errors)}")

        logger.info(
            "Processing claim %s — stage: %s",
            claim.id,
            claim.current_stage.value,
        )
        result = await stage_handler.process(claim, data=data)
        logger.info(
            "Claim %s stage %s complete — next: %s",
            claim.id,
            claim.current_stage.value,
            result.next_stage,
        )
        return result

    async def process_stage(
        self,
        claim: Claim,
        target_stage: WorkflowStage,
        data: dict[str, Any] | None = None,
    ) -> StageResult:
        """Process a specific stage (useful for re-processing or jumping stages).
        Only allowed if target_stage is the current stage or the immediate next stage.
        """
        current_idx = STAGE_ORDER.index(claim.current_stage) if claim.current_stage in STAGE_ORDER else -1
        target_idx = STAGE_ORDER.index(target_stage) if target_stage in STAGE_ORDER else -1

        if target_idx < current_idx:
            raise ValueError(
                f"Cannot go back to stage {target_stage.value} from {claim.current_stage.value}"
            )

        # Temporarily set the current stage so the handler processes it
        original_stage = claim.current_stage
        claim.current_stage = target_stage

        stage_handler = self._stages.get(target_stage)
        if stage_handler is None:
            claim.current_stage = original_stage
            raise ValueError(f"No handler for stage: {target_stage}")

        try:
            result = await stage_handler.process(claim, data=data)
        except Exception:
            claim.current_stage = original_stage
            raise

        return result

    def get_stage_info(self, stage: WorkflowStage) -> dict[str, Any]:
        """Return metadata about a workflow stage."""
        from models.enums import STAGE_KPI_HOURS, STAGE_FIELD_MAP
        return {
            "stage": stage.value,
            "kpi_hours": STAGE_KPI_HOURS.get(stage),
            "field_name": STAGE_FIELD_MAP.get(stage),
            "stage_index": STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1,
            "total_stages": len(STAGE_ORDER) - 1,
        }

    def get_all_stages_info(self) -> list[dict[str, Any]]:
        """Return metadata for all stages."""
        return [self.get_stage_info(s) for s in STAGE_ORDER]
