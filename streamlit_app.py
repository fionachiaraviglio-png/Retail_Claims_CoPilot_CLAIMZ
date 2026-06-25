"""ClearProcess Streamlit UI.

Run with:
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import uuid
from datetime import datetime
from typing import Any

import anthropic
import pandas as pd
from pydantic import ValidationError as PydanticValidationError
import streamlit as st
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from agents.bill_comparison_agent import BillComparisonAgent
from agents.fnol_triage_agent import FNOLTriageAgent
from config.settings import settings
from core.workflow import ClaimWorkflow
from storage import get_dir
from database import db
from models.claim import (AgentAnalysis, Claim, ClaimantInfo, DamageInfo,
                          FNOLData, VehicleInfo)
from models.enums import (STAGE_ORDER, ClaimStatus, CoverageDecision,
                          WorkflowStage)

st.set_page_config(page_title="ClearProcess", page_icon="CP", layout="wide")


def get_workflow() -> ClaimWorkflow:
    return ClaimWorkflow()


def run_async(coro):
    """Run async stage processing from Streamlit callbacks."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _has_likely_valid_anthropic_key() -> bool:
    api_key = (settings.anthropic_api_key or "").strip()
    return api_key.startswith("sk-ant-") and len(api_key) >= 40


def _extract_pdf_text(cp_pdf_content: bytes) -> str:
    reader = PdfReader(io.BytesIO(cp_pdf_content))
    text_parts: list[str] = []
    for page in reader.pages:
        text_parts.append(page.extract_text() or "")
    return _normalize_text(" ".join(text_parts))


def _find_first(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _normalize_text(match.group(1))
    return None


def _parse_optional_json(raw_text: str, label: str) -> dict[str, Any]:
    if not raw_text.strip():
        return {}
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} invalido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} debe ser un objeto JSON.")
    return parsed


def _extracted_fields_to_map(payload: dict[str, Any]) -> dict[str, str]:
    extracted_fields = payload.get("extracted_fields")
    if not isinstance(extracted_fields, list):
        return {}

    normalized: dict[str, str] = {}
    for entry in extracted_fields:
        if not isinstance(entry, dict):
            continue
        field_name = str(entry.get("field") or "").strip().lower()
        field_value = str(entry.get("value") or "").strip()
        if field_name:
            normalized[field_name] = field_value
    return normalized


def _claim_json_from_template(payload: dict[str, Any]) -> dict[str, Any]:
    fields = _extracted_fields_to_map(payload)
    if not fields:
        return payload

    return {
        "incident_date": fields.get("date of incident", ""),
        "incident_time": "",
        "incident_description": (
            fields.get("damage to insured vehicle")
            or payload.get("summary", "")
        ),
        "reporter_name": fields.get("claimant name", "") or fields.get("name", ""),
        "reporter_phone": "",
        "reporter_email": "",
        "reporter_rut": "",
        "license_plate": fields.get("license plate", ""),
        "make": fields.get("vehicle make", ""),
        "model": fields.get("vehicle model", ""),
        "year": fields.get("year", ""),
        "color": fields.get("color", ""),
        "vin": fields.get("vin", "") or fields.get("chassis", ""),
        "policy_number": fields.get("policy number", ""),
        "full_text": payload.get("full_text", ""),
        "source_template": payload.get("document_type", ""),
    }


def _policy_json_from_template(payload: dict[str, Any]) -> dict[str, Any]:
    fields = _extracted_fields_to_map(payload)
    if not fields:
        return payload

    return {
        "policy_number": fields.get("policy number", "") or fields.get("item number", ""),
        "insured_name": fields.get("insured name", "") or fields.get("name", ""),
        "insurance_company": fields.get("insurance company", ""),
        "broker_name": fields.get("broker name", ""),
        "coverage_start": fields.get("coverage start", ""),
        "coverage_end": fields.get("coverage end", ""),
        "deductible": fields.get("plan / deductible", ""),
        "license_plate": fields.get("license plate", ""),
        "vin": fields.get("vin", "") or fields.get("chassis", ""),
        "vehicle_make": fields.get("vehicle make", ""),
        "vehicle_model": fields.get("vehicle model", ""),
        "vehicle_year": fields.get("year", ""),
        "full_text": payload.get("full_text", ""),
        "source_template": payload.get("document_type", ""),
    }


def _extract_claim_pdf_json(text: str) -> dict[str, Any]:
    incident_date_pattern = (
        r"(?:fecha(?: del)? siniestro|fecha)\s*[:\-]\s*"
        r"([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})"
    )
    extracted = {
        "reporter_name": _find_first(
            [r"(?:reportante|denunciante|nombre)\s*[:\-]\s*([^\n\r,;]{3,80})"],
            text,
        ),
        "reporter_rut": _find_first([r"(?:rut|dni)\s*[:\-]\s*([0-9kK.\-]{7,15})"], text),
        "reporter_phone": _find_first(
            [r"(?:telefono|tel[eé]fono|celular|movil)\s*[:\-]\s*([+0-9\s\-]{7,20})"],
            text,
        ),
        "reporter_email": _find_first(
            [r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"],
            text,
        ),
        "license_plate": _find_first(
            [r"(?:patente|placa|matr[ií]cula)\s*[:\-]\s*([A-Za-z0-9\-]{5,12})"],
            text,
        ),
        "incident_date": _find_first(
            [incident_date_pattern],
            text,
        ),
        "incident_time": _find_first(
            [r"(?:hora(?: del)? siniestro|hora)\s*[:\-]\s*([0-9]{1,2}:[0-9]{2})"],
            text,
        ),
        "incident_description": _find_first(
            [r"(?:descripci[oó]n|relato|detalle)\s*[:\-]\s*([^\n\r]{10,500})"],
            text,
        ),
    }
    extracted["raw_text_excerpt"] = text[:900]
    extracted["extraction_timestamp_utc"] = datetime.utcnow().isoformat()
    return extracted


def _extract_policy_pdf_json(text: str) -> dict[str, Any]:
    policy_number_pattern = (
        r"(?:p[oó]liza|policy(?:\s+number)?)\s*"
        r"(?:n[°o]?\.?|number)?\s*[:\-]\s*([A-Za-z0-9\-]{4,40})"
    )
    coverage_start_pattern = (
        r"(?:vigencia desde|inicio de vigencia|desde)\s*[:\-]\s*"
        r"([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})"
    )
    coverage_end_pattern = (
        r"(?:vigencia hasta|fin de vigencia|hasta)\s*[:\-]\s*"
        r"([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})"
    )
    extracted = {
        "policy_number": _find_first(
            [policy_number_pattern],
            text,
        ),
        "insured_name": _find_first(
            [r"(?:asegurado|titular)\s*[:\-]\s*([^\n\r,;]{3,120})"],
            text,
        ),
        "insured_rut": _find_first([r"(?:rut|dni)\s*[:\-]\s*([0-9kK.\-]{7,15})"], text),
        "license_plate": _find_first(
            [r"(?:patente|placa|matr[ií]cula)\s*[:\-]\s*([A-Za-z0-9\-]{5,12})"],
            text,
        ),
        "vin": _find_first([r"(?:vin|chasis)\s*[:\-]\s*([A-Za-z0-9]{8,25})"], text),
        "coverage_start": _find_first(
            [coverage_start_pattern],
            text,
        ),
        "coverage_end": _find_first(
            [coverage_end_pattern],
            text,
        ),
    }
    extracted["raw_text_excerpt"] = text[:900]
    extracted["extraction_timestamp_utc"] = datetime.utcnow().isoformat()
    return extracted



def _get_policy_pdf_bytes_from_claim(claim_record: Claim) -> bytes | None:
    for stored_doc in reversed(claim_record.additional_documents):
        if stored_doc.get("category") != "policy_pdf_extraction":
            continue
        encoded = stored_doc.get("pdf_base64")
        if not encoded:
            continue
        try:
            return base64.b64decode(encoded)
        except (ValueError, TypeError):
            continue
    return None


def _render_triage_result(cp_triage_outcome) -> None:
    decision = cp_triage_outcome.preliminary_decision.value
    confidence = cp_triage_outcome.confidence

    DECISION_STYLE = {
        "covered":        ("CUBIERTO",          "#28a745", "✅"),
        "not_covered":    ("NO CUBIERTO",        "#dc3545", "❌"),
        "conditional":    ("CONDICIONAL",        "#fd7e14", "⚠️"),
        "requires_review":("REQUIERE REVISION",  "#6c757d", "🔍"),
    }
    label, color, icon = DECISION_STYLE.get(decision, ("DESCONOCIDO", "#6c757d", "❓"))

    if cp_triage_outcome.auto_approval_eligible:
        st.success(
            "✅ **Elegible para aprobacion automatica.** "
            "El agente recomienda aprobar directamente."
        )

    st.markdown(
        f'<div style="background:{color}18; border-left:5px solid {color}; '
        f'padding:12px 16px; border-radius:6px; margin:8px 0 12px 0;">'
        f'<span style="font-size:22px; vertical-align:middle;">{icon}</span>&nbsp;'
        f'<span style="color:{color}; font-size:19px; font-weight:700; vertical-align:middle;">'
        f'{label}</span>'
        f'<span style="color:{color}; font-size:13px; margin-left:14px; vertical-align:middle;">'
        f'{confidence:.0%} confianza</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if cp_triage_outcome.handler_recommendation:
        st.info(cp_triage_outcome.handler_recommendation)

    if cp_triage_outcome.matched_clauses:
        with st.expander(f"✅ {len(cp_triage_outcome.matched_clauses)} clausula(s) cubierta(s)"):
            for clause in cp_triage_outcome.matched_clauses:
                st.markdown(f"✅ {clause}")

    if cp_triage_outcome.exclusions_found:
        with st.expander(f"❌ {len(cp_triage_outcome.exclusions_found)} exclusion(es) detectada(s)"):
            for exclusion in cp_triage_outcome.exclusions_found:
                st.markdown(f"❌ {exclusion}")

    if cp_triage_outcome.risk_flags:
        with st.expander(f"⚠️ {len(cp_triage_outcome.risk_flags)} alerta(s) de riesgo"):
            for flag in cp_triage_outcome.risk_flags:
                st.markdown(f"⚠️ {flag}")

    if cp_triage_outcome.missing_info:
        with st.expander(f"📋 {len(cp_triage_outcome.missing_info)} dato(s) faltante(s)"):
            for item in cp_triage_outcome.missing_info:
                st.markdown(f"📋 {item}")



def _vehicle_workshop_status_label(claim_record: Claim) -> str:
    if claim_record.status == ClaimStatus.REJECTED:
        return "Rechazado por el ajustador"
    if claim_record.status == ClaimStatus.WAITING_FOR_DOCUMENTS:
        return "Pendiente de documentos del asegurado"
    if claim_record.ready_for_pickup:
        return "Listo para retiro"
    if claim_record.repair_started:
        return "En reparacion"
    if claim_record.vehicle_at_workshop:
        return "Vehiculo en taller"
    if claim_record.status == ClaimStatus.HANDLER_APPROVED:
        return "Aprobado por el ajustador"
    return "Pendiente de decision"


def _work_type_label(cp_line_item) -> str:
    if cp_line_item.cambiar is not None:
        return "Sustituir / Cambiar"
    if cp_line_item.desmontar_montar is not None:
        return "Sustituir / Desmontar y Montar"
    if cp_line_item.reparar_leve is not None:
        return "Reparar / Leve"
    if cp_line_item.reparar_mediano is not None:
        return "Reparar / Mediano"
    if cp_line_item.reparar_grave is not None:
        return "Reparar / Grave"
    if cp_line_item.pintar is not None:
        return "Pintar"
    if cp_line_item.trabajo_externo is not None:
        return "Trabajo Externo"
    return "Pendiente de clasificacion"


def _decision_label(handler_approved: bool | None) -> str:
    if handler_approved is True:
        return "SI"
    if handler_approved is False:
        return "NO"
    return "SI"


def _save_uploads_to_disk(claim_id: str, files: list[tuple[str, bytes]]) -> None:
    folder = get_dir(claim_id)
    for filename, content in files:
        (folder / filename).write_bytes(content)


def _latest_doc_json(claim_record: Claim, category: str) -> dict[str, Any]:
    for stored_doc in reversed(claim_record.additional_documents):
        if stored_doc.get("category") != category:
            continue
        extracted_json = stored_doc.get("extracted_json")
        if isinstance(extracted_json, dict):
            return extracted_json
    return {}


# ---- app bootstrap ----

workflow = get_workflow()

st.session_state.setdefault("page", "dashboard")
st.session_state.setdefault("selected_claim_id", None)
st.session_state.setdefault("view_mode", "handler")


def navigate_to(page: str, claim_id: str | None = None) -> None:
    st.session_state.page = page
    if claim_id is not None:
        st.session_state.selected_claim_id = claim_id
    st.rerun()


with st.sidebar:
    st.subheader("Controles")
    if st.button("Refrescar datos", use_container_width=True):
        st.rerun()
    st.divider()
    st.write("Base de datos:", "clearprocess.db")


CUSTOMER_STAGE_LABELS: dict[WorkflowStage, str] = {
    WorkflowStage.CLAIM_REPORT: "Apertura del siniestro",
    WorkflowStage.VEHICLE_INTAKE: "Ingreso al taller",
    WorkflowStage.DAMAGE_INSPECTION: "Inspeccion de danos",
    WorkflowStage.COVERAGE_ANALYSIS: "Analisis de cobertura",
    WorkflowStage.WORK_ORDER_CREATION: "Creacion de orden de trabajo",
    WorkflowStage.SPARE_PARTS_PURCHASE: "Compra de repuestos",
    WorkflowStage.REPAIR_PROCESS: "Reparacion en curso",
    WorkflowStage.WORK_ORDER_CLOSURE: "Cierre de la reparacion",
    WorkflowStage.VEHICLE_DELIVERY: "Entrega del vehiculo",
    WorkflowStage.CUSTOMER_APPROVAL_BILLING: "Aprobacion y facturacion",
    WorkflowStage.COMPLETED: "Siniestro completado",
}

# ---- mode toggle (rendered on every page) ----

_tgl_col, _ = st.columns([2, 6])
with _tgl_col:
    _is_customer = st.session_state.view_mode == "customer"
    _new_val = st.toggle("Vista del cliente", value=_is_customer, key="view_mode_toggle")
    if _new_val != _is_customer:
        st.session_state.view_mode = "customer" if _new_val else "handler"
        if st.session_state.page != "claim_detail":
            st.session_state.page = "dashboard"
        st.rerun()


# ---- DASHBOARD ----

def render_dashboard() -> None:
    col_title, col_btn = st.columns([5, 1])
    col_title.title("ClearProcess")
    if col_btn.button("+ Nuevo Siniestro", use_container_width=True, type="primary"):
        navigate_to("new_claim")

    claim_records = db.list_all()
    total = len(claim_records)
    active = sum(1 for c in claim_records if c.status == ClaimStatus.ACTIVE)
    pending_docs = sum(1 for c in claim_records if c.status == ClaimStatus.WAITING_FOR_DOCUMENTS)
    completed = sum(1 for c in claim_records if c.status == ClaimStatus.COMPLETED)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", total)
    m2.metric("Activos", active)
    m3.metric("Pendiente docs", pending_docs)
    m4.metric("Completados", completed)

    if not claim_records:
        st.info("No hay siniestros cargados aun. Crea uno con '+ Nuevo Siniestro'.")
        return

    # Header row
    hcols = st.columns([1, 2, 2, 2, 1, 2, 1])
    for col, label in zip(hcols, ["ID", "Poliza", "Vehiculo", "Patente", "Etapa", "Estado", ""]):
        col.markdown(f"**{label}**")
    st.divider()

    for c in claim_records:
        cols = st.columns([1, 2, 2, 2, 1, 2, 1])
        cols[0].write(c.id[:8])
        cols[1].write(c.claimant.policy_number or "—")
        cols[2].write(f"{c.vehicle.year} {c.vehicle.make} {c.vehicle.model}")
        cols[3].write(c.vehicle.license_plate)
        cols[4].write(c.current_stage.value)
        cols[5].write(c.status.value)
        if cols[6].button("Ver →", key=f"view_{c.id}"):
            navigate_to("claim_detail", c.id)


# ---- NEW CLAIM ----

def render_new_claim() -> None:
    if st.button("← Volver al dashboard"):
        navigate_to("dashboard")

    st.subheader("Alta de siniestro")

    st.session_state.setdefault("creating_claim", False)
    st.session_state.setdefault("claim_form_data", {})

    # ── Phase 2: processing — replace the form with a full spinner ────
    if st.session_state.creating_claim:
        with st.spinner("Procesando siniestro con IA, por favor espera..."):
            fd = st.session_state.claim_form_data
            try:
                claim_json_payload_raw = fd.get("claim_json_payload_raw", {})
                policy_json_payload_raw = fd.get("policy_json_payload_raw", {})
                claim_json_payload = _claim_json_from_template(claim_json_payload_raw)
                policy_json_payload = _policy_json_from_template(policy_json_payload_raw)

                claim_pdf_bytes: bytes | None = fd.get("claim_pdf_bytes")
                claim_pdf_name: str = fd.get("claim_pdf_name", "siniestro.pdf")
                policy_pdf_bytes: bytes | None = fd.get("policy_pdf_bytes")
                policy_pdf_name: str = fd.get("policy_pdf_name", "poliza.pdf")

                claim_pdf_json: dict[str, Any] = (
                    _extract_claim_pdf_json(_extract_pdf_text(claim_pdf_bytes))
                    if claim_pdf_bytes else {}
                )
                policy_pdf_json: dict[str, Any] = (
                    _extract_policy_pdf_json(_extract_pdf_text(policy_pdf_bytes))
                    if policy_pdf_bytes else {}
                )

                merged_claim_json = {**claim_pdf_json, **claim_json_payload}
                merged_policy_json = {**policy_pdf_json, **policy_json_payload}

                claimant_name = (
                    merged_claim_json.get("reporter_name")
                    or merged_policy_json.get("insured_name")
                    or "Asegurado"
                )
                claimant = ClaimantInfo(
                    name=claimant_name,
                    phone=merged_claim_json.get("reporter_phone") or "",
                    email=merged_claim_json.get("reporter_email") or "",
                    policy_number=(
                        merged_policy_json.get("policy_number")
                        or merged_claim_json.get("policy_number")
                        or ""
                    ),
                    address=merged_claim_json.get("address") or None,
                )
                vehicle = VehicleInfo(
                    make=merged_claim_json.get("make") or "Desconocido",
                    model=merged_claim_json.get("model") or "Desconocido",
                    year=int(merged_claim_json.get("year") or 2024),
                    license_plate=(
                        merged_claim_json.get("license_plate")
                        or merged_policy_json.get("license_plate")
                        or "UNKNOWN"
                    ),
                    vin=merged_claim_json.get("vin") or merged_policy_json.get("vin") or None,
                    color=merged_claim_json.get("color") or None,
                )
                claim = Claim(claimant=claimant, vehicle=vehicle)
                initial_data: dict[str, Any] = {}

                if claimant.policy_number:
                    db.save_policy(claimant.policy_number, {
                        "policy_number": claimant.policy_number,
                        "claimant_name": claimant.name,
                        "claimant_phone": claimant.phone,
                        "claimant_email": claimant.email,
                        "vehicle": {
                            "make": vehicle.make, "model": vehicle.model,
                            "year": vehicle.year, "license_plate": vehicle.license_plate,
                            "vin": vehicle.vin,
                        },
                        **merged_policy_json,
                    })

                extracted_docs: list[dict[str, Any]] = []
                if claim_pdf_bytes:
                    extracted_docs.append({
                        "name": claim_pdf_name, "category": "claim_pdf_extraction",
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "extracted_json": claim_pdf_json,
                        "pdf_base64": base64.b64encode(claim_pdf_bytes).decode("ascii"),
                    })
                if policy_pdf_bytes:
                    extracted_docs.append({
                        "name": policy_pdf_name, "category": "policy_pdf_extraction",
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "extracted_json": policy_pdf_json,
                        "pdf_base64": base64.b64encode(policy_pdf_bytes).decode("ascii"),
                    })
                if claim_json_payload_raw:
                    extracted_docs.append({
                        "name": "claim_json_input", "category": "claim_json_input",
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "extracted_json": claim_json_payload_raw,
                    })
                if policy_json_payload_raw:
                    extracted_docs.append({
                        "name": "policy_json_input", "category": "policy_json_input",
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "extracted_json": policy_json_payload_raw,
                    })
                claim = claim.model_copy(update={"additional_documents": extracted_docs})

                extracted_fnol = FNOLData(
                    reporter_name=merged_claim_json.get("reporter_name") or claimant.name,
                    reporter_rut=merged_claim_json.get("reporter_rut") or "N/A",
                    reporter_phone=merged_claim_json.get("reporter_phone") or claimant.phone,
                    reporter_email=merged_claim_json.get("reporter_email") or claimant.email,
                    license_plate=merged_claim_json.get("license_plate") or vehicle.license_plate,
                    incident_date=merged_claim_json.get("incident_date") or "",
                    incident_time=merged_claim_json.get("incident_time") or "",
                    incident_description=(merged_claim_json.get("incident_description") or ""),
                    photos_count=0,
                )
                initial_data["fnol_data"] = extracted_fnol.model_dump(mode="json")
                initial_data["policy_pdf_bytes"] = policy_pdf_bytes
                initial_data["policy_json"] = merged_policy_json
                initial_data["run_fnol_triage"] = False

                upload_files = []
                if claim_pdf_bytes:
                    upload_files.append((claim_pdf_name, claim_pdf_bytes))
                if policy_pdf_bytes:
                    upload_files.append((policy_pdf_name, policy_pdf_bytes))
                if upload_files:
                    _save_uploads_to_disk(claim.id, upload_files)

                db.save(claim)
                result = run_async(workflow.advance(claim, data=initial_data))
                db.save(result.updated_claim)

                st.session_state.creating_claim = False
                st.session_state.claim_form_data = {}
                navigate_to("claim_detail", result.updated_claim.id)

            except anthropic.AuthenticationError:
                st.session_state.creating_claim = False
                st.error(
                    "Error de autenticacion con Anthropic (401 invalid x-api-key). "
                    "Actualiza ANTHROPIC_API_KEY en .env y reinicia Streamlit."
                )
            except PydanticValidationError as exc:
                st.session_state.creating_claim = False
                st.error(f"Error de validacion de datos: {exc}")
            except (ValueError, TypeError, RuntimeError, OSError, PdfReadError) as exc:
                st.session_state.creating_claim = False
                st.error(f"No se pudo crear el siniestro: {exc}")
        return

    # ── Phase 1: show the form ────────────────────────────────────────
    with st.form("create_claim_form"):
        st.markdown("### Subida de PDFs")
        claim_pdf = st.file_uploader(
            "PDF del Siniestro",
            type=["pdf"],
            help="Documento con la declaracion o antecedentes del siniestro.",
        )
        policy_pdf = st.file_uploader(
            "PDF de la Poliza",
            type=["pdf"],
            help="Documento de poliza para validar coberturas.",
        )

        st.markdown("### Entrada JSON (opcional)")
        claim_json_input = st.text_area(
            "JSON del Siniestro",
            value="",
            height=140,
            help="Puedes pegar un objeto JSON con informacion del siniestro.",
        )
        policy_json_input = st.text_area(
            "JSON de la Poliza",
            value="",
            height=140,
            help="Puedes pegar un objeto JSON con informacion de la poliza.",
        )

        submitted = st.form_submit_button(
            "Crear y procesar etapa inicial",
            use_container_width=True,
            type="primary",
        )

    if submitted:
        try:
            claim_json_payload_raw = _parse_optional_json(claim_json_input, "JSON del Siniestro")
            policy_json_payload_raw = _parse_optional_json(policy_json_input, "JSON de la Poliza")
            claim_json_payload = _claim_json_from_template(claim_json_payload_raw)
            policy_json_payload = _policy_json_from_template(policy_json_payload_raw)

            if claim_pdf is None and not claim_json_payload:
                st.error("Debes ingresar siniestro por PDF o por JSON.")
                st.stop()
            if policy_pdf is None and not policy_json_payload:
                st.error("Debes ingresar poliza por PDF o por JSON.")
                st.stop()

            # Store form data in session state, then rerun so the form
            # is replaced by the spinner on the next render.
            st.session_state.claim_form_data = {
                "claim_pdf_bytes": claim_pdf.getvalue() if claim_pdf else None,
                "claim_pdf_name": claim_pdf.name if claim_pdf else "siniestro.pdf",
                "policy_pdf_bytes": policy_pdf.getvalue() if policy_pdf else None,
                "policy_pdf_name": policy_pdf.name if policy_pdf else "poliza.pdf",
                "claim_json_payload_raw": claim_json_payload_raw,
                "policy_json_payload_raw": policy_json_payload_raw,
            }
            st.session_state.creating_claim = True
            st.rerun()

        except (ValueError, TypeError, PdfReadError) as exc:
            st.error(f"No se pudo iniciar la creacion: {exc}")


# ---- WORK ORDER helpers ----

def _fmt_amount(val: float | None) -> str:
    return f"${val:,.0f}" if val is not None else "—"


def _calc_damage_severity(line_items: list) -> str:
    if any((item.reparar_grave or 0) > 0 for item in line_items):
        return "Grave"
    if any((item.reparar_mediano or 0) > 0 for item in line_items):
        return "Mediano"
    return "Leve"


def _render_wo_table(line_items: list) -> None:
    rows = []
    for item in line_items:
        amounts = [
            item.desmontar_montar, item.cambiar, item.valor_repuesto,
            item.reparar_leve, item.reparar_mediano, item.reparar_grave,
            item.pintar, item.trabajo_externo,
        ]
        total = sum(v for v in amounts if v is not None)
        rows.append({
            "":           "⚠️" if getattr(item, "is_unapproved_alert", False) else "",
            "Descripcion": item.description,
            "Tipo":        _work_type_label(item),
            "D&M":         _fmt_amount(item.desmontar_montar),
            "Cambiar":     _fmt_amount(item.cambiar),
            "Val.Repuesto": _fmt_amount(item.valor_repuesto),
            "Leve":        _fmt_amount(item.reparar_leve),
            "Mediano":     _fmt_amount(item.reparar_mediano),
            "Grave":       _fmt_amount(item.reparar_grave),
            "Pintar":      _fmt_amount(item.pintar),
            "T.Externo":   _fmt_amount(item.trabajo_externo),
            "Total":       _fmt_amount(total if total > 0 else None),
            "IA Rec.":     item.ai_recommendation or "—",
            "Aprobado":    _decision_label(item.handler_approved),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---- CLAIM DETAIL ----

def render_claim_detail(claim_id: str) -> None:
    if st.button("← Volver al dashboard"):
        navigate_to("dashboard")

    selected_claim = db.get(claim_id)
    if selected_claim is None:
        st.error("No se pudo cargar el siniestro.")
        return

    st.subheader(
        f"#{selected_claim.id[:8]} — {selected_claim.claimant.name}"
        f" — {selected_claim.vehicle.license_plate}"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Etapa actual", selected_claim.current_stage.value)
    c2.metric("Estado", selected_claim.status.value)
    c3.metric("Analisis AI", len(selected_claim.agent_analyses))

    stage_idx = STAGE_ORDER.index(selected_claim.current_stage)
    progress = min((stage_idx + 1) / len(STAGE_ORDER), 1.0)
    st.progress(progress)

    triage_tab, adjuster_tab, workshop_tab_ui, work_order_tab_ui, docs_tab_h, policy_tab = st.tabs([
        "Triage", "Decision Ajustador", "Taller", "Orden de Trabajo", "Documentacion", "Poliza",
    ])

    # ---- Triage ----
    with triage_tab:
        extracted_docs = [
            d for d in selected_claim.additional_documents
            if d.get("category") in {"claim_pdf_extraction", "policy_pdf_extraction"}
        ]
        if extracted_docs:
            st.markdown("### JSON extraidos desde PDFs")
            for doc in extracted_docs:
                title = f"{doc.get('category')} - {doc.get('name', 'archivo')}"
                with st.expander(title, expanded=False):
                    st.json(doc.get("extracted_json", {}))

        with st.expander("Detalle completo del siniestro", expanded=False):
            st.json(selected_claim.model_dump(mode="json"))

        st.markdown("### FNOL Triage")

        if selected_claim.fnol_data is None:
            st.info("Este claim no tiene FNOL cargado, no se puede ejecutar triage.")
            default_claim_json = _latest_doc_json(selected_claim, "claim_json_input")
            default_policy_json = _latest_doc_json(selected_claim, "policy_json_input")

            fnol_json_text = st.text_area(
                "Entrada 1 - JSON del Siniestro",
                value=(
                    json.dumps(default_claim_json, indent=2, ensure_ascii=False)
                    if default_claim_json else ""
                ),
                height=160,
                key=f"manual_fnol_json_{selected_claim.id}",
            )
            policy_json_text = st.text_area(
                "Entrada 2 - JSON de la Poliza",
                value=(
                    json.dumps(default_policy_json, indent=2, ensure_ascii=False)
                    if default_policy_json else ""
                ),
                height=160,
                key=f"manual_policy_json_{selected_claim.id}",
            )

            if st.button(
                "Ejecutar FNOLTriageAgent con 2 entradas",
                key=f"run_triage_manual_inputs_{selected_claim.id}",
                use_container_width=True,
            ):
                try:
                    claim_json_payload = _parse_optional_json(
                        fnol_json_text, "Entrada JSON del Siniestro"
                    )
                    policy_json_payload = _parse_optional_json(
                        policy_json_text, "Entrada JSON de la Poliza"
                    )

                    if not claim_json_payload:
                        st.error("La entrada JSON del Siniestro es obligatoria.")
                        st.stop()

                    selected_claim.fnol_data = FNOLData(
                        reporter_name=(
                            claim_json_payload.get("reporter_name")
                            or selected_claim.claimant.name
                        ),
                        reporter_rut=claim_json_payload.get("reporter_rut") or "N/A",
                        reporter_phone=(
                            claim_json_payload.get("reporter_phone")
                            or selected_claim.claimant.phone
                        ),
                        reporter_email=(
                            claim_json_payload.get("reporter_email")
                            or selected_claim.claimant.email
                        ),
                        license_plate=(
                            claim_json_payload.get("license_plate")
                            or selected_claim.vehicle.license_plate
                        ),
                        incident_date=claim_json_payload.get("incident_date") or "",
                        incident_time=claim_json_payload.get("incident_time") or "",
                        incident_description=(
                            claim_json_payload.get("incident_description") or ""
                        ),
                        photos_count=int(claim_json_payload.get("photos_count") or 0),
                    )

                    existing_docs = selected_claim.model_dump(mode="json").get(
                        "additional_documents"
                    )
                    if not isinstance(existing_docs, list):
                        existing_docs = []
                    existing_docs.append(
                        {
                            "name": "manual_claim_json_input",
                            "category": "claim_json_input",
                            "uploaded_at": datetime.utcnow().isoformat(),
                            "extracted_json": claim_json_payload,
                        }
                    )
                    if policy_json_payload:
                        existing_docs.append(
                            {
                                "name": "manual_policy_json_input",
                                "category": "policy_json_input",
                                "uploaded_at": datetime.utcnow().isoformat(),
                                "extracted_json": policy_json_payload,
                            }
                        )
                    selected_claim.additional_documents = existing_docs

                    policy_pdf_bytes_manual = _get_policy_pdf_bytes_from_claim(selected_claim)
                    stored_policy_json = (
                        db.get_policy(selected_claim.claimant.policy_number)
                        if selected_claim.claimant.policy_number
                        else None
                    )
                    merged_manual_policy = {
                        **(stored_policy_json or {}),
                        **policy_json_payload,
                    }
                    triage_agent = FNOLTriageAgent()
                    triage_result = run_async(
                        triage_agent.run_triage(
                            fnol_data=selected_claim.fnol_data,
                            pdf_bytes=policy_pdf_bytes_manual,
                            policy_json=merged_manual_policy or None,
                        )
                    )

                    selected_claim.triage_result = triage_result
                    selected_claim.handler_approved = (
                        True if triage_result.auto_approval_eligible else None
                    )
                    selected_claim.agent_analyses.append(
                        AgentAnalysis(
                            agent_type="fnol_triage",
                            stage=WorkflowStage.CLAIM_REPORT,
                            result=triage_result.model_dump(mode="json"),
                            recommendation=triage_result.handler_recommendation,
                            confidence=triage_result.confidence,
                        )
                    )
                    selected_claim.updated_at = datetime.utcnow()
                    db.save(selected_claim)

                    st.success(
                        f"Triage ejecutado: {triage_result.preliminary_decision.value.upper()}"
                    )
                    st.write(
                        f"Confianza: {triage_result.confidence:.0%} | "
                        f"Autoaprobacion: {triage_result.auto_approval_eligible}"
                    )
                    _render_triage_result(triage_result)
                    st.rerun()
                except anthropic.AuthenticationError:
                    st.error(
                        "Error de autenticacion con Anthropic (401 invalid x-api-key). "
                        "Actualiza ANTHROPIC_API_KEY en .env y reinicia Streamlit."
                    )
                except (ValueError, TypeError, RuntimeError, OSError, PdfReadError) as exc:
                    st.error(f"No se pudo ejecutar FNOLTriageAgent: {exc}")
        else:
            if selected_claim.triage_result is None:
                st.warning("Triage pendiente. Ejecuta el agente manualmente.")
                if st.button(
                    "Ejecutar FNOLTriageAgent",
                    key=f"run_triage_{selected_claim.id}",
                    use_container_width=True,
                ):
                    try:
                        with st.spinner("Analizando poliza y siniestro con IA..."):
                            policy_pdf_bytes_existing = _get_policy_pdf_bytes_from_claim(selected_claim)
                            stored_policy = (
                                db.get_policy(selected_claim.claimant.policy_number)
                                if selected_claim.claimant.policy_number
                                else None
                            )
                            merged_existing_policy = {
                                **(stored_policy or {}),
                                **_latest_doc_json(selected_claim, "policy_json_input"),
                            }
                            triage_agent = FNOLTriageAgent()
                            triage_result = run_async(
                                triage_agent.run_triage(
                                    fnol_data=selected_claim.fnol_data,
                                    pdf_bytes=policy_pdf_bytes_existing,
                                    policy_json=merged_existing_policy or None,
                                )
                            )
                            selected_claim.triage_result = triage_result
                            selected_claim.handler_approved = (
                                True if triage_result.auto_approval_eligible else None
                            )
                            selected_claim.agent_analyses.append(
                                AgentAnalysis(
                                    agent_type="fnol_triage",
                                    stage=WorkflowStage.CLAIM_REPORT,
                                    result=triage_result.model_dump(mode="json"),
                                    recommendation=triage_result.handler_recommendation,
                                    confidence=triage_result.confidence,
                                )
                            )
                            selected_claim.updated_at = datetime.utcnow()
                            db.save(selected_claim)
                        st.rerun()
                    except anthropic.AuthenticationError:
                        st.error(
                            "Error de autenticacion con Anthropic (401 invalid x-api-key). "
                            "Actualiza ANTHROPIC_API_KEY en .env y reinicia Streamlit."
                        )
                    except (ValueError, TypeError, RuntimeError, OSError, PdfReadError) as exc:
                        st.error(f"No se pudo ejecutar FNOLTriageAgent: {exc}")
            else:
                st.success(
                    f"Triage ejecutado: "
                    f"{selected_claim.triage_result.preliminary_decision.value.upper()} "
                    f"({selected_claim.triage_result.confidence:.0%} confianza)"
                )
                _render_triage_result(selected_claim.triage_result)
                st.info("Triage completado. Pasa a la pestana **Decision Ajustador** para aprobar o rechazar el siniestro.")

    # ---- Adjuster Decision ----
    with adjuster_tab:
        st.markdown("### Decision del Ajustador")

        # ── Read-only view once a decision has been made ──────────────
        if selected_claim.handler_approved is True:
            st.markdown(
                '<div style="font-size:22px; font-weight:700; color:#28a745; margin-bottom:8px;">'
                '✅ Aprobado</div>',
                unsafe_allow_html=True,
            )
            if selected_claim.handler_notes:
                st.markdown(
                    f'<div style="background:#e8f4fd; border-left:4px solid #3b9ede; '
                    f'padding:12px 16px; border-radius:6px; color:#1a1a1a;">'
                    f'{selected_claim.handler_notes}</div>',
                    unsafe_allow_html=True,
                )

        elif selected_claim.handler_approved is False:
            st.markdown(
                '<div style="font-size:22px; font-weight:700; color:#dc3545; margin-bottom:8px;">'
                '❌ Rechazado</div>',
                unsafe_allow_html=True,
            )
            if selected_claim.handler_notes:
                st.markdown(
                    f'<div style="background:#e8f4fd; border-left:4px solid #3b9ede; '
                    f'padding:12px 16px; border-radius:6px; color:#1a1a1a;">'
                    f'{selected_claim.handler_notes}</div>',
                    unsafe_allow_html=True,
                )

        # ── Editable view — pending decision ──────────────────────────
        else:
            handler_notes_key = f"handler_notes_{selected_claim.id}"
            handler_notes = st.text_area(
                "Notas del ajustador",
                value=selected_claim.handler_notes or "",
                height=120,
                key=handler_notes_key,
            )

            action_col_1, action_col_2, action_col_3 = st.columns(3)

            if action_col_1.button(
                "Aprobar",
                key=f"approve_handler_{selected_claim.id}",
                use_container_width=True,
            ):
                try:
                    selected_claim.handler_approved = True
                    selected_claim.handler_notes = handler_notes or None
                    selected_claim.status = ClaimStatus.HANDLER_APPROVED
                    selected_claim.coverage_decision = CoverageDecision.APPROVED
                    selected_claim.updated_at = datetime.utcnow()

                    if selected_claim.current_stage == WorkflowStage.CLAIM_REPORT:
                        try:
                            result = run_async(
                                workflow.process_stage(
                                    selected_claim,
                                    WorkflowStage.VEHICLE_INTAKE,
                                    data={},
                                )
                            )
                            selected_claim = result.updated_claim
                            selected_claim.status = ClaimStatus.HANDLER_APPROVED
                        except (ValueError, TypeError, RuntimeError, OSError):
                            pass

                    db.save(selected_claim)
                    st.rerun()
                except (ValueError, TypeError, RuntimeError, OSError) as exc:
                    st.error(f"No se pudo aprobar el siniestro: {exc}")

            if action_col_2.button(
                "Rechazar",
                key=f"reject_handler_{selected_claim.id}",
                use_container_width=True,
            ):
                selected_claim.handler_approved = False
                selected_claim.handler_notes = handler_notes or None
                selected_claim.status = ClaimStatus.REJECTED
                selected_claim.coverage_decision = CoverageDecision.REJECTED
                selected_claim.updated_at = datetime.utcnow()
                db.save(selected_claim)
                st.rerun()

            checklist_key = f"show_doc_checklist_{selected_claim.id}"
            st.session_state.setdefault(checklist_key, False)

            if action_col_3.button(
                "Solicitar documentos",
                key=f"request_docs_{selected_claim.id}",
                use_container_width=True,
            ):
                st.session_state[checklist_key] = True
                st.rerun()

            if selected_claim.documents_requested_types:
                st.markdown("**Documentos solicitados anteriormente:**")
                for doc in selected_claim.documents_requested_types:
                    st.write(f"- {doc}")

            if st.session_state.get(checklist_key, False):
                st.markdown("### Selecciona los documentos requeridos")
                fotos = st.checkbox("Fotos de los danos", key=f"doc_fotos_{selected_claim.id}")
                denuncia = st.checkbox("Denuncia policial", key=f"doc_denuncia_{selected_claim.id}")
                documentacion = st.checkbox("Documentacion", key=f"doc_documentacion_{selected_claim.id}")
                otros = st.checkbox("Otros", key=f"doc_otros_{selected_claim.id}")
                otros_text = ""
                if otros:
                    otros_text = st.text_input(
                        "Nombre del documento",
                        key=f"doc_otros_text_{selected_claim.id}",
                        placeholder="Ej: Factura de reparacion previa",
                    )

                send_col, cancel_col = st.columns(2)
                if send_col.button(
                    "Enviar solicitud",
                    type="primary",
                    key=f"send_doc_request_{selected_claim.id}",
                    use_container_width=True,
                ):
                    requested: list[str] = []
                    if fotos:
                        requested.append("Fotos de los danos")
                    if denuncia:
                        requested.append("Denuncia policial")
                    if documentacion:
                        requested.append("Documentacion")
                    if otros:
                        requested.append(otros_text.strip() if otros_text.strip() else "Otros")

                    if not requested:
                        st.error("Selecciona al menos un documento.")
                    else:
                        selected_claim.handler_approved = None
                        selected_claim.handler_notes = handler_notes or None
                        selected_claim.documents_requested_types = requested
                        selected_claim.documents_requested_at = datetime.utcnow()
                        selected_claim.status = ClaimStatus.WAITING_FOR_DOCUMENTS
                        selected_claim.updated_at = datetime.utcnow()
                        db.save(selected_claim)
                        st.session_state[checklist_key] = False
                        st.rerun()

                if cancel_col.button(
                    "Cancelar",
                    key=f"cancel_doc_request_{selected_claim.id}",
                    use_container_width=True,
                ):
                    st.session_state[checklist_key] = False
                    st.rerun()

        if selected_claim.current_stage == WorkflowStage.COMPLETED:
            st.success("Este siniestro ya completo el workflow.")

    # ---- Workshop ----
    with workshop_tab_ui:
        wo_exists = (
            selected_claim.work_order is not None
            and bool(selected_claim.work_order.line_items)
        )

        # ── Stage 3: WO already generated ────────────────────────────
        if wo_exists:
            # Use effective display stage (respects ready_for_pickup, repair_started flags)
            # so handler and customer views stay in sync.
            _eff_ws_stage = _effective_display_stage(selected_claim)
            _ws_eff_idx   = STAGE_ORDER.index(_eff_ws_stage)
            _repair_idx   = STAGE_ORDER.index(WorkflowStage.REPAIR_PROCESS)
            _delivery_idx = STAGE_ORDER.index(WorkflowStage.VEHICLE_DELIVERY)
            _billing_idx_t = STAGE_ORDER.index(WorkflowStage.CUSTOMER_APPROVAL_BILLING)

            if _ws_eff_idx == _repair_idx:
                st.info("Reparacion en curso.")
                if st.button(
                    "Marcar vehiculo listo para retiro",
                    key=f"ready_for_pickup_{selected_claim.id}",
                    type="primary",
                    use_container_width=True,
                ):
                    with st.spinner("Avanzando etapa..."):
                        selected_claim.ready_for_pickup = True
                        selected_claim.ready_for_pickup_at = datetime.utcnow()
                        selected_claim.fecha_cierre_ot = datetime.utcnow()
                        selected_claim.updated_at = datetime.utcnow()
                        try:
                            stage_data = {"final_cost_usd": 0, "repair_notes": "Reparacion completada"}
                            current = selected_claim
                            for target in [
                                WorkflowStage.WORK_ORDER_CLOSURE,
                                WorkflowStage.VEHICLE_DELIVERY,
                            ]:
                                if STAGE_ORDER.index(current.current_stage) >= STAGE_ORDER.index(target):
                                    continue
                                res = run_async(workflow.process_stage(current, target, data=stage_data))
                                current = res.updated_claim
                            db.save(current)
                        except (ValueError, TypeError, RuntimeError, OSError) as exc:
                            db.save(selected_claim)
                            st.warning(f"Marcado listo, pero no se pudo avanzar etapa: {exc}")
                    st.rerun()

            elif _ws_eff_idx >= _billing_idx_t:
                # Taller fully complete — prompt handler to go to Orden de Trabajo
                st.success(
                    "✅ Etapa de taller completada. "
                    "Ve a la pestana **Orden de Trabajo** para cargar las facturas finales del taller."
                )

            elif _ws_eff_idx == _delivery_idx:
                # Vehicle delivered, awaiting receipt upload
                st.markdown("### Retiro del vehiculo")
                st.info(
                    "Sube el acta de conformidad firmada por el cliente y confirma el retiro."
                )
                delivery_file = st.file_uploader(
                    "Conformidad del cliente",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"delivery_receipt_{selected_claim.id}",
                )
                if st.button(
                    "Vehiculo retirado",
                    type="primary",
                    key=f"confirm_delivery_{selected_claim.id}",
                    use_container_width=True,
                    disabled=delivery_file is None,
                ):
                    try:
                        with st.spinner("Registrando retiro..."):
                            receipt_bytes = delivery_file.getvalue()
                            get_dir(selected_claim.id, "delivery").joinpath(
                                delivery_file.name
                            ).write_bytes(receipt_bytes)
                            existing_docs = selected_claim.model_dump(mode="json").get(
                                "additional_documents", []
                            )
                            existing_docs.append({
                                "name": delivery_file.name,
                                "category": "delivery_receipt",
                                "uploaded_at": datetime.utcnow().isoformat(),
                                "file_base64": base64.b64encode(receipt_bytes).decode("ascii"),
                            })
                            selected_claim.additional_documents = existing_docs

                            # Pre-fill all required intermediate fields so
                            # the advance loop can traverse any skipped stages.
                            now = datetime.utcnow()
                            selected_claim.fecha_cierre_ot = selected_claim.fecha_cierre_ot or now
                            selected_claim.fecha_salida_taller = now
                            selected_claim.ready_for_pickup = True
                            selected_claim.ready_for_pickup_at = selected_claim.ready_for_pickup_at or now

                            # Advance through all remaining stages up to
                            # CUSTOMER_APPROVAL_BILLING in one go.
                            _target = STAGE_ORDER.index(WorkflowStage.CUSTOMER_APPROVAL_BILLING)
                            stage_data = {
                                "acceptance_receipt_signed": True,
                                "final_cost_usd": 0,
                            }
                            current = selected_claim
                            for target in [
                                WorkflowStage.WORK_ORDER_CLOSURE,
                                WorkflowStage.VEHICLE_DELIVERY,
                                WorkflowStage.CUSTOMER_APPROVAL_BILLING,
                            ]:
                                if STAGE_ORDER.index(current.current_stage) >= STAGE_ORDER.index(target):
                                    continue
                                res = run_async(workflow.process_stage(current, target, data=stage_data))
                                current = res.updated_claim
                            db.save(current)
                        st.rerun()
                    except anthropic.AuthenticationError:
                        st.error(
                            "Error de autenticacion con Anthropic. "
                            "Actualiza ANTHROPIC_API_KEY en .env."
                        )
                    except (ValueError, TypeError, RuntimeError, OSError) as exc:
                        st.error(f"No se pudo confirmar el retiro: {exc}")

            else:
                # SPARE_PARTS_PURCHASE / WORK_ORDER_CLOSURE or any other intermediate state
                st.info(
                    "Orden de trabajo generada. "
                    "Ve a la pestana **Orden de Trabajo** para revisar y aprobar los items."
                )

        # ── Stage 2: vehicle at workshop, no WO yet ───────────────────
        elif selected_claim.vehicle_at_workshop:
            generating_key = f"generating_wo_{selected_claim.id}"
            st.session_state.setdefault(generating_key, False)

            st.markdown("### Inspeccion del Taller")
            inspection_notes = st.text_area(
                "Texto libre de inspeccion",
                value=selected_claim.repair_notes or "",
                height=160,
                key=f"workshop_notes_{selected_claim.id}",
            )
            inspection_images = st.file_uploader(
                "Imagenes de inspeccion",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                key=f"workshop_images_{selected_claim.id}",
            )

            btn_clicked = st.button(
                "Generar Work Order con agente",
                key=f"generate_wo_{selected_claim.id}",
                type="primary",
                use_container_width=True,
                disabled=st.session_state[generating_key],
            )

            if btn_clicked:
                if not inspection_notes.strip() and not inspection_images:
                    st.error("Debes ingresar al menos un texto de inspeccion o una imagen.")
                else:
                    st.session_state[generating_key] = True
                    st.rerun()

            if st.session_state[generating_key]:
                try:
                    with st.spinner("Generando orden de trabajo con IA, por favor espera..."):
                            existing_docs = selected_claim.model_dump(mode="json").get(
                                "additional_documents"
                            ) or []

                            photo_bytes_list: list[bytes] = []
                            workshop_docs: list[dict[str, Any]] = []
                            uploaded_at = datetime.utcnow().isoformat()

                            for inspection_file in inspection_images:
                                file_bytes = inspection_file.getvalue()
                                photo_bytes_list.append(file_bytes)
                                workshop_docs.append({
                                    "name": inspection_file.name,
                                    "category": "workshop_inspection_photo",
                                    "uploaded_at": uploaded_at,
                                    "inspection_note": inspection_notes,
                                    "mime_type": inspection_file.type,
                                    "file_base64": base64.b64encode(file_bytes).decode("ascii"),
                                })

                            workshop_docs.append({
                                "name": f"workshop-inspection-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.txt",
                                "category": "workshop_inspection_note",
                                "uploaded_at": uploaded_at,
                                "inspection_note": inspection_notes,
                            })
                            selected_claim = selected_claim.model_copy(update={
                                "additional_documents": existing_docs + workshop_docs,
                                "workshop_photos_count": (
                                    selected_claim.workshop_photos_count + len(photo_bytes_list)
                                ),
                            })
                            selected_claim.damage = DamageInfo(
                                description=inspection_notes, photos_submitted=True
                            )
                            selected_claim.repair_notes = inspection_notes
                            selected_claim.fecha_inspeccion = datetime.utcnow()
                            selected_claim.updated_at = datetime.utcnow()

                            work_order_data = {
                                "workshop_name": (
                                    selected_claim.work_order.workshop_name
                                    if selected_claim.work_order and selected_claim.work_order.workshop_name
                                    else "Taller inspeccion Streamlit"
                                ),
                                "estimated_completion_days": (
                                    selected_claim.work_order.estimated_completion_days
                                    if selected_claim.work_order and selected_claim.work_order.estimated_completion_days
                                    else 10
                                ),
                                "photo_bytes_list": photo_bytes_list,
                            }

                            result = run_async(workflow.process_stage(
                                selected_claim,
                                WorkflowStage.WORK_ORDER_CREATION,
                                data=work_order_data,
                            ))

                            updated_claim = result.updated_claim
                            updated_claim.status = (
                                ClaimStatus.HANDLER_APPROVED
                                if updated_claim.handler_approved is True
                                else updated_claim.status
                            )
                            updated_claim.agent_analyses.append(AgentAnalysis(
                                agent_type="workshop_inspection_ui",
                                stage=WorkflowStage.WORK_ORDER_CREATION,
                                result={
                                    "inspection_note": inspection_notes,
                                    "uploaded_images": len(photo_bytes_list),
                                    "work_order_number": (
                                        updated_claim.work_order.number
                                        if updated_claim.work_order
                                        else f"WO-{uuid.uuid4().hex[:8].upper()}"
                                    ),
                                },
                                recommendation="Work order generada desde la pestana Taller.",
                            ))
                            updated_claim.updated_at = datetime.utcnow()
                            db.save(updated_claim)
                    st.session_state[generating_key] = False
                    st.rerun()
                except anthropic.AuthenticationError:
                    st.session_state[generating_key] = False
                    st.error(
                        "Error de autenticacion con Anthropic (401 invalid x-api-key). "
                        "Actualiza ANTHROPIC_API_KEY en .env y reinicia Streamlit."
                    )
                except (ValueError, TypeError, RuntimeError, OSError, PdfReadError) as exc:
                    st.session_state[generating_key] = False
                    st.error(f"No se pudo generar la Work Order: {exc}")

        # ── Stage 1: vehicle not yet at workshop ──────────────────────
        else:
            if st.button(
                "Marcar vehiculo en taller",
                key=f"vehicle_at_workshop_{selected_claim.id}",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Registrando ingreso del vehiculo..."):
                    now = datetime.utcnow()
                    selected_claim.vehicle_at_workshop = True
                    selected_claim.vehicle_at_workshop_at = now
                    # Advance to DAMAGE_INSPECTION — inspection phase begins.
                    # Set fecha_inspeccion now so WORK_ORDER_CREATION can enter later.
                    selected_claim.current_stage = WorkflowStage.DAMAGE_INSPECTION
                    selected_claim.fecha_inspeccion = now
                    selected_claim.updated_at = now
                    db.save(selected_claim)
                st.rerun()

    # ---- Work Order ----
    with work_order_tab_ui:
        if selected_claim.work_order is None or not selected_claim.work_order.line_items:
            st.info("Todavia no hay una orden de trabajo generada para este siniestro.")
        else:
            wo = selected_claim.work_order
            phase = wo.phase or "draft"

            st.markdown("### Orden de Trabajo")
            if wo.number:
                st.caption(f"N° {wo.number}")

            # ── Phase C: reconciled — amounts populated + bill comparison ─────
            if phase == "final":
                _repair_idx   = STAGE_ORDER.index(WorkflowStage.REPAIR_PROCESS)
                _billing_idx  = STAGE_ORDER.index(WorkflowStage.CUSTOMER_APPROVAL_BILLING)
                _cur_idx      = STAGE_ORDER.index(selected_claim.current_stage)
                repair_started = _cur_idx >= _repair_idx

                severity = _calc_damage_severity(wo.line_items)
                severity_icon = {"Grave": "🔴", "Mediano": "🟡", "Leve": "🟢"}.get(severity, "")
                st.metric("Severidad de daños", f"{severity_icon} {severity}")

                unapproved = [i for i in wo.line_items if getattr(i, "is_unapproved_alert", False)]
                if unapproved:
                    st.warning(
                        f"⚠️ {len(unapproved)} item(s) agregados por el taller "
                        "no estaban previamente aprobados."
                    )

                _render_wo_table(wo.line_items)

                if not repair_started:
                    starting_repair_key = f"starting_repair_{selected_claim.id}"
                    st.session_state.setdefault(starting_repair_key, False)

                    # Revision final — editable until repair begins
                    st.markdown("#### Revision final")
                    final_decisions: dict[int, str] = {}
                    for idx, item in enumerate(wo.line_items):
                        label = f"{'⚠️ ' if getattr(item, 'is_unapproved_alert', False) else ''}{item.description}"
                        dec_key = f"wo_final_{selected_claim.id}_{idx}"
                        dec = st.select_slider(
                            label,
                            options=["NO", "SI"],
                            value=_decision_label(item.handler_approved),
                            key=dec_key,
                        )
                        final_decisions[idx] = dec

                    col_save, col_start = st.columns(2)

                    if col_save.button(
                        "Guardar revision",
                        use_container_width=True,
                        key=f"wo_save_{selected_claim.id}",
                    ):
                        for idx, dv in final_decisions.items():
                            wo.line_items[idx].handler_approved = (dv == "SI")
                        selected_claim.updated_at = datetime.utcnow()
                        db.save(selected_claim)
                        st.rerun()

                    if col_start.button(
                        "Iniciar Reparacion",
                        type="primary",
                        use_container_width=True,
                        key=f"iniciar_reparacion_{selected_claim.id}",
                        disabled=st.session_state[starting_repair_key],
                    ):
                        st.session_state[f"repair_decisions_{selected_claim.id}"] = dict(final_decisions)
                        st.session_state[starting_repair_key] = True
                        st.rerun()

                    if st.session_state[starting_repair_key]:
                        try:
                            with st.spinner("Iniciando reparacion, por favor espera..."):
                                stored = st.session_state.get(
                                    f"repair_decisions_{selected_claim.id}", {}
                                )
                                for idx, dv in stored.items():
                                    wo.line_items[idx].handler_approved = (dv == "SI")
                                now = datetime.utcnow()
                                selected_claim.repair_started = True
                                selected_claim.repair_started_at = now
                                result = run_async(workflow.process_stage(
                                    selected_claim,
                                    WorkflowStage.REPAIR_PROCESS,
                                    data={"repair_notes": "Iniciado desde revision final de OT"},
                                ))
                                updated = result.updated_claim
                                updated.repair_started = True
                                updated.repair_started_at = now
                                db.save(updated)
                            st.session_state[starting_repair_key] = False
                            st.rerun()
                        except PydanticValidationError as exc:
                            st.session_state[starting_repair_key] = False
                            st.error(f"Error de validacion al iniciar reparacion: {exc}")
                        except (ValueError, TypeError, RuntimeError, OSError) as exc:
                            st.session_state[starting_repair_key] = False
                            st.error(f"No se pudo iniciar la reparacion: {exc}")

                # ── Bill section — only after customer has confirmed pickup ────
                if _cur_idx >= _billing_idx:
                    st.markdown("---")
                    st.markdown("#### Factura final del taller")
                    st.caption("Sube la factura final para que la IA verifique los montos contra la OT aprobada.")

                    bill_file = st.file_uploader(
                        "Factura (PDF o imagen)",
                        type=["pdf", "png", "jpg", "jpeg"],
                        key=f"bill_upload_{selected_claim.id}",
                    )

                    comparison_key = f"bill_comparison_{selected_claim.id}"
                    st.session_state.setdefault(comparison_key, None)

                    if st.button(
                        "Analizar factura con IA",
                        type="primary",
                        key=f"analyze_bill_{selected_claim.id}",
                        use_container_width=True,
                        disabled=bill_file is None,
                    ):
                        try:
                            bill_bytes = bill_file.getvalue()
                            get_dir(selected_claim.id, "bills").joinpath(
                                bill_file.name
                            ).write_bytes(bill_bytes)
                            agent = BillComparisonAgent()
                            comparison = run_async(agent.run_comparison(
                                bill_files=[bill_bytes],
                                wo_line_items=wo.line_items,
                            ))
                            st.session_state[comparison_key] = comparison
                            st.rerun()
                        except anthropic.AuthenticationError:
                            st.error(
                                "Error de autenticacion con Anthropic. "
                                "Actualiza ANTHROPIC_API_KEY en .env."
                            )
                        except (ValueError, TypeError, RuntimeError, OSError) as exc:
                            st.error(f"No se pudo analizar la factura: {exc}")

                    comparison = st.session_state.get(comparison_key)
                    if comparison:
                        st.markdown("#### Resultado del analisis")

                        m1, m2, m3 = st.columns(3)
                        m1.metric("Total factura", _fmt_amount(comparison.get("total_bill_amount")))
                        m2.metric("Total OT aprobada", _fmt_amount(comparison.get("total_wo_amount")))
                        discrepancy = comparison.get("total_discrepancy", 0) or 0
                        m3.metric(
                            "Diferencia",
                            _fmt_amount(abs(discrepancy)) if discrepancy else "$0",
                            delta=f"{'↑' if discrepancy > 0 else '↓'} {abs(discrepancy):,.0f}" if discrepancy else None,
                            delta_color="inverse",
                        )

                        if comparison.get("has_alerts"):
                            st.warning("⚠️ Se detectaron discrepancias. Revisa los items marcados.")

                        rows = []
                        for c in comparison.get("comparisons", []):
                            rows.append({
                                "":              "⚠️" if c.get("is_alert") else "✅",
                                "Factura":       c.get("bill_description", "—"),
                                "OT aprobada":   c.get("wo_description", "—"),
                                "Monto factura": _fmt_amount(c.get("bill_amount")),
                                "Monto OT":      _fmt_amount(c.get("wo_amount")),
                                "Diferencia":    _fmt_amount(c.get("discrepancy_amount")),
                                "Dif %":         f"{(c.get('discrepancy_pct') or 0):.1f}%",
                                "Alerta":        c.get("alert_reason") or "",
                            })
                        if rows:
                            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                        st.markdown(f"**Resumen:** {comparison.get('summary', '')}")
                        st.markdown("---")

                        handler_bill_notes = st.text_area(
                            "Notas del ajustador sobre la factura",
                            placeholder="Todo conforme / Diferencia en item X justificada por...",
                            key=f"bill_notes_{selected_claim.id}",
                        )
                        if st.button(
                            "Aprobar factura y cerrar siniestro",
                            type="primary",
                            key=f"approve_bill_{selected_claim.id}",
                            use_container_width=True,
                        ):
                            try:
                                if handler_bill_notes.strip():
                                    existing_docs = selected_claim.model_dump(mode="json").get(
                                        "additional_documents", []
                                    )
                                    existing_docs.append({
                                        "name": "notas_factura_ajustador.txt",
                                        "category": "bill_approval_notes",
                                        "uploaded_at": datetime.utcnow().isoformat(),
                                        "notes": handler_bill_notes.strip(),
                                        "comparison_summary": comparison.get("summary", ""),
                                    })
                                    selected_claim.additional_documents = existing_docs

                                now = datetime.utcnow()
                                if not selected_claim.fecha_cierre_ot:
                                    selected_claim.fecha_cierre_ot = now
                                if not getattr(selected_claim, "fecha_salida_taller", None):
                                    selected_claim.fecha_salida_taller = now
                                selected_claim.ready_for_pickup = selected_claim.ready_for_pickup or True
                                selected_claim.ready_for_pickup_at = selected_claim.ready_for_pickup_at or now

                                invoice_number = f"INV-{selected_claim.id[:8].upper()}"
                                stage_data: dict[str, Any] = {
                                    "invoice_number": invoice_number,
                                    "acceptance_receipt_signed": True,
                                    "final_cost_usd": 0,
                                }

                                current = selected_claim
                                for target in [
                                    WorkflowStage.WORK_ORDER_CLOSURE,
                                    WorkflowStage.VEHICLE_DELIVERY,
                                    WorkflowStage.CUSTOMER_APPROVAL_BILLING,
                                ]:
                                    if STAGE_ORDER.index(current.current_stage) >= STAGE_ORDER.index(target):
                                        continue
                                    res = run_async(workflow.process_stage(current, target, data=stage_data))
                                    current = res.updated_claim
                                # Mark claim as completed
                                current.current_stage = WorkflowStage.COMPLETED
                                current.updated_at = datetime.utcnow()
                                db.save(current)
                                st.session_state[comparison_key] = None
                                st.rerun()
                            except (ValueError, TypeError, RuntimeError, OSError) as exc:
                                st.error(f"No se pudo cerrar el siniestro: {exc}")

            # ── Phase B: handler reviewed items, awaiting budget ─────────────
            elif phase == "handler_reviewed":
                st.info("Decisiones guardadas. Sube el presupuesto del taller para continuar.")
                _render_wo_table(wo.line_items)

                reconcile_key = f"reconciling_{selected_claim.id}"
                st.session_state.setdefault(reconcile_key, False)

                st.markdown("#### Presupuesto del taller")
                budget_file = st.file_uploader(
                    "Presupuesto (PDF o imagen)",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"budget_upload_{selected_claim.id}",
                )

                btn_reconcile = st.button(
                    "Enviar presupuesto y reconciliar",
                    type="primary",
                    key=f"reconcile_{selected_claim.id}",
                    use_container_width=True,
                    disabled=budget_file is None or st.session_state[reconcile_key],
                )

                if btn_reconcile and budget_file is not None:
                    st.session_state[reconcile_key] = True
                    st.rerun()

                if st.session_state[reconcile_key]:
                    try:
                        with st.spinner("Analizando presupuesto con IA, por favor espera..."):
                            budget_bytes = budget_file.getvalue()
                            get_dir(selected_claim.id, "budget").joinpath(
                                budget_file.name
                            ).write_bytes(budget_bytes)
                            result = run_async(workflow.process_stage(
                                selected_claim,
                                WorkflowStage.SPARE_PARTS_PURCHASE,
                                data={"budget_files": [budget_bytes], "deductible": 0},
                            ))
                            updated = result.updated_claim
                            updated.work_order.phase = "final"
                            db.save(updated)
                        st.session_state[reconcile_key] = False
                        st.rerun()
                    except anthropic.AuthenticationError:
                        st.session_state[reconcile_key] = False
                        st.error(
                            "Error de autenticacion con Anthropic. "
                            "Actualiza ANTHROPIC_API_KEY en .env."
                        )
                    except (ValueError, TypeError, RuntimeError, OSError) as exc:
                        st.session_state[reconcile_key] = False
                        st.error(f"No se pudo reconciliar: {exc}")

            # ── Phase A: initial review — no amounts yet ─────────────────────
            else:
                st.info("Revisa cada item y aprueba o rechaza antes de solicitar el presupuesto.")
                _render_wo_table(wo.line_items)

                st.markdown("#### Decisiones por item")
                with st.form(f"work_order_review_{selected_claim.id}"):
                    decision_updates: list[tuple[int, str]] = []
                    for idx, line_item in enumerate(wo.line_items):
                        row_cols = st.columns([5, 2, 1])
                        row_cols[0].write(line_item.description)
                        row_cols[1].write(_work_type_label(line_item))
                        decision_value = row_cols[2].select_slider(
                            f"Decision {idx + 1}",
                            options=["NO", "SI"],
                            value=_decision_label(line_item.handler_approved),
                            label_visibility="collapsed",
                            key=f"wo_decision_{selected_claim.id}_{idx}",
                        )
                        decision_updates.append((idx, decision_value))

                    save_work_order = st.form_submit_button(
                        "Guardar decisiones", use_container_width=True
                    )

                if save_work_order:
                    for idx, dv in decision_updates:
                        wo.line_items[idx].handler_approved = (dv == "SI")
                    selected_claim.work_order.phase = "handler_reviewed"
                    selected_claim.updated_at = datetime.utcnow()
                    db.save(selected_claim)
                    st.rerun()

    # ---- Documentacion ----
    with docs_tab_h:
        _render_customer_docs(selected_claim)

    # ---- Policy ----
    with policy_tab:
        st.markdown("### Poliza")
        policy_number = selected_claim.claimant.policy_number
        if not policy_number:
            st.info("Este siniestro no tiene numero de poliza registrado.")
        else:
            policy_data = db.get_policy(policy_number)
            if policy_data:
                st.json(policy_data)
            else:
                st.info(f"No se encontro informacion de poliza para '{policy_number}'.")


# ---- CUSTOMER DASHBOARD ----

def render_customer_dashboard() -> None:
    col_title, col_btn = st.columns([5, 1])
    col_title.title("ClearProcess")
    if col_btn.button("+ Crear siniestro", use_container_width=True, type="primary"):
        navigate_to("new_claim")

    st.markdown("---")
    st.subheader("Buscar mi siniestro")
    search_col, btn_col = st.columns([4, 1])
    short_id_input = search_col.text_input(
        "ID del siniestro",
        placeholder="Primeros 8 caracteres, ej: 4f1434c1",
        label_visibility="collapsed",
        key="customer_search_input",
    )
    if btn_col.button("Buscar", use_container_width=True, type="primary", key="customer_search_btn"):
        q = short_id_input.strip().lower()
        if not q:
            st.error("Ingresa el ID de tu siniestro.")
        else:
            match = next((c for c in db.list_all() if c.id.startswith(q)), None)
            if match:
                navigate_to("claim_detail", match.id)
            else:
                st.error(f"No se encontro ningun siniestro con ID '{short_id_input.strip()}'.")


# ---- CUSTOMER CLAIM DETAIL helpers ----

def _effective_display_stage(claim: Claim) -> WorkflowStage:
    """Return the stage to highlight in the customer timeline.

    The handler side uses boolean flags (repair_started, ready_for_pickup,
    vehicle_at_workshop) that don't always advance current_stage immediately.
    Promote the display stage so the customer sees the real situation.
    """
    idx = STAGE_ORDER.index(claim.current_stage)
    repair_idx = STAGE_ORDER.index(WorkflowStage.REPAIR_PROCESS)
    closure_idx = STAGE_ORDER.index(WorkflowStage.WORK_ORDER_CLOSURE)
    intake_idx = STAGE_ORDER.index(WorkflowStage.VEHICLE_INTAKE)

    inspect_idx = STAGE_ORDER.index(WorkflowStage.DAMAGE_INSPECTION)

    delivery_idx = STAGE_ORDER.index(WorkflowStage.VEHICLE_DELIVERY)

    if claim.ready_for_pickup and idx < delivery_idx:
        return WorkflowStage.VEHICLE_DELIVERY
    if claim.repair_started and repair_idx <= idx < delivery_idx:
        return WorkflowStage.REPAIR_PROCESS
    if claim.vehicle_at_workshop and idx < inspect_idx:
        return WorkflowStage.DAMAGE_INSPECTION
    return claim.current_stage


def _render_customer_stage_timeline(claim: Claim) -> None:
    eff = _effective_display_stage(claim)
    eff_idx = STAGE_ORDER.index(eff)

    INTAKE_IDX    = STAGE_ORDER.index(WorkflowStage.VEHICLE_INTAKE)
    INSPECT_IDX   = STAGE_ORDER.index(WorkflowStage.DAMAGE_INSPECTION)
    REPAIR_IDX    = STAGE_ORDER.index(WorkflowStage.REPAIR_PROCESS)
    DELIVERY_IDX  = STAGE_ORDER.index(WorkflowStage.VEHICLE_DELIVERY)
    BILLING_IDX   = STAGE_ORDER.index(WorkflowStage.CUSTOMER_APPROVAL_BILLING)
    COMPLETED_IDX = STAGE_ORDER.index(WorkflowStage.COMPLETED)

    def _color(done: bool, current: bool) -> str:
        if done:    return "#28a745"
        if current: return "#fd7e14"
        return "#cccccc"

    def _ts(dt) -> str:
        if dt is None:
            return ""
        try:
            return dt.strftime("%d/%m/%Y")
        except Exception:
            return ""

    def _stage_row(label: str, done: bool, current: bool, ts=None, indent: bool = False) -> None:
        color  = _color(done, current)
        weight = "bold" if current else "normal"
        size   = "15px" if not indent else "14px"
        ml     = "28px" if indent else "0"
        ts_html = (
            f'<span style="color:{color}; font-size:12px; margin-left:10px;">{_ts(ts)}</span>'
            if ts is not None else ""
        )
        st.markdown(
            f'<div style="margin:5px 0 5px {ml};">'
            f'<span style="color:{color}; font-size:17px;">&#9679;</span>&nbsp;'
            f'<span style="color:{color}; font-weight:{weight}; font-size:{size};">{label}</span>'
            f'{ts_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 1. APERTURA DEL SINIESTRO ────────────────────────────────────
    ap_done    = eff_idx > 0
    ap_current = eff_idx == 0
    color = _color(ap_done, ap_current)
    weight = "bold" if ap_current else "normal"

    if claim.status == ClaimStatus.REJECTED:
        extra = " &mdash; <em>Rechazado</em>"
    elif claim.status == ClaimStatus.WAITING_FOR_DOCUMENTS and claim.documents_requested_types:
        docs = ", ".join(claim.documents_requested_types)
        extra = f" &mdash; Documentos pendientes: {docs}"
    elif ap_done:
        extra = " &mdash; Aprobado"
    else:
        extra = " &mdash; En revision"

    apertura_ts = _ts(getattr(claim, "fecha_aviso", None) or claim.created_at)
    st.markdown(
        f'<div style="margin:5px 0;">'
        f'<span style="color:{color}; font-size:17px;">&#9679;</span>&nbsp;'
        f'<span style="color:{color}; font-weight:{weight}; font-size:15px;">Apertura del siniestro</span>'
        f'<span style="color:{color}; font-size:12px; margin-left:10px;">{apertura_ts}</span>'
        f'<span style="color:{color}; font-size:13px;">{extra}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 2. TALLER ─────────────────────────────────────────────────────
    taller_done    = eff_idx >= BILLING_IDX
    taller_current = INTAKE_IDX <= eff_idx < BILLING_IDX
    taller_pending = eff_idx < INTAKE_IDX

    color  = _color(taller_done, taller_current)
    weight = "bold" if taller_current else "normal"
    taller_ts = _ts(getattr(claim, "fecha_ingreso_taller", None))
    taller_ts_html = (
        f'<span style="color:{color}; font-size:12px; margin-left:10px;">{taller_ts}</span>'
        if taller_ts else ""
    )
    st.markdown(
        f'<div style="margin:5px 0;">'
        f'<span style="color:{color}; font-size:17px;">&#9679;</span>&nbsp;'
        f'<span style="color:{color}; font-weight:{weight}; font-size:15px;">Taller</span>'
        f'{taller_ts_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not taller_pending:
        _stage_row(
            "Ingreso al taller",
            done=eff_idx > INTAKE_IDX,
            current=eff_idx == INTAKE_IDX,
            ts=claim.vehicle_at_workshop_at,
            indent=True,
        )
        _stage_row(
            "Inspeccion de danos",
            done=eff_idx >= REPAIR_IDX,
            current=INSPECT_IDX <= eff_idx < REPAIR_IDX,
            ts=getattr(claim, "fecha_inspeccion", None),
            indent=True,
        )
        _stage_row(
            "Reparacion en curso",
            done=eff_idx >= DELIVERY_IDX,
            current=REPAIR_IDX <= eff_idx < DELIVERY_IDX,
            ts=claim.repair_started_at,
            indent=True,
        )
        _stage_row(
            "Entrega del vehiculo",
            done=eff_idx >= BILLING_IDX,
            current=eff_idx == DELIVERY_IDX,
            ts=getattr(claim, "fecha_salida_taller", None),
            indent=True,
        )

    # ── 3. SINIESTRO COMPLETADO ───────────────────────────────────────
    _stage_row(
        "Siniestro completado",
        done=eff_idx >= COMPLETED_IDX,
        current=eff_idx == BILLING_IDX,
        ts=getattr(claim, "fecha_cierre", None),
    )


def _render_customer_docs(claim: Claim) -> None:
    CATEGORY_LABELS = {
        "claim_pdf_extraction": "Declaracion del siniestro",
        "policy_pdf_extraction": "Poliza de seguro",
        "claim_json_input": "Datos del siniestro",
        "policy_json_input": "Datos de la poliza",
        "customer_upload": "Documento subido por el asegurado",
        "workshop_inspection_photo": "Foto de inspeccion del taller",
        "workshop_inspection_note": "Nota de inspeccion del taller",
        "delivery_receipt": "Acta de entrega / Conformidad",
        "customer_confirmation": "Confirmacion de conformidad del cliente",
        "bill_approval_notes": "Notas de aprobacion de factura",
    }

    viewable_docs = [
        d for d in claim.additional_documents
        if d.get("category") not in {"workshop_inspection_photo", "workshop_inspection_note"}
    ]

    if viewable_docs:
        st.markdown("### Documentos en el expediente")
        for i, doc in enumerate(viewable_docs):
            cat = doc.get("category", "otro")
            doc_type = doc.get("doc_type", "")
            name = doc.get("name") or doc_type or "documento"
            if cat == "customer_upload" and doc_type:
                display_label = doc_type
            else:
                display_label = CATEGORY_LABELS.get(cat, cat)
            col_name, col_dl = st.columns([5, 1])
            col_name.write(f"**{display_label}**")
            pdf_data = doc.get("pdf_base64") or doc.get("file_base64")
            if pdf_data:
                try:
                    col_dl.download_button(
                        "Descargar",
                        data=base64.b64decode(pdf_data),
                        file_name=name,
                        key=f"dl_{claim.id}_{i}",
                    )
                except Exception:
                    pass
    else:
        st.info("No hay documentos en el expediente aun.")

    if claim.documents_requested_types and claim.status == ClaimStatus.WAITING_FOR_DOCUMENTS:
        st.markdown("---")
        st.markdown("### Documentos solicitados por el ajustador")
        st.info("Por favor sube los siguientes documentos para continuar con tu siniestro.")

        for idx, doc_type in enumerate(claim.documents_requested_types):
            already_uploaded = any(
                d.get("doc_type") == doc_type and d.get("category") == "customer_upload"
                for d in claim.additional_documents
            )
            if already_uploaded:
                st.markdown(f"&#9989; **{doc_type}** — Ya subido")
                continue

            st.markdown(f"**{doc_type}**")
            with st.form(key=f"upload_req_{claim.id}_{idx}"):
                uploaded = st.file_uploader(
                    "Selecciona el archivo",
                    label_visibility="collapsed",
                    key=f"req_file_{claim.id}_{idx}",
                )
                if st.form_submit_button("Subir", use_container_width=True):
                    if not uploaded:
                        st.error("Selecciona un archivo antes de subir.")
                    else:
                        file_bytes = uploaded.getvalue()
                        get_dir(claim.id, "customer").joinpath(uploaded.name).write_bytes(file_bytes)
                        existing = claim.model_dump(mode="json").get("additional_documents", [])
                        existing.append({
                            "name": uploaded.name,
                            "category": "customer_upload",
                            "doc_type": doc_type,
                            "uploaded_at": datetime.utcnow().isoformat(),
                            "file_base64": base64.b64encode(file_bytes).decode("ascii"),
                        })
                        claim.additional_documents = existing
                        claim.updated_at = datetime.utcnow()
                        db.save(claim)
                        st.success(f"'{doc_type}' subido correctamente.")
                        st.rerun()

    st.markdown("---")
    st.markdown("### Subir documento adicional")
    with st.form(key=f"upload_extra_{claim.id}"):
        doc_type_options = [
            "Fotos de los danos",
            "Denuncia policial",
            "Documentacion",
            "Otros",
        ]
        selected_type = st.selectbox(
            "Tipo de documento",
            options=doc_type_options,
            key=f"extra_type_{claim.id}",
        )
        otros_name = ""
        if selected_type == "Otros":
            otros_name = st.text_input(
                "Nombre del documento",
                placeholder="Ej: Presupuesto del taller",
                key=f"extra_otros_name_{claim.id}",
            )
        extra_files = st.file_uploader(
            "Selecciona uno o varios archivos",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=f"extra_file_{claim.id}",
        )
        if st.form_submit_button("Subir documentos", use_container_width=True):
            final_name = otros_name.strip() if selected_type == "Otros" else selected_type
            if not extra_files:
                st.error("Selecciona al menos un archivo.")
            elif selected_type == "Otros" and not final_name:
                st.error("Ingresa el nombre del documento.")
            else:
                existing = claim.model_dump(mode="json").get("additional_documents", [])
                uploaded_at = datetime.utcnow().isoformat()
                for f in extra_files:
                    file_bytes = f.getvalue()
                    get_dir(claim.id, "customer").joinpath(f.name).write_bytes(file_bytes)
                    existing.append({
                        "name": final_name,
                        "category": "customer_upload",
                        "doc_type": final_name,
                        "uploaded_at": uploaded_at,
                        "file_base64": base64.b64encode(file_bytes).decode("ascii"),
                    })
                claim.additional_documents = existing
                claim.updated_at = datetime.utcnow()
                db.save(claim)
                st.success(f"{len(extra_files)} documento(s) subido(s) correctamente.")
                st.rerun()


def _render_customer_poliza(claim: Claim) -> None:
    policy_number = claim.claimant.policy_number
    if not policy_number:
        st.info("No hay numero de poliza registrado para este siniestro.")
        return

    policy_data = db.get_policy(policy_number)
    if not policy_data:
        st.info("No se encontro informacion de poliza.")
        return

    col1, col2 = st.columns(2)
    col1.metric("Poliza", policy_data.get("policy_number") or policy_number)
    col1.metric("Asegurado", policy_data.get("insured_name") or policy_data.get("claimant_name") or "—")
    col2.metric("Vigencia desde", policy_data.get("coverage_start") or "—")
    col2.metric("Vigencia hasta", policy_data.get("coverage_end") or "—")

    with st.expander("Ver detalle completo", expanded=False):
        st.json(policy_data)

    policy_pdf_bytes = _get_policy_pdf_bytes_from_claim(claim)
    if policy_pdf_bytes:
        st.download_button(
            "Descargar poliza (PDF)",
            data=policy_pdf_bytes,
            file_name=f"poliza_{policy_number}.pdf",
            key=f"dl_policy_{claim.id}",
        )


# ---- CUSTOMER CLAIM DETAIL ----

def render_customer_claim_detail(claim_id: str) -> None:
    if st.button("← Volver"):
        navigate_to("dashboard")

    claim = db.get(claim_id)
    if claim is None:
        st.error("No se pudo cargar el siniestro.")
        return

    st.subheader(f"Siniestro #{claim.id[:8]}")
    st.caption(
        f"{claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}"
        f" — {claim.vehicle.license_plate}"
    )

    estado_tab, docs_tab, poliza_tab = st.tabs(["Estado", "Documentacion", "Poliza"])

    with estado_tab:
        _render_customer_stage_timeline(claim)

        eff_stage = _effective_display_stage(claim)
        if eff_stage == WorkflowStage.VEHICLE_DELIVERY:
            st.markdown("---")
            st.success("🚗 Tu vehiculo esta listo para ser retirado. Acercate al taller para retirarlo.")

    with docs_tab:
        _render_customer_docs(claim)

    with poliza_tab:
        _render_customer_poliza(claim)


# ---- page routing ----

_page = st.session_state.page
_mode = st.session_state.view_mode

if _mode == "customer":
    if _page == "dashboard":
        render_customer_dashboard()
    elif _page == "new_claim":
        render_new_claim()
    elif _page == "claim_detail":
        render_customer_claim_detail(st.session_state.selected_claim_id)
    else:
        render_customer_dashboard()
else:
    if _page == "dashboard":
        render_dashboard()
    elif _page == "new_claim":
        render_new_claim()
    elif _page == "claim_detail":
        render_claim_detail(st.session_state.selected_claim_id)
    else:
        render_dashboard()
