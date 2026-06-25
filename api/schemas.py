"""FastAPI request/response schemas for the ClearProcess API."""
from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from models.enums import WorkflowStage, DamageSeverity


# ── Request schemas ────────────────────────────────────────────────────────────

class VehicleInfoRequest(BaseModel):
    make: str
    model: str
    year: int
    license_plate: str
    vin: str | None = None
    color: str | None = None


class ClaimantInfoRequest(BaseModel):
    name: str
    phone: str
    email: str
    policy_number: str
    address: str | None = None


class CreateClaimRequest(BaseModel):
    claimant: ClaimantInfoRequest
    vehicle: VehicleInfoRequest


class AdvanceStageRequest(BaseModel):
    """Generic advance request — data varies by stage."""
    data: dict[str, Any] = Field(default_factory=dict)


class DamageInspectionRequest(BaseModel):
    damage_description: str
    affected_areas: list[str] = Field(default_factory=list)
    photos_submitted: bool = False
    vehicle_market_value: float = 20000.0


class CoverageAnalysisRequest(BaseModel):
    incident_circumstances: str = "Collision"
    deductible_usd: float = 500.0


class VehicleIntakeRequest(BaseModel):
    workshop_name: str = "Authorized Repair Center"


class WorkOrderRequest(BaseModel):
    labor_cost_usd: float = 800.0
    estimated_completion_days: int = 10


class VehicleDeliveryRequest(BaseModel):
    acceptance_receipt_signed: bool = True


class BillingRequest(BaseModel):
    invoice_number: str | None = None
    final_cost_usd: float | None = None


# ── FNOL / Triage request schemas ─────────────────────────────────────────────

class DamagedPartGroupRequest(BaseModel):
    """One zone from the visual car-part selector (Frontal / Trasera / etc.)."""
    zone: str
    parts: list[str] = Field(default_factory=list)


class FNOLDataRequest(BaseModel):
    """All fields from the customer FNOL digital form (Datos generales + Antecedentes)."""
    claim_type: str = "accident_damage"   # "accident_damage" | "robbery" | "assistance"

    # Datos generales denuncio
    reporter_is_policy_holder: bool = True
    reporter_relationship: str | None = None    # "Familiar", "Conductor", "Otro"
    reporter_name: str
    reporter_rut: str
    reporter_document_number: str | None = None
    reporter_phone: str
    reporter_email: str
    license_plate: str

    # Antecedentes del accidente
    damaged_parts: list[DamagedPartGroupRequest] = Field(default_factory=list)
    incident_street: str = ""
    incident_number: str = ""
    incident_region: str = ""
    incident_commune: str = ""
    incident_date: str = ""
    incident_time: str = ""
    incident_description: str = ""
    photos_count: int = 0
    insured_was_driver: bool | None = None
    third_party_involved: bool | None = None
    police_attended: bool | None = None
    police_report_filed: bool | None = None
    police_report_number: str | None = None


class FNOLSubmissionRequest(BaseModel):
    """Full FNOL submission: claimant info + vehicle info + form fields + optional policy PDF."""
    claimant: ClaimantInfoRequest
    vehicle: VehicleInfoRequest
    fnol: FNOLDataRequest
    # Base64-encoded PDF of the policy document uploaded by the handler.
    # If omitted, the triage agent will still run but with lower confidence
    # (and will flag missing policy as a missing_info item).
    policy_pdf_base64: str | None = None


class HandlerApprovalRequest(BaseModel):
    """Handler decision on the triage result before workshop dispatch."""
    approved: bool
    notes: str | None = None


# ── Response schemas ───────────────────────────────────────────────────────────

class StageInfoResponse(BaseModel):
    stage: str
    kpi_hours: int | None
    field_name: str | None
    stage_index: int
    total_stages: int


class KPIEntryResponse(BaseModel):
    stage: str
    compliance_status: str
    max_hours_allowed: int | None
    elapsed_hours: float | None
    message: str


class KPIStatusResponse(BaseModel):
    overall_status: str
    stage_kpis: list[KPIEntryResponse]
    days_elapsed_total: float
    at_risk: bool
    ai_forecast: str | None


class AgentAnalysisResponse(BaseModel):
    agent_type: str
    stage: str
    ran_at: datetime
    recommendation: str
    confidence: float | None


class ClaimSummaryResponse(BaseModel):
    id: str
    policy_number: str
    vehicle: str
    current_stage: str
    status: str
    coverage_decision: str
    fecha_aviso: datetime | None
    fecha_cierre: datetime | None
    kpi_status: KPIStatusResponse
    created_at: datetime
    updated_at: datetime


class ClaimDetailResponse(ClaimSummaryResponse):
    agent_analyses: list[AgentAnalysisResponse]
    notifications_sent_count: int
    stage_history_count: int
    work_order_number: str | None
    invoice_number: str | None


class AdvanceStageResponse(BaseModel):
    success: bool
    claim_id: str
    previous_stage: str
    current_stage: str
    next_stage: str | None
    messages: list[str]
    agent_analysis: AgentAnalysisResponse | None
    kpi_status: KPIStatusResponse


class WorkflowInfoResponse(BaseModel):
    stages: list[StageInfoResponse]
    total_process_days: int
    description: str


# ── Work Order schemas ─────────────────────────────────────────────────────────

class WorkOrderLineItemResponse(BaseModel):
    item_number: int
    description: str
    desmontar_montar: float | None
    cambiar: float | None
    valor_repuesto: float | None
    reparar_leve: float | None
    reparar_mediano: float | None
    reparar_grave: float | None
    pintar: float | None
    trabajo_externo: float | None
    ai_recommendation: str | None
    ai_reason: str | None
    handler_approved: bool | None
    workshop_matched_description: str | None
    match_confidence: float | None
    is_unapproved_alert: bool


class WorkOrderSummaryResponse(BaseModel):
    trabajo_externo: float
    reparacion_hours: float
    reparacion: float
    pintura_hours: float
    pintura: float
    repuestos: float
    subtotal_neto: float
    deducible_neto: float
    depreciacion_neto: float
    total_compania_neto: float


class WorkOrderResponse(BaseModel):
    number: str
    phase: str
    workshop_name: str
    line_items: list[WorkOrderLineItemResponse]
    summary: WorkOrderSummaryResponse | None
    unapproved_alerts: list[str]


class HandlerLineItemReview(BaseModel):
    """Single line item decision from the handler."""
    item_number: int
    approved: bool
    notes: str | None = None


class HandlerWOReviewRequest(BaseModel):
    """Phase 2: handler approves/rejects individual line items."""
    reviews: list[HandlerLineItemReview]


class WOAnalyzeRequest(BaseModel):
    """Phase 1 via JSON (base64 photos) — alternative to multipart upload."""
    photo_base64_list: list[str]
    workshop_name: str | None = None
    estimated_completion_days: int = 10


class WOReviewResponse(BaseModel):
    claim_id: str
    work_order: WorkOrderResponse
    approved_count: int
    rejected_count: int
    pending_count: int
    messages: list[str]


class WOBudgetResponse(BaseModel):
    claim_id: str
    work_order: WorkOrderResponse
    unapproved_alerts: list[str]
    messages: list[str]


class TriageResultResponse(BaseModel):
    preliminary_decision: str
    matched_clauses: list[str]
    exclusions_found: list[str]
    risk_flags: list[str]
    missing_info: list[str]
    confidence: float
    handler_recommendation: str
    auto_approval_eligible: bool


class FNOLResponse(BaseModel):
    """Response from POST /claims/fnol and POST /claims/{id}/upload-policy."""
    claim_id: str
    triage_result: TriageResultResponse | None
    handler_approved: bool | None
    current_stage: str
    messages: list[str]
