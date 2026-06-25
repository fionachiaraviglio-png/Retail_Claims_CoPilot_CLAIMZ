from enum import Enum


class WorkflowStage(str, Enum):
    CLAIM_REPORT = "claim_report"
    VEHICLE_INTAKE = "vehicle_intake"
    DAMAGE_INSPECTION = "damage_inspection"
    COVERAGE_ANALYSIS = "coverage_analysis"
    WORK_ORDER_CREATION = "work_order_creation"
    SPARE_PARTS_PURCHASE = "spare_parts_purchase"
    REPAIR_PROCESS = "repair_process"
    WORK_ORDER_CLOSURE = "work_order_closure"
    VEHICLE_DELIVERY = "vehicle_delivery"
    CUSTOMER_APPROVAL_BILLING = "customer_approval_billing"
    COMPLETED = "completed"


class ClaimStatus(str, Enum):
    ACTIVE = "active"
    HANDLER_APPROVED = "handler_approved"
    WAITING_FOR_DOCUMENTS = "waiting_for_documents"  # handler requested additional docs
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    COMPLETED = "completed"


class CoverageDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PARTIAL = "partial"


class DamageSeverity(str, Enum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    TOTAL_LOSS = "total_loss"


class KPIComplianceStatus(str, Enum):
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    NOT_APPLICABLE = "not_applicable"


class TriageDecision(str, Enum):
    COVERED = "covered"               # claim appears covered — proceed to workshop
    NOT_COVERED = "not_covered"       # clear exclusion or non-covered peril
    CONDITIONAL = "conditional"       # covered but additional info required
    REQUIRES_REVIEW = "requires_review"  # complex case — human must decide


# Maps each stage to its workflow field name used in external systems
STAGE_FIELD_MAP: dict[WorkflowStage, str] = {
    WorkflowStage.CLAIM_REPORT: "Fecha_Aviso",
    WorkflowStage.VEHICLE_INTAKE: "Fecha_Ingreso_Taller",
    WorkflowStage.DAMAGE_INSPECTION: "Fecha_Inspeccion",
    WorkflowStage.WORK_ORDER_CREATION: "Fecha_Creacion_OT",
    WorkflowStage.WORK_ORDER_CLOSURE: "Fecha_Cierre_OT",
    WorkflowStage.VEHICLE_DELIVERY: "Fecha_Salida_Taller",
    WorkflowStage.CUSTOMER_APPROVAL_BILLING: "Fecha_Cierre",
}

# KPI definitions: max hours from previous stage (None = no KPI)
STAGE_KPI_HOURS: dict[WorkflowStage, int | None] = {
    WorkflowStage.CLAIM_REPORT: None,
    WorkflowStage.VEHICLE_INTAKE: 240,          # 10 calendar days
    WorkflowStage.DAMAGE_INSPECTION: 48,         # 48 hours
    WorkflowStage.COVERAGE_ANALYSIS: 24,         # 24 hours if approved
    WorkflowStage.WORK_ORDER_CREATION: 120,      # 5 days
    WorkflowStage.SPARE_PARTS_PURCHASE: 24,      # 24 hours for quotation
    WorkflowStage.REPAIR_PROCESS: None,
    WorkflowStage.WORK_ORDER_CLOSURE: 240,       # 10 days from WO creation
    WorkflowStage.VEHICLE_DELIVERY: None,
    WorkflowStage.CUSTOMER_APPROVAL_BILLING: 24,  # 24 hours from delivery
    WorkflowStage.COMPLETED: None,
}

TOTAL_PROCESS_DAYS = 45

STAGE_ORDER = [
    WorkflowStage.CLAIM_REPORT,
    WorkflowStage.VEHICLE_INTAKE,
    WorkflowStage.DAMAGE_INSPECTION,
    WorkflowStage.COVERAGE_ANALYSIS,
    WorkflowStage.WORK_ORDER_CREATION,
    WorkflowStage.SPARE_PARTS_PURCHASE,
    WorkflowStage.REPAIR_PROCESS,
    WorkflowStage.WORK_ORDER_CLOSURE,
    WorkflowStage.VEHICLE_DELIVERY,
    WorkflowStage.CUSTOMER_APPROVAL_BILLING,
    WorkflowStage.COMPLETED,
]
