"""AI Agent: Bill Comparison — final workshop invoice vs approved Work Order.

Takes the workshop's final bill (PDF/images) and the approved final WO line items,
produces a line-by-line comparison showing matches, price discrepancies, and
items in the bill that don't appear in the approved WO.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from models.claim import WorkOrderLineItem

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


def _media_type(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


class BillComparisonAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return """Eres un auditor experto de facturas de talleres para una compañía de seguros.
Tu tarea es comparar la factura final del taller con la Orden de Trabajo (OT) aprobada
y detectar discrepancias.

Para cada ítem de la factura:
1. Busca el ítem correspondiente en la OT aprobada (el nombre puede ser diferente — usa comprensión semántica)
2. Compara el monto de la factura con el monto de la OT
3. Si el ítem de la factura NO está en la OT aprobada: marcar como alerta
4. Si el precio difiere más del 5%: marcar como discrepancia

Devuelve JSON con:
{
  "comparisons": [
    {
      "bill_description": "...",
      "wo_description": "...",       // null si no se encuentra
      "bill_amount": 1234.56,
      "wo_amount": 1200.00,          // null si no se encuentra
      "match": true/false,
      "discrepancy_amount": 34.56,
      "discrepancy_pct": 2.88,
      "is_alert": false,
      "alert_reason": null           // o descripción del problema
    }
  ],
  "total_bill_amount": 12345.00,
  "total_wo_amount": 12000.00,
  "total_discrepancy": 345.00,
  "has_alerts": false,
  "summary": "Resumen de la comparación"
}"""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "extract_bill_items",
                "description": "Extraer todos los ítems y montos de la factura del taller",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string"},
                                    "amount": {"type": "number"},
                                    "quantity": {"type": "number"},
                                    "unit_price": {"type": "number"},
                                },
                                "required": ["description", "amount"],
                            },
                        },
                        "total": {"type": "number"},
                    },
                    "required": ["items", "total"],
                },
            },
            {
                "name": "compare_to_wo",
                "description": "Comparar ítems de la factura con la OT aprobada",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "comparisons": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "bill_description": {"type": "string"},
                                    "wo_description": {"type": "string"},
                                    "bill_amount": {"type": "number"},
                                    "wo_amount": {"type": "number"},
                                    "match": {"type": "boolean"},
                                    "is_alert": {"type": "boolean"},
                                    "alert_reason": {"type": "string"},
                                },
                                "required": ["bill_description", "bill_amount", "match", "is_alert"],
                            },
                        }
                    },
                    "required": ["comparisons"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "extract_bill_items":
            items = tool_input.get("items", [])
            return json.dumps({"extracted": len(items), "total": tool_input.get("total", 0)})

        if tool_name == "compare_to_wo":
            comparisons = tool_input.get("comparisons", [])
            alerts = sum(1 for c in comparisons if c.get("is_alert"))
            mismatches = sum(1 for c in comparisons if not c.get("match") and not c.get("is_alert"))
            return json.dumps({
                "total_comparisons": len(comparisons),
                "alerts": alerts,
                "price_mismatches": mismatches,
            })

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def run_comparison(
        self,
        bill_files: list[bytes],
        wo_line_items: list[WorkOrderLineItem],
        max_iterations: int = 10,
    ) -> dict[str, Any]:
        """Compare bill files against approved WO line items.

        Returns the full comparison dict (suitable for claim.bill_comparison_result).
        """
        content: list[dict[str, Any]] = []

        for i, data in enumerate(bill_files):
            mt = _media_type(data)
            b64 = base64.standard_b64encode(data).decode()
            if mt == "application/pdf":
                content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    "title": f"Factura del taller {i + 1}",
                    "cache_control": {"type": "ephemeral"},
                })
            else:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mt, "data": b64},
                })

        wo_summary = json.dumps(
            [
                {
                    "item_number": i.item_number,
                    "description": i.description,
                    "valor_repuesto": i.valor_repuesto,
                    "reparar_leve": i.reparar_leve,
                    "reparar_mediano": i.reparar_mediano,
                    "reparar_grave": i.reparar_grave,
                    "pintar": i.pintar,
                    "trabajo_externo": i.trabajo_externo,
                    "cambiar": i.cambiar,
                    "desmontar_montar": i.desmontar_montar,
                    "handler_approved": i.handler_approved,
                }
                for i in wo_line_items
                if i.handler_approved is not False
            ],
            indent=2,
            ensure_ascii=False,
        )

        content.append({
            "type": "text",
            "text": (
                f"ORDEN DE TRABAJO APROBADA ({len(wo_line_items)} ítems):\n{wo_summary}\n\n"
                "Por favor compara la factura del taller con esta OT aprobada.\n"
                "Usa ambas herramientas: extract_bill_items → compare_to_wo.\n"
                "Luego devuelve el JSON final con la comparación completa."
            ),
        })

        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        final_text = ""
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
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
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": res})
            messages.append({"role": "user", "content": tool_results})

        try:
            start = final_text.find("{")
            end = final_text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(final_text[start:end])
        except (json.JSONDecodeError, ValueError):
            pass

        return {
            "comparisons": [],
            "total_bill_amount": 0,
            "total_wo_amount": 0,
            "total_discrepancy": 0,
            "has_alerts": False,
            "summary": "No se pudo procesar la factura — revisión manual requerida.",
        }
