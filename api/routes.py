"""FastAPI routes for the ClearProcess claim workflow API."""
from __future__ import annotations
import logging
from typing import Any
from datetime import datetime
import uuid
import base64

from fastapi import APIRouter, HTTPException, Body, UploadFile, File
from pydantic import BaseModel

from models.claim import Claim, ClaimantInfo, VehicleInfo, FNOLData, DamagedPartGroup, WorkOrder
from models.enums import WorkflowStage, ClaimStatus, TOTAL_PROCESS_DAYS
from agents.fnol_triage_agent import FNOLTriageAgent
from core.workflow import ClaimWorkflow
from agents.work_order_draft_agent import WorkOrderDraftAgent
from agents.work_order_reconciliation_agent import WorkOrderReconciliationAgent
from agents.bill_comparison_agent import BillComparisonAgent
from api.schemas import (
    CreateClaimRequest,
    AdvanceStageRequest,
    ClaimSummaryResponse,
    ClaimDetailResponse,
    AdvanceStageResponse,
    WorkflowInfoResponse,
    StageInfoResponse,
    KPIStatusResponse,
    KPIEntryResponse,
    AgentAnalysisResponse,
    FNOLSubmissionRequest,
    HandlerApprovalRequest,
    FNOLResponse,
    TriageResultResponse,
    WorkOrderLineItemResponse,
    WorkOrderSummaryResponse,
    WorkOrderResponse,
    HandlerWOReviewRequest,
    WOAnalyzeRequest,
    WOReviewResponse,
    WOBudgetResponse,
)
from database import db
from storage import save_upload, list_files
from pathlib import Path

logger = logging.getLogger(__name__)

router = APIRouter()

_workflow = ClaimWorkflow()


# ── Pydantic Models ────────────────────────────────────────────────────────────

class RequestDocumentsRequest(BaseModel):
    types: list[str]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _triage_response(claim: Claim) -> TriageResultResponse | None:
    if claim.triage_result is None:
        return None
    tr = claim.triage_result
    return TriageResultResponse(
        preliminary_decision=tr.preliminary_decision.value,
        matched_clauses=tr.matched_clauses,
        exclusions_found=tr.exclusions_found,
        risk_flags=tr.risk_flags,
        missing_info=tr.missing_info,
        confidence=tr.confidence,
        handler_recommendation=tr.handler_recommendation,
        auto_approval_eligible=tr.auto_approval_eligible,
    )


def _fnol_response(claim: Claim, messages: list[str]) -> FNOLResponse:
    return FNOLResponse(
        claim_id=claim.id,
        triage_result=_triage_response(claim),
        handler_approved=claim.handler_approved,
        current_stage=claim.current_stage.value,
        messages=messages,
    )

def _kpi_response(claim: Claim) -> KPIStatusResponse:
    return KPIStatusResponse(
        overall_status=claim.kpi_status.overall_status.value,
        stage_kpis=[
            KPIEntryResponse(
                stage=e.stage.value,
                compliance_status=e.compliance_status.value,
                max_hours_allowed=e.max_hours_allowed,
                elapsed_hours=e.elapsed_hours,
                message=e.message,
            )
            for e in claim.kpi_status.stage_kpis
        ],
        days_elapsed_total=claim.kpi_status.days_elapsed_total,
        at_risk=claim.kpi_status.at_risk,
        ai_forecast=claim.kpi_status.ai_forecast,
    )


def _claim_summary(claim: Claim) -> ClaimSummaryResponse:
    return ClaimSummaryResponse(
        id=claim.id,
        policy_number=claim.claimant.policy_number,
        vehicle=f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
        current_stage=claim.current_stage.value,
        status=claim.status.value,
        coverage_decision=claim.coverage_decision.value,
        fecha_aviso=claim.fecha_aviso,
        fecha_cierre=claim.fecha_cierre,
        kpi_status=_kpi_response(claim),
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


def _claim_detail(claim: Claim) -> ClaimDetailResponse:
    return ClaimDetailResponse(
        **_claim_summary(claim).model_dump(),
        agent_analyses=[
            AgentAnalysisResponse(
                agent_type=a.agent_type,
                stage=a.stage.value,
                ran_at=a.ran_at,
                recommendation=a.recommendation,
                confidence=a.confidence,
            )
            for a in claim.agent_analyses
        ],
        notifications_sent_count=len(claim.notifications_sent),
        stage_history_count=len(claim.stage_history),
        work_order_number=claim.work_order.number if claim.work_order else None,
        invoice_number=claim.invoice_number,
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/claims", response_model=AdvanceStageResponse, status_code=201)
async def create_claim(request: CreateClaimRequest):
    """Create a new insurance claim (FNOL) and process the Claim Report stage."""
    claim = Claim(
        claimant=ClaimantInfo(**request.claimant.model_dump()),
        vehicle=VehicleInfo(**request.vehicle.model_dump()),
    )
    db.save(claim)

    try:
        result = await _workflow.advance(claim, data={})
    except Exception as e:
        logger.exception("Failed to process claim report stage")
        raise HTTPException(status_code=500, detail=str(e))

    db.save(result.updated_claim)

    return AdvanceStageResponse(
        success=result.success,
        claim_id=claim.id,
        previous_stage=WorkflowStage.CLAIM_REPORT.value,
        current_stage=result.updated_claim.current_stage.value,
        next_stage=result.next_stage.value if result.next_stage else None,
        messages=result.messages,
        agent_analysis=AgentAnalysisResponse(
            agent_type=result.agent_analysis.agent_type,
            stage=result.agent_analysis.stage.value,
            ran_at=result.agent_analysis.ran_at,
            recommendation=result.agent_analysis.recommendation,
            confidence=result.agent_analysis.confidence,
        ) if result.agent_analysis else None,
        kpi_status=_kpi_response(result.updated_claim),
    )


@router.get("/claims", response_model=list[ClaimSummaryResponse])
async def list_claims():
    """List all claims with summary information."""
    return [_claim_summary(c) for c in db.list_all()]


@router.get("/claims/{claim_id}", response_model=ClaimDetailResponse)
async def get_claim(claim_id: str):
    """Get full claim details including agent analyses and stage history."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return _claim_detail(claim)


@router.post("/claims/{claim_id}/advance", response_model=AdvanceStageResponse)
async def advance_claim(claim_id: str, request: AdvanceStageRequest):
    """Advance the claim to the next stage, invoking the appropriate AI agents."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if claim.current_stage == WorkflowStage.COMPLETED:
        raise HTTPException(status_code=400, detail="Claim is already completed")

    previous_stage = claim.current_stage

    try:
        result = await _workflow.advance(claim, data=request.data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Failed to advance claim %s", claim_id)
        raise HTTPException(status_code=500, detail=str(e))

    db.save(result.updated_claim)

    return AdvanceStageResponse(
        success=result.success,
        claim_id=claim_id,
        previous_stage=previous_stage.value,
        current_stage=result.updated_claim.current_stage.value,
        next_stage=result.next_stage.value if result.next_stage else None,
        messages=result.messages,
        agent_analysis=AgentAnalysisResponse(
            agent_type=result.agent_analysis.agent_type,
            stage=result.agent_analysis.stage.value,
            ran_at=result.agent_analysis.ran_at,
            recommendation=result.agent_analysis.recommendation,
            confidence=result.agent_analysis.confidence,
        ) if result.agent_analysis else None,
        kpi_status=_kpi_response(result.updated_claim),
    )


@router.get("/claims/{claim_id}/kpi", response_model=KPIStatusResponse)
async def get_claim_kpi(claim_id: str):
    """Get the real-time KPI compliance status for a claim."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return _kpi_response(claim)


@router.get("/claims/{claim_id}/timeline", response_model=list[dict])
async def get_claim_timeline(claim_id: str):
    """Get the full event timeline for a claim."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    timeline = []
    for transition in claim.stage_history:
        timeline.append({
            "event": "stage_transition",
            "to_stage": transition.to_stage.value,
            "timestamp": transition.transitioned_at.isoformat(),
            "notes": transition.notes,
        })
    for analysis in claim.agent_analyses:
        timeline.append({
            "event": "ai_analysis",
            "agent_type": analysis.agent_type,
            "stage": analysis.stage.value,
            "timestamp": analysis.ran_at.isoformat(),
            "recommendation": analysis.recommendation[:200] if analysis.recommendation else "",
        })
    timeline.sort(key=lambda x: x["timestamp"])
    return timeline


@router.get("/workflow", response_model=WorkflowInfoResponse)
async def get_workflow_info():
    """Get information about the full workflow including all stages and KPIs."""
    stages_info = _workflow.get_all_stages_info()
    return WorkflowInfoResponse(
        stages=[StageInfoResponse(**s) for s in stages_info],
        total_process_days=TOTAL_PROCESS_DAYS,
        description=(
            "ClearProcess: AI-powered vehicle insurance claim workflow. "
            "10 stages from FNOL to billing, with AI agents at damage assessment, "
            "coverage analysis, spare parts sourcing, and KPI monitoring."
        ),
    )


# ── Work Order helper ─────────────────────────────────────────────────────────

def _wo_response(claim: Claim) -> WorkOrderResponse | None:
    wo = claim.work_order
    if wo is None:
        return None
    return WorkOrderResponse(
        number=wo.number,
        phase=wo.phase,
        workshop_name=wo.workshop_name,
        line_items=[
            WorkOrderLineItemResponse(
                item_number=i.item_number,
                description=i.description,
                desmontar_montar=i.desmontar_montar,
                cambiar=i.cambiar,
                valor_repuesto=i.valor_repuesto,
                reparar_leve=i.reparar_leve,
                reparar_mediano=i.reparar_mediano,
                reparar_grave=i.reparar_grave,
                pintar=i.pintar,
                trabajo_externo=i.trabajo_externo,
                ai_recommendation=i.ai_recommendation,
                ai_reason=i.ai_reason,
                handler_approved=i.handler_approved,
                workshop_matched_description=i.workshop_matched_description,
                match_confidence=i.match_confidence,
                is_unapproved_alert=i.is_unapproved_alert,
            )
            for i in wo.line_items
        ],
        summary=WorkOrderSummaryResponse(
            trabajo_externo=wo.summary.trabajo_externo,
            reparacion_hours=wo.summary.reparacion_hours,
            reparacion=wo.summary.reparacion,
            pintura_hours=wo.summary.pintura_hours,
            pintura=wo.summary.pintura,
            repuestos=wo.summary.repuestos,
            subtotal_neto=wo.summary.subtotal_neto,
            deducible_neto=wo.summary.deducible_neto,
            depreciacion_neto=wo.summary.depreciacion_neto,
            total_compania_neto=wo.summary.total_compania_neto,
        ) if wo.summary else None,
        unapproved_alerts=wo.unapproved_alerts,
    )


# ── FNOL Triage endpoints ──────────────────────────────────────────────────────

@router.post("/claims/fnol", response_model=FNOLResponse, status_code=201)
async def submit_fnol(request: FNOLSubmissionRequest):
    """Submit a full FNOL (First Notice of Loss) with optional policy PDF.

    The FNOLTriageAgent analyzes the claim against the policy and returns a
    preliminary coverage decision to the handler.
    - If confidence >= 90%, covered, and no risk flags → auto-approved and ready for workshop.
    - Otherwise → pending handler review via POST /claims/{id}/approve-triage.
    """
    # Build domain models from request
    claim = Claim(
        claimant=ClaimantInfo(**request.claimant.model_dump()),
        vehicle=VehicleInfo(**request.vehicle.model_dump()),
    )
    db.save(claim)

    # Convert FNOLDataRequest → FNOLData (same fields, just convert nested part groups)
    fnol_dict = request.fnol.model_dump()
    fnol_dict["damaged_parts"] = [
        DamagedPartGroup(zone=g["zone"], parts=g["parts"])
        for g in fnol_dict.get("damaged_parts", [])
    ]
    fnol_data = FNOLData(**{k: v for k, v in fnol_dict.items() if k != "damaged_parts"},
                         damaged_parts=fnol_dict["damaged_parts"])

    # Decode policy PDF if provided
    pdf_bytes: bytes | None = None
    if request.policy_pdf_base64:
        try:
            pdf_bytes = base64.b64decode(request.policy_pdf_base64)
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid base64 in policy_pdf_base64")

    # Run Claim Report stage with FNOL data + optional PDF
    try:
        result = await _workflow.advance(
            claim,
            data={"fnol_data": fnol_data, "policy_pdf_bytes": pdf_bytes},
        )
    except Exception as e:
        logger.exception("FNOL processing failed for claim %s", claim.id)
        raise HTTPException(status_code=500, detail=str(e))

    updated = result.updated_claim
    db.save(updated)

    return _fnol_response(updated, result.messages)


@router.post("/claims/{claim_id}/approve-triage", response_model=FNOLResponse)
async def approve_triage(claim_id: str, request: HandlerApprovalRequest):
    """Handler approves or rejects the triage result.

    - approved=True → claim advances to Vehicle Intake (workshop dispatch).
    - approved=False → claim is rejected; no workshop dispatch.
    """
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if claim.triage_result is None:
        raise HTTPException(
            status_code=422,
            detail="No triage result on this claim. Submit FNOL via POST /claims/fnol first."
        )

    if claim.handler_approved is not None:
        # Already decided — re-decision is allowed (handler can override)
        pass

    claim.handler_approved = request.approved
    claim.handler_notes = request.notes

    messages = [
        f"Handler {'APPROVED' if request.approved else 'REJECTED'} triage for claim {claim_id}",
    ]
    if request.notes:
        messages.append(f"Handler notes: {request.notes}")

    if request.approved and claim.current_stage == WorkflowStage.CLAIM_REPORT:
        # Advance to Vehicle Intake now that the handler has approved
        try:
            result = await _workflow.process_stage(
                claim, WorkflowStage.VEHICLE_INTAKE, data={}
            )
            db.save(result.updated_claim)
            messages.extend(result.messages)
            messages.append("Claim dispatched to workshop (Vehicle Intake stage)")
            return _fnol_response(result.updated_claim, messages)
        except Exception as e:
            logger.exception("Failed to advance to vehicle intake after handler approval")
            # Don't fail the approval itself — just note the issue
            messages.append(f"Warning: could not auto-advance to Vehicle Intake: {e}")
    elif not request.approved:
        claim.status = ClaimStatus.REJECTED
        messages.append("Claim rejected — no workshop dispatch")

    db.save(claim)
    return _fnol_response(claim, messages)


@router.post("/claims/{claim_id}/upload-policy", response_model=FNOLResponse)
async def upload_policy(claim_id: str, policy_pdf: UploadFile = File(...)):
    """Upload (or re-upload) the policy PDF and re-run the FNOL triage agent.

    Use this when the policy was not available at FNOL submission time,
    or to re-triage after a policy correction.

    Accepts a multipart file upload (Content-Type: multipart/form-data).
    """
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if claim.fnol_data is None:
        raise HTTPException(
            status_code=422,
            detail="No FNOL data on this claim. Submit via POST /claims/fnol first."
        )

    if policy_pdf.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=422,
            detail=f"Expected a PDF file, got: {policy_pdf.content_type}"
        )

    pdf_bytes = await policy_pdf.read()

    try:
        triage_agent = FNOLTriageAgent()
        triage_result = await triage_agent.run_triage(
            fnol_data=claim.fnol_data,
            pdf_bytes=pdf_bytes,
        )
    except Exception as e:
        logger.exception("Re-triage failed for claim %s", claim_id)
        raise HTTPException(status_code=500, detail=str(e))

    claim.triage_result = triage_result

    from models.claim import AgentAnalysis
    analysis = AgentAnalysis(
        agent_type="fnol_triage",
        stage=claim.current_stage,
        result=triage_result.model_dump(mode="json"),
        recommendation=triage_result.handler_recommendation,
        confidence=triage_result.confidence,
    )
    claim.agent_analyses.append(analysis)

    messages = [
        f"Policy PDF uploaded — triage re-run for claim {claim_id}",
        f"Decision: {triage_result.preliminary_decision.value.upper()} (confidence {triage_result.confidence:.0%})",
    ]
    if triage_result.auto_approval_eligible:
        claim.handler_approved = True
        messages.append("✅ AUTO-APPROVED after policy upload")
    else:
        claim.handler_approved = None
        messages.append("⏳ Awaiting handler review — use POST /claims/{id}/approve-triage")

    db.save(claim)
    return _fnol_response(claim, messages)


# ── Work Order endpoints (Phase 1: analyze, Phase 2: review, Phase 3: budget) ─

@router.post("/claims/{claim_id}/work-order/analyze", response_model=WOReviewResponse)
async def analyze_wo_photos(
    claim_id: str,
    photos: list[UploadFile] = File(...),
    workshop_name: str = "Authorized Repair Center",
    estimated_completion_days: int = 10,
):
    """Phase 1: Upload workshop damage photos → AI creates preliminary WO draft.

    Accepts multiple photos (JPEG/PNG/WebP) as multipart file upload.
    Returns the preliminary WO with AI coverage recommendation per line item.
    Handler must then review via POST /claims/{id}/work-order/review.
    """
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if claim.current_stage not in (WorkflowStage.WORK_ORDER_CREATION, WorkflowStage.SPARE_PARTS_PURCHASE):
        raise HTTPException(
            status_code=422,
            detail=f"Work order analysis requires stage WORK_ORDER_CREATION, current: {claim.current_stage.value}"
        )

    photo_bytes_list = [await photo.read() for photo in photos]

    try:
        result = await _workflow.advance(
            claim,
            data={
                "photo_bytes_list": photo_bytes_list,
                "workshop_name": workshop_name,
                "estimated_completion_days": estimated_completion_days,
            },
        )
    except Exception as e:
        logger.exception("WO photo analysis failed for claim %s", claim_id)
        raise HTTPException(status_code=500, detail=str(e))

    updated = result.updated_claim
    db.save(updated)
    wo = _wo_response(updated)

    pending = sum(1 for i in wo.line_items if i.handler_approved is None) if wo else 0
    return WOReviewResponse(
        claim_id=claim_id,
        work_order=wo,
        approved_count=0,
        rejected_count=0,
        pending_count=pending,
        messages=result.messages,
    )


@router.post("/claims/{claim_id}/work-order/review", response_model=WOReviewResponse)
async def review_wo_line_items(claim_id: str, request: HandlerWOReviewRequest):
    """Phase 2: Handler approves or rejects individual WO line items.

    Submit decisions for each item (item_number + approved:bool).
    After review, upload the workshop budget via POST /claims/{id}/work-order/budget.
    """
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if claim.work_order is None or not claim.work_order.line_items:
        raise HTTPException(
            status_code=422,
            detail="No WO line items found. Run photo analysis first."
        )

    review_map = {r.item_number: r for r in request.reviews}
    approved = rejected = still_pending = 0

    for item in claim.work_order.line_items:
        review = review_map.get(item.item_number)
        if review:
            item.handler_approved = review.approved
            approved += review.approved
            rejected += not review.approved
        else:
            if item.handler_approved is None:
                still_pending += 1
            elif item.handler_approved:
                approved += 1
            else:
                rejected += 1

    claim.work_order.phase = "handler_reviewed"
    db.save(claim)

    wo = _wo_response(claim)
    messages = [
        f"Handler review saved: {approved} approved, {rejected} rejected, {still_pending} pending",
    ]
    if still_pending > 0:
        messages.append(f"ℹ️ {still_pending} items still pending review")
    if approved > 0:
        messages.append("Ready for budget upload → POST /claims/{id}/work-order/budget")

    return WOReviewResponse(
        claim_id=claim_id,
        work_order=wo,
        approved_count=approved,
        rejected_count=rejected,
        pending_count=still_pending,
        messages=messages,
    )


@router.post("/claims/{claim_id}/work-order/budget", response_model=WOBudgetResponse)
async def upload_workshop_budget(
    claim_id: str,
    budget_files: list[UploadFile] = File(...),
    deductible: float = 0.0,
):
    """Phase 3: Upload workshop budget (PDF/images) → AI reconciles against approved WO.

    The reconciliation agent:
    - Fuzzy-matches workshop item names to approved preliminary WO items
    - Fills in actual costs from the budget
    - Flags workshop items not in the approved WO (is_unapproved_alert=True, but still included)
    - Calculates WorkOrderSummary totals
    """
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if claim.work_order is None or not claim.work_order.line_items:
        raise HTTPException(status_code=422, detail="No WO line items found. Complete Phase 1 and 2 first.")

    budget_bytes_list = [await f.read() for f in budget_files]

    try:
        result = await _workflow.process_stage(
            claim,
            WorkflowStage.SPARE_PARTS_PURCHASE,
            data={"budget_files": budget_bytes_list, "deductible": deductible},
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("WO budget reconciliation failed for claim %s", claim_id)
        raise HTTPException(status_code=500, detail=str(e))

    updated = result.updated_claim
    db.save(updated)
    wo = _wo_response(updated)

    return WOBudgetResponse(
        claim_id=claim_id,
        work_order=wo,
        unapproved_alerts=updated.work_order.unapproved_alerts if updated.work_order else [],
        messages=result.messages,
    )


@router.get("/claims/{claim_id}/work-order", response_model=WorkOrderResponse)
async def get_work_order(claim_id: str):
    """Get the current work order state (any phase: draft / handler_reviewed / final)."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    wo = _wo_response(claim)
    if wo is None:
        raise HTTPException(status_code=404, detail="No work order on this claim yet")
    return wo


# ── NEW Customer-facing and workflow endpoints ──────────────────────────────────

@router.get("/customer/claim/{claim_id}")
async def get_customer_claim_info(claim_id: str):
    """Returns minimal customer-safe claim info."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    triage_decision = None
    if claim.triage_result and hasattr(claim.triage_result, 'preliminary_decision'):
        triage_decision = claim.triage_result.preliminary_decision.value

    rejection_notes = None
    if claim.handler_approved is False and claim.handler_notes:
        rejection_notes = claim.handler_notes

    return {
        "claim_id": claim.id,
        "status": claim.status.value,
        "handler_approved": claim.handler_approved,
        "documents_requested_types": claim.documents_requested_types,
        "vehicle_at_workshop": claim.vehicle_at_workshop,
        "repair_started": claim.repair_started,
        "ready_for_pickup": claim.ready_for_pickup,
        "customer_accepted_repair": claim.customer_accepted_repair,
        "payment_completed": claim.payment_completed,
        "deductible_amount": claim.deductible_amount,
        "triage_decision": triage_decision,
        "coverage_decision": claim.coverage_decision.value if claim.coverage_decision else None,
        "current_stage": claim.current_stage.value,
        "rejection_notes": rejection_notes,
    }


@router.post("/claims/{claim_id}/request-documents")
async def request_documents(claim_id: str, request: RequestDocumentsRequest):
    """Request additional documents from customer."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    claim.documents_requested_types = request.types
    claim.documents_requested_at = datetime.utcnow()
    claim.status = ClaimStatus.WAITING_FOR_DOCUMENTS
    claim.handler_approved = None

    db.save(claim)

    return {
        "claim_id": claim.id,
        "types_requested": request.types,
        "message": "Documentos adicionales solicitados al asegurado",
    }


@router.post("/claims/{claim_id}/submit-documents")
async def submit_documents(claim_id: str, files: list[UploadFile] = File(...)):
    """Submit additional documents for the claim."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    # Save uploaded files
    for file in files:
        save_upload(claim_id, file, "additional_docs")

    # Load policy PDF if exists
    policy_bytes = None
    policy_paths = list_files(claim_id, "policy")
    if policy_paths:
        policy_bytes = policy_paths[0].read_bytes()

    # Add doc metadata
    if not claim.additional_documents:
        claim.additional_documents = []

    for file in files:
        claim.additional_documents.append({
            "name": file.filename,
            "category": "additional_docs",
            "uploaded_at": datetime.utcnow().isoformat(),
        })

    # Re-run triage if FNOL data exists
    if claim.fnol_data is not None:
        try:
            triage_agent = FNOLTriageAgent()
            triage_result = await triage_agent.run_triage(
                fnol_data=claim.fnol_data,
                pdf_bytes=policy_bytes,
            )
            claim.triage_result = triage_result
        except Exception as e:
            logger.exception("Triage re-run failed after document submission for claim %s", claim_id)

    claim.handler_approved = None
    claim.status = ClaimStatus.ACTIVE
    db.save(claim)

    return {
        "claim_id": claim.id,
        "files_uploaded": len(files),
        "triage_rerun": True,
        "message": "Documentos recibidos. El ajustador revisará tu caso nuevamente.",
    }


@router.post("/claims/{claim_id}/vehicle-submitted")
async def vehicle_submitted(claim_id: str):
    """Register vehicle submission to workshop."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    claim.vehicle_at_workshop = True
    claim.vehicle_at_workshop_at = datetime.utcnow()
    db.save(claim)

    return {
        "claim_id": claim.id,
        "message": "Vehículo registrado en el taller",
    }


@router.post("/claims/{claim_id}/workshop-inspection")
async def workshop_inspection(claim_id: str, photos: list[UploadFile] = File(...)):
    """Submit workshop inspection photos and generate work order draft."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    # Save photos
    photo_bytes_list = []
    for photo in photos:
        save_upload(claim_id, photo, "inspection")
        photo_bytes_list.append(await photo.read())

    # Run work order draft agent
    try:
        draft_agent = WorkOrderDraftAgent()
        context = {
            "vehicle": f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}",
            "damage": ", ".join([p.zone for group in claim.fnol_data.damaged_parts for p in [group.zone]]) if claim.fnol_data else "",
        }
        line_items = await draft_agent.run_with_photos(
            photo_bytes_list=photo_bytes_list,
            context=context,
        )
    except Exception as e:
        logger.exception("Work order draft generation failed for claim %s", claim_id)
        raise HTTPException(status_code=500, detail=str(e))

    # Create or update work order
    if claim.work_order is None:
        claim.work_order = WorkOrder()

    claim.work_order.phase = "draft"
    claim.work_order.line_items = line_items
    claim.workshop_photos_count = len(photos)

    # Set workflow stage if needed
    if claim.current_stage not in (WorkflowStage.WORK_ORDER_CREATION, WorkflowStage.SPARE_PARTS_PURCHASE):
        claim.current_stage = WorkflowStage.WORK_ORDER_CREATION

    # Generate WO number if not set
    if not claim.work_order.number:
        claim.work_order.number = f"WO-{datetime.utcnow().strftime('%Y%m')}-{str(uuid.uuid4())[:6].upper()}"

    db.save(claim)

    return {
        "claim_id": claim.id,
        "work_order": _wo_response(claim),
        "message": "Fotos analizadas. OT preliminar generada.",
    }


@router.post("/claims/{claim_id}/repair-started")
async def repair_started(claim_id: str):
    """Mark repair as started."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    claim.repair_started = True
    claim.repair_started_at = datetime.utcnow()
    db.save(claim)

    return {
        "claim_id": claim.id,
        "message": "Reparación iniciada. Se notificará al asegurado.",
    }


@router.post("/claims/{claim_id}/ready-for-pickup")
async def ready_for_pickup(claim_id: str):
    """Mark vehicle ready for customer pickup."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    claim.ready_for_pickup = True
    claim.ready_for_pickup_at = datetime.utcnow()
    db.save(claim)

    return {
        "claim_id": claim.id,
        "message": "Vehículo listo para retiro. Se notificará al asegurado.",
    }


@router.post("/claims/{claim_id}/accept-repair")
async def accept_repair(claim_id: str):
    """Customer accepts repair."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    claim.customer_accepted_repair = True
    claim.customer_accepted_at = datetime.utcnow()
    db.save(claim)

    return {
        "claim_id": claim.id,
        "deductible_amount": claim.deductible_amount,
        "message": "Reparación aceptada por el asegurado.",
    }


@router.post("/claims/{claim_id}/upload-final-bill")
async def upload_final_bill(claim_id: str, bill_files: list[UploadFile] = File(...)):
    """Upload final bill and run comparison against approved work order."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    # Save bill files
    bill_bytes_list = []
    for bill_file in bill_files:
        save_upload(claim_id, bill_file, "final_bill")
        bill_bytes_list.append(await bill_file.read())

    # Get approved WO items
    approved_items = []
    if claim.work_order and claim.work_order.line_items:
        approved_items = [
            item for item in claim.work_order.line_items
            if item.handler_approved is not False
        ]

    # Run bill comparison
    try:
        comparison_agent = BillComparisonAgent()
        comparison_result = await comparison_agent.run_comparison(
            bill_files=bill_bytes_list,
            wo_line_items=approved_items,
        )
        claim.bill_comparison_result = comparison_result
    except Exception as e:
        logger.exception("Bill comparison failed for claim %s", claim_id)
        raise HTTPException(status_code=500, detail=str(e))

    db.save(claim)

    return {
        "claim_id": claim.id,
        "comparison": claim.bill_comparison_result,
        "message": "Factura procesada. Comparación generada.",
    }


@router.post("/claims/{claim_id}/complete-payment")
async def complete_payment(claim_id: str):
    """Complete payment and close claim."""
    claim = db.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    claim.payment_completed = True
    claim.payment_completed_at = datetime.utcnow()
    claim.status = ClaimStatus.COMPLETED
    db.save(claim)

    return {
        "claim_id": claim.id,
        "message": "Pago registrado. Siniestro cerrado.",
    }
