from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
import uuid

from .enums import (
    WorkflowStage,
    ClaimStatus,
    CoverageDecision,
    DamageSeverity,
    KPIComplianceStatus,
    TriageDecision,
)


class DamagedPartGroup(BaseModel):
    """Maps to the visual car-part selector in the FNOL form.
    Zones: frontal, trasera, lateral_derecha, lateral_izquierda, zona_interior, otros
    """
    zone: str
    parts: list[str] = Field(default_factory=list)


class FNOLData(BaseModel):
    """All fields captured in the customer FNOL (First Notice of Loss) form.
    Matches the screens: Datos generales denuncio + Antecedentes del accidente.
    """
    claim_type: str = "accident_damage"   # "accident_damage" | "robbery" | "assistance"

    # Reporter (may differ from policy holder)
    reporter_is_policy_holder: bool = True
    reporter_relationship: str | None = None   # "Familiar", "Conductor", "Otro"
    reporter_name: str
    reporter_rut: str                           # Chilean national ID (RUT)
    reporter_document_number: str | None = None
    reporter_phone: str
    reporter_email: str

    # Vehicle identification
    license_plate: str                          # Patente — used to look up the insured vehicle

    # Damaged parts (from the visual selector — Frontal/Trasera/etc.)
    damaged_parts: list[DamagedPartGroup] = Field(default_factory=list)

    # Incident location
    incident_street: str = ""
    incident_number: str = ""
    incident_region: str = ""
    incident_commune: str = ""

    # Incident time
    incident_date: str = ""    # ISO date string: "2026-06-18"
    incident_time: str = ""    # "14:30"

    # Narrative
    incident_description: str = ""    # max 3000 chars

    # Evidence
    photos_count: int = 0

    # Circumstances
    insured_was_driver: bool | None = None
    third_party_involved: bool | None = None
    police_attended: bool | None = None
    police_report_filed: bool | None = None
    police_report_number: str | None = None


class TriageResult(BaseModel):
    """Preliminary coverage assessment produced by the FNOLTriageAgent."""
    ran_at: datetime = Field(default_factory=datetime.utcnow)
    preliminary_decision: TriageDecision = TriageDecision.REQUIRES_REVIEW
    matched_clauses: list[str] = Field(default_factory=list)    # policy clauses that support coverage
    exclusions_found: list[str] = Field(default_factory=list)   # exclusions that may apply
    risk_flags: list[str] = Field(default_factory=list)         # inconsistencies / fraud indicators
    missing_info: list[str] = Field(default_factory=list)       # additional info needed
    confidence: float = 0.0
    handler_recommendation: str = ""
    auto_approval_eligible: bool = False   # True when confidence >= 0.90, no risk flags, decision=COVERED


class VehicleInfo(BaseModel):
    make: str
    model: str
    year: int
    license_plate: str
    vin: str | None = None
    color: str | None = None


class ClaimantInfo(BaseModel):
    name: str
    phone: str
    email: str
    policy_number: str
    address: str | None = None


class DamageInfo(BaseModel):
    severity: DamageSeverity = DamageSeverity.MODERATE
    description: str = ""
    affected_areas: list[str] = Field(default_factory=list)
    estimated_cost_usd: float | None = None
    photos_submitted: bool = False
    ai_assessment: str | None = None
    repair_recommendations: list[str] = Field(default_factory=list)


class WorkOrderLineItem(BaseModel):
    """One line in the Descripción de los trabajos table.
    All cost columns are intentionally None in the preliminary draft —
    they are filled in during the reconciliation phase (Phase 3).
    Amounts are in the local currency (CLP for Chilean claims).
    """
    item_number: int
    description: str

    # Sustituir group
    desmontar_montar: float | None = None   # Desmontar y Montar
    cambiar: float | None = None            # Cambiar
    valor_repuesto: float | None = None     # Valor Repuesto (part cost)

    # Reparar group
    reparar_leve: float | None = None
    reparar_mediano: float | None = None
    reparar_grave: float | None = None

    # Other columns
    pintar: float | None = None
    trabajo_externo: float | None = None

    # AI recommendation (Phase 1 output)
    ai_recommendation: str | None = None    # "cover" | "do_not_cover"
    ai_reason: str | None = None

    # Handler decision (Phase 2)
    handler_approved: bool | None = None    # None = pending, True/False after review

    # Reconciliation metadata (Phase 3)
    workshop_matched_description: str | None = None  # name the workshop used
    match_confidence: float | None = None            # 0-1 semantic similarity
    is_unapproved_alert: bool = False                # workshop charged but not in approved WO


class WorkOrderSummary(BaseModel):
    """Footer totals row — populated in Phase 3 from workshop actual costs."""
    trabajo_externo: float = 0.0
    reparacion_hours: float = 0.0
    reparacion: float = 0.0
    pintura_hours: float = 0.0
    pintura: float = 0.0
    repuestos: float = 0.0
    subtotal_neto: float = 0.0
    deducible_neto: float = 0.0
    depreciacion_neto: float = 0.0
    total_compania_neto: float = 0.0


class WorkOrder(BaseModel):
    number: str = ""
    created_at: datetime | None = None
    workshop_name: str = ""
    estimated_completion_days: int | None = None

    # Phase tracking: "initial" → "draft" → "handler_reviewed" → "final"
    phase: str = "initial"

    # Structured line items (Phase 1 adds items, Phase 2 adds handler_approved, Phase 3 adds costs)
    line_items: list[WorkOrderLineItem] = Field(default_factory=list)
    summary: WorkOrderSummary | None = None

    # Alerts from Phase 3 reconciliation (workshop items not in approved preliminary WO)
    unapproved_alerts: list[str] = Field(default_factory=list)

    # Legacy aggregate cost fields — kept for compatibility with other stages
    labor_cost_usd: float | None = None
    parts_cost_usd: float | None = None
    total_cost_usd: float | None = None
    closed_at: datetime | None = None


class KPIEntry(BaseModel):
    stage: WorkflowStage
    compliance_status: KPIComplianceStatus
    max_hours_allowed: int | None
    elapsed_hours: float | None
    message: str


class KPIStatus(BaseModel):
    overall_status: KPIComplianceStatus = KPIComplianceStatus.ON_TRACK
    stage_kpis: list[KPIEntry] = Field(default_factory=list)
    days_elapsed_total: float = 0.0
    predicted_completion_date: datetime | None = None
    at_risk: bool = False
    ai_forecast: str | None = None


class StageTransition(BaseModel):
    from_stage: WorkflowStage | None
    to_stage: WorkflowStage
    transitioned_at: datetime = Field(default_factory=datetime.utcnow)
    transitioned_by: str = "system"
    notes: str | None = None


class AgentAnalysis(BaseModel):
    agent_type: str
    stage: WorkflowStage
    ran_at: datetime = Field(default_factory=datetime.utcnow)
    result: dict[str, Any] = Field(default_factory=dict)
    recommendation: str = ""
    confidence: float | None = None


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claimant: ClaimantInfo
    vehicle: VehicleInfo
    current_stage: WorkflowStage = WorkflowStage.CLAIM_REPORT
    status: ClaimStatus = ClaimStatus.ACTIVE

    # Stage timestamps (mirrors the external workflow fields)
    fecha_aviso: datetime | None = None           # Claim report
    fecha_ingreso_taller: datetime | None = None  # Vehicle intake
    fecha_inspeccion: datetime | None = None      # Damage inspection
    fecha_creacion_ot: datetime | None = None     # Work order creation
    fecha_cierre_ot: datetime | None = None       # Work order closure
    fecha_salida_taller: datetime | None = None   # Vehicle delivery
    fecha_cierre: datetime | None = None          # Final closure

    # Workflow data
    damage: DamageInfo | None = None
    coverage_decision: CoverageDecision = CoverageDecision.PENDING
    coverage_notes: str | None = None
    work_order: WorkOrder | None = None
    repair_notes: str | None = None
    acceptance_receipt_signed: bool = False
    invoice_number: str | None = None

    # Tracking
    kpi_status: KPIStatus = Field(default_factory=KPIStatus)
    stage_history: list[StageTransition] = Field(default_factory=list)
    agent_analyses: list[AgentAnalysis] = Field(default_factory=list)
    notifications_sent: list[dict[str, Any]] = Field(default_factory=list)

    # FNOL triage (pre-workshop gate)
    fnol_data: FNOLData | None = None
    triage_result: TriageResult | None = None
    handler_approved: bool | None = None   # None=pending, True=approved, False=rejected
    handler_notes: str | None = None

    # Document request loop
    documents_requested_types: list[str] = Field(default_factory=list)  # ["photos","police_report","documents"]
    documents_requested_at: datetime | None = None
    additional_documents: list[dict] = Field(default_factory=list)  # [{name, category, path, uploaded_at}]

    # Workshop milestones
    vehicle_at_workshop: bool = False
    vehicle_at_workshop_at: datetime | None = None
    workshop_photos_count: int = 0
    repair_started: bool = False
    repair_started_at: datetime | None = None
    ready_for_pickup: bool = False
    ready_for_pickup_at: datetime | None = None
    customer_accepted_repair: bool = False
    customer_accepted_at: datetime | None = None
    deductible_amount: float = 0.0

    # Final billing
    bill_comparison_result: dict | None = None
    payment_completed: bool = False
    payment_completed_at: datetime | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
