"""AI Agent: Work Order Reconciliation (Phase 3 of the unified WO step)

Takes:
  1. The handler-approved preliminary WO (line items with handler_approved=True/False)
  2. The workshop budget document (PDF or images) uploaded by the handler

Produces:
  - A final WO with actual costs filled in from the workshop budget
  - Alerts for workshop items that don't correspond to any approved preliminary item
  - Structured WorkOrderSummary (totals row)

Item matching is intentionally fuzzy — the workshop will name items differently
from the preliminary WO (e.g. "NEUMATICO 195/80 R15" vs "LLANTA DELANTERA").
The agent uses semantic understanding to match across naming conventions.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from models.claim import WorkOrderLineItem, WorkOrderSummary

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

_DETECT = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
}

def _media_type(data: bytes) -> str:
    for sig, mt in _DETECT.items():
        if data[:len(sig)] == sig:
            return mt
    # Check for PDF
    if data[:4] == b"%PDF":
        return "application/pdf"
    return "image/jpeg"


class WorkOrderReconciliationAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return """You are an expert insurance work order auditor.
Your task is to reconcile a workshop's budget/quotation against an approved preliminary Work Order.

The workshop budget will use different item names from the preliminary WO — you must use
semantic understanding to match them. For example:
  "NEUMATICO 195/80 R15" matches "LLANTA" (both are tires)
  "CILINDRO PUERTA DEL DER" matches "CILINDRO PUERTA DELANTERA DERECHA"
  "PUERTA DELAN.DERECHA" matches "PUERTA DELANTERA DERECHA"

Rules:
1. Match each workshop budget item to the closest approved preliminary WO item
2. Fill in the actual costs (from the budget) into the correct WO columns
3. If a workshop item has NO match in the approved preliminary WO:
   - Include it in the final WO with is_unapproved_alert=True
   - Add it to the unapproved_alerts list with a clear description
4. Items in the preliminary WO that have NO workshop price: keep with null costs
5. Calculate the WorkOrderSummary totals from the final line items

The work type columns (Sustituir/Reparar/Pintar/Trabajo Externo) should be carried
from the preliminary WO for matched items, or inferred from the budget for new items.

Return JSON with:
{
  "line_items": [...],
  "unapproved_alerts": ["alert message 1", ...],
  "summary": {
    "trabajo_externo": float,
    "reparacion_hours": float,
    "reparacion": float,
    "pintura_hours": float,
    "pintura": float,
    "repuestos": float,
    "subtotal_neto": float,
    "deducible_neto": float,
    "depreciacion_neto": float,
    "total_compania_neto": float
  }
}"""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "extract_budget_items",
                "description": "Extract all line items and their costs from the workshop budget document",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "budget_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string"},
                                    "unit_cost": {"type": "number"},
                                    "quantity": {"type": "number", "default": 1},
                                    "hours": {"type": "number"},
                                    "total": {"type": "number"},
                                    "category": {
                                        "type": "string",
                                        "description": "sustituir / reparacion / pintura / trabajo_externo",
                                    },
                                },
                                "required": ["description", "total"],
                            },
                        },
                        "budget_currency": {
                            "type": "string",
                            "description": "Currency of the budget (e.g. CLP, USD)",
                        },
                    },
                    "required": ["budget_items"],
                },
            },
            {
                "name": "match_budget_to_preliminary_wo",
                "description": "Semantically match each budget item to a preliminary WO item",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "matches": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "budget_item_description": {"type": "string"},
                                    "preliminary_wo_item_number": {
                                        "type": "integer",
                                        "description": "item_number from preliminary WO, or -1 if no match",
                                    },
                                    "preliminary_wo_description": {"type": "string"},
                                    "match_confidence": {
                                        "type": "number",
                                        "description": "0.0-1.0 semantic similarity",
                                    },
                                    "match_reason": {"type": "string"},
                                    "budget_total": {"type": "number"},
                                    "budget_hours": {"type": "number"},
                                },
                                "required": [
                                    "budget_item_description",
                                    "preliminary_wo_item_number",
                                    "match_confidence",
                                    "budget_total",
                                ],
                            },
                        }
                    },
                    "required": ["matches"],
                },
            },
            {
                "name": "flag_unapproved_items",
                "description": "Identify budget items that don't correspond to any approved WO item",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "unapproved_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "budget_description": {"type": "string"},
                                    "budget_amount": {"type": "number"},
                                    "reason_not_matched": {"type": "string"},
                                },
                                "required": ["budget_description", "budget_amount"],
                            },
                        }
                    },
                    "required": ["unapproved_items"],
                },
            },
            {
                "name": "build_final_wo_with_totals",
                "description": "Assemble the final WO line items and calculate summary totals",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "line_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item_number": {"type": "integer"},
                                    "description": {"type": "string"},
                                    "desmontar_montar": {"type": "number"},
                                    "cambiar": {"type": "number"},
                                    "valor_repuesto": {"type": "number"},
                                    "reparar_leve": {"type": "number"},
                                    "reparar_mediano": {"type": "number"},
                                    "reparar_grave": {"type": "number"},
                                    "pintar": {"type": "number"},
                                    "trabajo_externo": {"type": "number"},
                                    "workshop_matched_description": {"type": "string"},
                                    "match_confidence": {"type": "number"},
                                    "is_unapproved_alert": {"type": "boolean"},
                                    "ai_recommendation": {"type": "string"},
                                    "ai_reason": {"type": "string"},
                                    "handler_approved": {"type": "boolean"},
                                },
                                "required": ["item_number", "description"],
                            },
                        },
                        "summary": {
                            "type": "object",
                            "properties": {
                                "trabajo_externo": {"type": "number"},
                                "reparacion_hours": {"type": "number"},
                                "reparacion": {"type": "number"},
                                "pintura_hours": {"type": "number"},
                                "pintura": {"type": "number"},
                                "repuestos": {"type": "number"},
                                "subtotal_neto": {"type": "number"},
                                "deducible_neto": {"type": "number"},
                                "depreciacion_neto": {"type": "number"},
                                "total_compania_neto": {"type": "number"},
                            },
                        },
                    },
                    "required": ["line_items", "summary"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "extract_budget_items":
            items = tool_input.get("budget_items", [])
            total = sum(i.get("total", 0) for i in items if isinstance(i.get("total"), (int, float)))
            return json.dumps({
                "extracted": len(items),
                "budget_total": round(total, 2),
                "currency": tool_input.get("budget_currency", "CLP"),
            })

        if tool_name == "match_budget_to_preliminary_wo":
            matches = tool_input.get("matches", [])
            unmatched = [m for m in matches if m.get("preliminary_wo_item_number", -1) == -1]
            matched = [m for m in matches if m.get("preliminary_wo_item_number", -1) != -1]
            return json.dumps({
                "total_budget_items": len(matches),
                "matched": len(matched),
                "unmatched": len(unmatched),
                "avg_confidence": round(
                    sum(m.get("match_confidence", 0) for m in matched) / len(matched)
                    if matched else 0,
                    2,
                ),
            })

        if tool_name == "flag_unapproved_items":
            items = tool_input.get("unapproved_items", [])
            total_unapproved = sum(
                i.get("budget_amount", 0)
                for i in items
                if isinstance(i.get("budget_amount"), (int, float))
            )
            return json.dumps({
                "unapproved_count": len(items),
                "unapproved_total": round(total_unapproved, 2),
                "alerts_generated": len(items),
            })

        if tool_name == "build_final_wo_with_totals":
            items = tool_input.get("line_items", [])
            summary = tool_input.get("summary", {})
            return json.dumps({
                "final_wo_items": len(items),
                "alerts": sum(1 for i in items if i.get("is_unapproved_alert")),
                "total_compania_neto": summary.get("total_compania_neto", 0),
            })

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def run_reconciliation(
        self,
        approved_line_items: list[WorkOrderLineItem],
        budget_files: list[bytes],
        deductible: float = 0.0,
        max_iterations: int = 14,
    ) -> tuple[list[WorkOrderLineItem], WorkOrderSummary, list[str]]:
        """Reconcile approved preliminary WO against workshop budget documents.

        Returns:
            (final_line_items, summary, unapproved_alerts)
        """
        # Build content blocks
        content: list[dict[str, Any]] = []

        for i, file_bytes in enumerate(budget_files):
            mt = _media_type(file_bytes)
            b64 = base64.standard_b64encode(file_bytes).decode()
            if mt == "application/pdf":
                content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    "title": f"Workshop Budget Document {i + 1}",
                    "cache_control": {"type": "ephemeral"},
                })
            else:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mt, "data": b64},
                })

        # Serialize the approved preliminary WO for context
        approved_items_json = json.dumps(
            [
                {
                    "item_number": item.item_number,
                    "description": item.description,
                    "work_type": _infer_work_type(item),
                    "handler_approved": item.handler_approved,
                    "ai_recommendation": item.ai_recommendation,
                }
                for item in approved_line_items
            ],
            indent=2,
            ensure_ascii=False,
        )

        content.append({
            "type": "text",
            "text": (
                f"APPROVED PRELIMINARY WORK ORDER ({len(approved_line_items)} items):\n"
                f"{approved_items_json}\n\n"
                f"Deductible: {deductible}\n\n"
                "Please reconcile the workshop budget (in the attached document/images) "
                "against this approved preliminary WO.\n"
                "Use all four tools: extract_budget_items → match_budget_to_preliminary_wo → "
                "flag_unapproved_items → build_final_wo_with_totals.\n"
                "Return final JSON with keys: line_items, unapproved_alerts, summary."
            ),
        })

        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        final_text = ""
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=6000,
                # thinking={"type": "adaptive"},
                system=self.system_prompt,
                tools=self.tools,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        final_text = block.text
                break

            if response.stop_reason != "tool_use":
                for block in response.content:
                    if block.type == "text":
                        final_text = block.text
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    res = await self._execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": res,
                    })
            messages.append({"role": "user", "content": tool_results})

        return self._parse_reconciliation_result(final_text, approved_line_items, deductible)

    def _parse_reconciliation_result(
        self,
        text: str,
        preliminary_items: list[WorkOrderLineItem],
        deductible: float,
    ) -> tuple[list[WorkOrderLineItem], WorkOrderSummary, list[str]]:
        """Parse agent output into (line_items, summary, alerts)."""
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                raw_items = data.get("line_items", [])
                raw_summary = data.get("summary", {})
                alerts = data.get("unapproved_alerts", [])

                # Build preliminary WO lookup for carrying over handler decisions
                prelim_lookup = {i.item_number: i for i in preliminary_items}

                line_items: list[WorkOrderLineItem] = []
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        continue
                    item_num = raw.get("item_number", len(line_items) + 1)
                    prelim = prelim_lookup.get(item_num)

                    item = WorkOrderLineItem(
                        item_number=item_num,
                        description=raw.get("description", ""),
                        desmontar_montar=raw.get("desmontar_montar"),
                        cambiar=raw.get("cambiar"),
                        valor_repuesto=raw.get("valor_repuesto"),
                        reparar_leve=raw.get("reparar_leve"),
                        reparar_mediano=raw.get("reparar_mediano"),
                        reparar_grave=raw.get("reparar_grave"),
                        pintar=raw.get("pintar"),
                        trabajo_externo=raw.get("trabajo_externo"),
                        workshop_matched_description=raw.get("workshop_matched_description"),
                        match_confidence=raw.get("match_confidence"),
                        is_unapproved_alert=raw.get("is_unapproved_alert", False),
                        ai_recommendation=raw.get("ai_recommendation") or (prelim.ai_recommendation if prelim else None),
                        ai_reason=raw.get("ai_reason") or (prelim.ai_reason if prelim else None),
                        handler_approved=raw.get("handler_approved") if raw.get("handler_approved") is not None else (prelim.handler_approved if prelim else None),
                    )
                    line_items.append(item)

                # Also add any approved preliminary items that the agent didn't include
                final_item_nums = {i.item_number for i in line_items}
                for prelim in preliminary_items:
                    if prelim.item_number not in final_item_nums and prelim.handler_approved is not False:
                        line_items.append(prelim)  # carry forward with null costs

                line_items.sort(key=lambda x: x.item_number)

                summary = WorkOrderSummary(
                    trabajo_externo=raw_summary.get("trabajo_externo", 0.0),
                    reparacion_hours=raw_summary.get("reparacion_hours", 0.0),
                    reparacion=raw_summary.get("reparacion", 0.0),
                    pintura_hours=raw_summary.get("pintura_hours", 0.0),
                    pintura=raw_summary.get("pintura", 0.0),
                    repuestos=raw_summary.get("repuestos", 0.0),
                    subtotal_neto=raw_summary.get("subtotal_neto", 0.0),
                    deducible_neto=raw_summary.get("deducible_neto", deductible),
                    depreciacion_neto=raw_summary.get("depreciacion_neto", 0.0),
                    total_compania_neto=raw_summary.get("total_compania_neto", 0.0),
                )

                return line_items, summary, alerts

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Reconciliation parse error: %s", e)

        # Fallback: return preliminary items unchanged with zero summary
        return (
            preliminary_items,
            WorkOrderSummary(deducible_neto=deductible),
            ["RECONCILIATION FAILED — manual review required"],
        )


def _infer_work_type(item: WorkOrderLineItem) -> str:
    """Infer the primary work type string from an item's set columns."""
    if item.trabajo_externo is not None:
        return "trabajo_externo"
    if item.pintar is not None and item.reparar_leve is None and item.cambiar is None:
        return "pintar"
    if item.cambiar is not None:
        return "sustituir_cambiar"
    if item.desmontar_montar is not None:
        return "sustituir_desmontar_montar"
    if item.reparar_grave is not None:
        return "reparar_grave"
    if item.reparar_mediano is not None:
        return "reparar_mediano"
    if item.reparar_leve is not None:
        return "reparar_leve"
    return "reparar_leve"
