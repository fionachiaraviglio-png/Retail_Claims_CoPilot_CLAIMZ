"""AI Agent: Work Order Draft (Phase 1 of the unified WO step)

Analyzes workshop damage photos and produces a preliminary Work Order in the
Chilean insurance WO format (Descripción de los trabajos table):
  Sustituir (Desmontar y Montar / Cambiar / Valor Repuesto)
  Reparar (Leve / Mediano / Grave)
  Pintar
  Trabajo Externo

Costs are intentionally LEFT EMPTY — the draft only defines what work is needed
and provides a per-item coverage recommendation for the handler to review.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from models.claim import WorkOrderLineItem

from .base_agent import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

# Detect image media type from magic bytes
def _detect_media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"  # safe default for workshop photos


class WorkOrderDraftAgent(BaseAgent):

    @property
    def system_prompt(self) -> str:
        return """You are an expert vehicle damage assessor for an insurance company.
Your task is to analyze workshop damage photos and create a preliminary Work Order
in the Chilean insurance format used by repair workshops.

Work Order structure — each damaged item becomes a line item with ONE primary column checked:
- Sustituir > Cambiar: part must be replaced (also note Valor Repuesto for part cost column)
- Sustituir > Desmontar y Montar: part removed/reinstalled but reused
- Reparar > Leve: minor repair (dents, scratches)
- Reparar > Mediano: moderate repair (panel reshaping)
- Reparar > Grave: major structural repair
- Pintar: repainting (often accompanies Reparar)
- Trabajo Externo: specialized external service (locksmith, glass, upholstery)

IMPORTANT: Leave ALL cost/hour values as null — you do not know the prices yet.
Only set the work category indicator (the column that applies to each item).

For EACH line item you must provide:
- ai_recommendation: "cover" or "do_not_cover"
- ai_reason: one sentence explaining why

Coverage guidance:
- Cover: damage consistent with the reported incident, normal repair items
- Do not cover: pre-existing wear, unrelated damage, luxury upgrades, items not visible in photos

Use all tools in order, then return JSON with key "line_items": array of work order items."""

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "identify_damage_items",
                "description": "Identify all damaged components visible in the photos",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "visible_damage": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "component": {"type": "string"},
                                    "damage_description": {"type": "string"},
                                    "estimated_severity": {
                                        "type": "string",
                                        "enum": ["leve", "mediano", "grave"],
                                    },
                                    "needs_replacement": {"type": "boolean"},
                                },
                                "required": ["component", "damage_description", "needs_replacement"],
                            },
                        },
                        "additional_items_from_context": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Items that logically follow from the damage (e.g. painting after panel repair)",
                        },
                    },
                    "required": ["visible_damage"],
                },
            },
            {
                "name": "classify_work_type",
                "description": "Assign each damage item to the correct WO column",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "classifications": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "component": {"type": "string"},
                                    "primary_work_type": {
                                        "type": "string",
                                        "enum": [
                                            "sustituir_cambiar",
                                            "sustituir_desmontar_montar",
                                            "reparar_leve",
                                            "reparar_mediano",
                                            "reparar_grave",
                                            "pintar",
                                            "trabajo_externo",
                                        ],
                                    },
                                    "also_needs_painting": {"type": "boolean"},
                                    "also_needs_valor_repuesto": {
                                        "type": "boolean",
                                        "description": "True for replacement parts — triggers Valor Repuesto column",
                                    },
                                },
                                "required": ["component", "primary_work_type"],
                            },
                        }
                    },
                    "required": ["classifications"],
                },
            },
            {
                "name": "recommend_coverage_per_item",
                "description": "For each identified work item, recommend whether insurance should cover it",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "recommendations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "component": {"type": "string"},
                                    "should_cover": {"type": "boolean"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["component", "should_cover", "reason"],
                            },
                        }
                    },
                    "required": ["recommendations"],
                },
            },
            {
                "name": "build_wo_line_items",
                "description": "Assemble the final preliminary WO line items with all fields",
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
                                    "work_type": {
                                        "type": "string",
                                        "enum": [
                                            "sustituir_cambiar",
                                            "sustituir_desmontar_montar",
                                            "reparar_leve",
                                            "reparar_mediano",
                                            "reparar_grave",
                                            "pintar",
                                            "trabajo_externo",
                                        ],
                                    },
                                    "also_needs_painting": {"type": "boolean", "default": False},
                                    "ai_recommendation": {
                                        "type": "string",
                                        "enum": ["cover", "do_not_cover"],
                                    },
                                    "ai_reason": {"type": "string"},
                                },
                                "required": [
                                    "item_number",
                                    "description",
                                    "work_type",
                                    "ai_recommendation",
                                    "ai_reason",
                                ],
                            },
                        }
                    },
                    "required": ["line_items"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "identify_damage_items":
            items = tool_input.get("visible_damage", [])
            extra = tool_input.get("additional_items_from_context", [])
            return json.dumps({
                "identified": len(items),
                "additional_inferred": len(extra),
                "ready_for_classification": True,
            })

        if tool_name == "classify_work_type":
            classifications = tool_input.get("classifications", [])
            return json.dumps({
                "classified": len(classifications),
                "has_painting": any(c.get("also_needs_painting") for c in classifications),
                "has_replacements": any(c.get("also_needs_valor_repuesto") for c in classifications),
            })

        if tool_name == "recommend_coverage_per_item":
            recs = tool_input.get("recommendations", [])
            covered = sum(1 for r in recs if r.get("should_cover"))
            return json.dumps({
                "total_items": len(recs),
                "recommend_cover": covered,
                "recommend_reject": len(recs) - covered,
            })

        if tool_name == "build_wo_line_items":
            items = tool_input.get("line_items", [])
            return json.dumps({
                "wo_built": True,
                "line_items_count": len(items),
            })

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def run_with_photos(
        self,
        photo_bytes_list: list[bytes],
        context: dict[str, Any] | None = None,
        max_iterations: int = 12,
    ) -> list[WorkOrderLineItem]:
        """Analyze workshop damage photos and return preliminary WO line items.

        Args:
            photo_bytes_list: Workshop damage photos (JPEG/PNG/WebP).
            context: Optional dict with vehicle info, damage description, affected_areas.
        """
        content: list[dict[str, Any]] = []

        for i, photo in enumerate(photo_bytes_list):
            media_type = _detect_media_type(photo)
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(photo).decode(),
                },
            })
            logger.debug("Added photo %d (%s, %d bytes)", i + 1, media_type, len(photo))

        ctx_text = ""
        if context:
            ctx_text = f"\n\nAdditional context:\n{json.dumps(context, indent=2, ensure_ascii=False)}"

        content.append({
            "type": "text",
            "text": (
                f"Analyze these {len(photo_bytes_list)} workshop damage photo(s) and create a "
                f"preliminary Work Order in the Chilean insurance WO format.{ctx_text}\n\n"
                "Use all four tools in order: identify_damage_items → classify_work_type → "
                "recommend_coverage_per_item → build_wo_line_items.\n"
                "Return your final JSON with key 'line_items': array of work order items."
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
                    result = await self._execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})

        return self._parse_line_items(final_text)

    def _parse_line_items(self, text: str) -> list[WorkOrderLineItem]:
        """Parse agent output into structured WorkOrderLineItem list."""
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                raw_items = data.get("line_items", [])
                items = []
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        continue
                    work_type = raw.get("work_type", "")
                    item = WorkOrderLineItem(
                        item_number=raw.get("item_number", len(items) + 1),
                        description=raw.get("description", ""),
                        ai_recommendation=raw.get("ai_recommendation", "cover"),
                        ai_reason=raw.get("ai_reason", ""),
                    )
                    # Map work_type to the correct column flag
                    # Costs remain None — only the type indicator is set (sentinel True → 0.0)
                    if work_type == "sustituir_cambiar":
                        item.cambiar = 0.0          # placeholder — workshop fills in hours
                        item.valor_repuesto = 0.0    # placeholder — workshop fills in part cost
                    elif work_type == "sustituir_desmontar_montar":
                        item.desmontar_montar = 0.0
                    elif work_type == "reparar_leve":
                        item.reparar_leve = 0.0
                    elif work_type == "reparar_mediano":
                        item.reparar_mediano = 0.0
                    elif work_type == "reparar_grave":
                        item.reparar_grave = 0.0
                    elif work_type == "pintar":
                        item.pintar = 0.0
                    elif work_type == "trabajo_externo":
                        item.trabajo_externo = 0.0

                    if raw.get("also_needs_painting") and item.pintar is None:
                        item.pintar = 0.0

                    items.append(item)
                return items
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # Fallback: return a single placeholder item
        return [
            WorkOrderLineItem(
                item_number=1,
                description="ANALYSIS FAILED — manual entry required",
                ai_recommendation="do_not_cover",
                ai_reason="Could not parse damage from photos",
            )
        ]
