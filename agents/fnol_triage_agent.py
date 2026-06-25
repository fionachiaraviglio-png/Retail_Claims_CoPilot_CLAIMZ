"""AI Agent: FNOL Triage (violet diamond at Claim Report stage)

Performs preliminary coverage assessment at FNOL submission time by reading:
  - The customer's FNOL form data (all fields from the digital form)
  - The policy PDF uploaded by the handler

Returns a structured triage result to the handler before any workshop dispatch.
Auto-approves when confidence >= 90%, decision=COVERED, and no risk flags.

Production upgrade path: replace inline base64 PDF with Anthropic Files API
(client.beta.files.upload + "source": {"type": "file", "file_id": ...})
to avoid re-uploading the same policy document on every claim.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

import anthropic

from config.settings import settings
from models.claim import FNOLData, TriageResult
from models.enums import TriageDecision

from .base_agent import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

AUTO_APPROVE_THRESHOLD = 0.90


class FNOLTriageAgent(BaseAgent):
    """Pre-triage agent: assesses FNOL vs policy PDF before workshop dispatch."""

    @property
    def system_prompt(self) -> str:
        base_prompt = """You are an expert insurance claim pre-triage analyst for a vehicle insurer.
Your job is to read the customer's FNOL (First Notice of Loss) and the policy document,
then determine whether the claim is PRELIMINARILY covered before it is sent to a workshop.

Your analysis must be thorough and objective. Use the policy PDF content to:
1. Identify the covered perils, coverage limits, and deductible
2. Check for exclusions that might apply (racing, drunk driving, unlicensed driver,
   mechanical breakdown, gradual wear, intentional damage, acts of God if not covered, etc.)
3. Cross-reference the FNOL details against the policy terms
4. Detect inconsistencies or risk flags (e.g., damage doesn't match reported incident,
   missing police report for required situations, suspicious circumstances)
5. Generate a clear recommendation for the handler

Return your final assessment as JSON with these exact keys:
{
  "preliminary_decision": "covered|not_covered|conditional|requires_review",
  "matched_clauses": ["list of specific policy clauses that support coverage"],
  "exclusions_found": ["list of exclusions that may apply"],
  "risk_flags": ["list of inconsistencies or suspicious patterns"],
  "missing_info": ["list of additional information needed"],
  "confidence": 0.0-1.0,
  "handler_recommendation": "plain language summary for the handler",
  "auto_approval_eligible": true/false
}

auto_approval_eligible = true ONLY when: decision=covered AND confidence>=0.90 AND risk_flags is empty.
Answer in Spanish."""

        app_env = (settings.app_env or "").strip().lower()
        print(app_env)
        if app_env == "development":
            return (
                f"{base_prompt}\n\n"
                "Please note that claim and policy data can be very sparse because "
                "we are running in a TEST environment."
            )
        return base_prompt

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "extract_policy_coverage_terms",
                "description": "Extract key coverage terms from the policy: covered perils, limits, deductibles, and vehicle coverage scope",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "policy_number": {"type": "string"},
                        "coverage_sections_identified": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Coverage sections found in the policy document",
                        },
                        "covered_perils": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of perils explicitly covered",
                        },
                        "deductible_amount": {"type": "number"},
                        "coverage_limit": {"type": "number"},
                    },
                    "required": ["coverage_sections_identified", "covered_perils"],
                },
            },
            {
                "name": "check_claim_against_exclusions",
                "description": "Check if any policy exclusions apply to this specific claim",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "exclusions_in_policy": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exclusions listed in the policy",
                        },
                        "applicable_exclusions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exclusions that may apply to this specific claim",
                        },
                        "exclusion_confidence": {
                            "type": "number",
                            "description": "Confidence that exclusions apply (0-1)",
                        },
                    },
                    "required": ["exclusions_in_policy", "applicable_exclusions"],
                },
            },
            {
                "name": "validate_claim_circumstances",
                "description": "Validate claim circumstances for consistency and detect risk flags",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "claim_type": {"type": "string"},
                        "incident_description": {"type": "string"},
                        "damaged_parts": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "third_party_involved": {"type": "boolean"},
                        "police_attended": {"type": "boolean"},
                        "police_report_filed": {"type": "boolean"},
                        "insured_was_driver": {"type": "boolean"},
                        "inconsistencies_found": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "risk_flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["claim_type", "inconsistencies_found", "risk_flags"],
                },
            },
            {
                "name": "generate_handler_report",
                "description": "Generate the final triage report for the handler",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "preliminary_decision": {
                            "type": "string",
                            "enum": ["covered", "not_covered", "conditional", "requires_review"],
                        },
                        "matched_clauses": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "exclusions_found": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "risk_flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "missing_info": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "number"},
                        "handler_recommendation": {"type": "string"},
                    },
                    "required": [
                        "preliminary_decision",
                        "matched_clauses",
                        "confidence",
                        "handler_recommendation",
                    ],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "extract_policy_coverage_terms":
            covered = tool_input.get("covered_perils", [])
            sections = tool_input.get("coverage_sections_identified", [])
            return json.dumps({
                "status": "extracted",
                "covered_perils_count": len(covered),
                "sections_found": len(sections),
                "extraction_complete": True,
            })

        if tool_name == "check_claim_against_exclusions":
            applicable = tool_input.get("applicable_exclusions", [])
            confidence = tool_input.get("exclusion_confidence", 0.0)
            return json.dumps({
                "exclusions_applicable": len(applicable) > 0,
                "applicable_count": len(applicable),
                "exclusion_confidence": confidence,
            })

        if tool_name == "validate_claim_circumstances":
            flags = tool_input.get("risk_flags", [])
            inconsistencies = tool_input.get("inconsistencies_found", [])
            all_flags = flags + inconsistencies
            return json.dumps({
                "validation_complete": True,
                "total_flags": len(all_flags),
                "requires_investigation": len(all_flags) > 2,
            })

        if tool_name == "generate_handler_report":
            decision = tool_input.get("preliminary_decision", "requires_review")
            confidence = float(tool_input.get("confidence", 0.0))
            risk_flags = tool_input.get("risk_flags", [])
            auto_eligible = (
                decision == "covered"
                and confidence >= AUTO_APPROVE_THRESHOLD
                and len(risk_flags) == 0
            )
            return json.dumps({
                "report_generated": True,
                "auto_approval_eligible": auto_eligible,
                "decision": decision,
                "confidence": confidence,
            })

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def run_triage(
        self,
        fnol_data: FNOLData,
        pdf_bytes: bytes | None = None,
        policy_json: dict[str, Any] | None = None,
        max_iterations: int = 12,
    ) -> TriageResult:
        """Run triage analysis and return a structured TriageResult.

        Args:
            fnol_data: All form fields from the FNOL submission.
            pdf_bytes: Raw bytes of the policy PDF (inline base64). Pass None to
                       run without the policy document (lower confidence result).
            policy_json: Structured policy data already stored in the DB. Injected
                         as a text block so the agent does not need to re-request it.
        """
        # Build the content blocks for the first user message
        content: list[dict[str, Any]] = []

        if pdf_bytes:
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
                "title": "Policy Document",
                "cache_control": {"type": "ephemeral"},
            })
        elif policy_json:
            content.append({
                "type": "text",
                "text": (
                    "NOTE: No policy PDF was provided. "
                    "The following structured policy data is available from the database "
                    "and should be used as the primary policy reference:\n\n"
                    f"POLICY DATA:\n{json.dumps(policy_json, indent=2, ensure_ascii=False)}"
                ),
            })
        else:
            content.append({
                "type": "text",
                "text": (
                    "NOTE: No policy PDF or structured policy data was provided. "
                    "Base your analysis on the FNOL data alone and flag missing "
                    "policy as a missing_info item."
                ),
            })

        if policy_json and pdf_bytes:
            # Both available: add JSON as supplementary context after the PDF block
            content.append({
                "type": "text",
                "text": (
                    "Additional structured policy data from the database "
                    "(use alongside the PDF above):\n\n"
                    f"POLICY DATA:\n{json.dumps(policy_json, indent=2, ensure_ascii=False)}"
                ),
            })

        # Flatten damaged parts for the context
        all_parts = []
        for group in fnol_data.damaged_parts:
            all_parts.extend([f"{group.zone}/{p}" for p in group.parts])

        fnol_summary = {
            "claim_type": fnol_data.claim_type,
            "license_plate": fnol_data.license_plate,
            "reporter_is_policy_holder": fnol_data.reporter_is_policy_holder,
            "reporter_relationship": fnol_data.reporter_relationship,
            "incident_date": fnol_data.incident_date,
            "incident_time": fnol_data.incident_time,
            "incident_location": f"{fnol_data.incident_street} {fnol_data.incident_number}, {fnol_data.incident_commune}, {fnol_data.incident_region}",
            "damaged_parts": all_parts,
            "incident_description": fnol_data.incident_description,
            "photos_submitted": fnol_data.photos_count > 0,
            "insured_was_driver": fnol_data.insured_was_driver,
            "third_party_involved": fnol_data.third_party_involved,
            "police_attended": fnol_data.police_attended,
            "police_report_filed": fnol_data.police_report_filed,
            "police_report_number": fnol_data.police_report_number,
        }

        content.append({
            "type": "text",
            "text": (
                "Please perform a complete pre-triage analysis for this FNOL submission.\n\n"
                f"FNOL DATA:\n{json.dumps(fnol_summary, indent=2, ensure_ascii=False)}\n\n"
                "Return your final JSON assessment directly in this same response."
            ),
        })

        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        final_text = ""
        # Batch mode: single model call, single response (no iterative tool rounds).
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            messages=messages,
        )

        logger.debug(
            "FNOLTriageAgent batch stop_reason=%s",
            response.stop_reason,
        )

        for block in response.content:
            if block.type == "text":
                final_text = block.text
                break

        return self._build_triage_result(final_text)

    def _build_triage_result(self, text: str) -> TriageResult:
        """Parse agent output into a structured TriageResult."""
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])

                decision_str = data.get("preliminary_decision", "requires_review")
                try:
                    decision = TriageDecision(decision_str)
                except ValueError:
                    decision = TriageDecision.REQUIRES_REVIEW

                confidence = float(data.get("confidence", 0.0))
                risk_flags = data.get("risk_flags", [])
                auto_eligible = (
                    decision == TriageDecision.COVERED
                    and confidence >= AUTO_APPROVE_THRESHOLD
                    and len(risk_flags) == 0
                )

                return TriageResult(
                    preliminary_decision=decision,
                    matched_clauses=data.get("matched_clauses", []),
                    exclusions_found=data.get("exclusions_found", []),
                    risk_flags=risk_flags,
                    missing_info=data.get("missing_info", []),
                    confidence=confidence,
                    handler_recommendation=data.get("handler_recommendation", text[:500]),
                    auto_approval_eligible=auto_eligible,
                )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # Fallback when JSON extraction fails
        return TriageResult(
            preliminary_decision=TriageDecision.REQUIRES_REVIEW,
            missing_info=["Policy PDF analysis failed — manual review required"],
            confidence=0.0,
            handler_recommendation=text[:500] if text else "Unable to complete triage — manual review required",
            auto_approval_eligible=False,
        )

    # Keep the standard run() for compatibility with BaseAgent callers
    async def run(self, user_message: str, context: dict[str, Any] | None = None, max_iterations: int = 10) -> AgentResult:
        result = await self.run_triage(
            fnol_data=FNOLData(
                reporter_name="unknown",
                reporter_rut="",
                reporter_phone="",
                reporter_email="",
                license_plate="",
                incident_description=user_message,
            ),
            pdf_bytes=None,
        )
        return AgentResult(
            success=True,
            data=result.model_dump(mode="json"),
            recommendation=result.handler_recommendation,
            confidence=result.confidence,
            raw_response=result.handler_recommendation,
        )
