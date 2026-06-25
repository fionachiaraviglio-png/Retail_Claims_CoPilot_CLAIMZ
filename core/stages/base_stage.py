from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from models.claim import Claim, StageTransition, AgentAnalysis
from models.enums import WorkflowStage

logger = logging.getLogger(__name__)


class StageResult(BaseModel):
    success: bool
    next_stage: WorkflowStage | None
    updated_claim: Claim
    agent_analysis: AgentAnalysis | None = None
    notifications_sent: list[dict[str, Any]] = []
    messages: list[str] = []

    @field_validator("updated_claim", mode="before")
    @classmethod
    def coerce_claim(cls, v: Any) -> Any:
        # Convert any model instance to a dict so Pydantic can validate it
        # against the current Claim class, avoiding hot-reload class-identity mismatches.
        if hasattr(v, "model_dump"):
            return v.model_dump(mode="python")
        return v


class BaseStage(ABC):
    """Abstract base class for all workflow stages.

    Each stage:
    1. Validates prerequisites before entry
    2. Processes the stage logic (may invoke AI agents)
    3. Updates the claim with results
    4. Records the stage transition
    5. Returns a StageResult with the updated claim and next stage
    """

    @property
    @abstractmethod
    def stage(self) -> WorkflowStage:
        """The workflow stage this class handles."""

    @property
    @abstractmethod
    def next_stage(self) -> WorkflowStage | None:
        """The default next stage after this one completes."""

    @property
    def required_fields(self) -> list[str]:
        """Claim fields that must be set before entering this stage."""
        return []

    def validate_entry(self, claim: Claim) -> list[str]:
        """Return a list of validation errors, or empty list if valid."""
        errors = []
        for field in self.required_fields:
            if getattr(claim, field, None) is None:
                errors.append(f"Required field missing: {field}")
        return errors

    @abstractmethod
    async def process(self, claim: Claim, data: dict[str, Any] | None = None) -> StageResult:
        """Process the stage logic and return a StageResult."""

    def _record_transition(self, claim: Claim, notes: str | None = None) -> Claim:
        """Record the stage entry in the claim's history."""
        transition = StageTransition(
            from_stage=claim.current_stage if claim.current_stage != self.stage else None,
            to_stage=self.stage,
            transitioned_by="system",
            notes=notes,
        )
        claim.stage_history.append(transition)
        claim.current_stage = self.stage
        claim.updated_at = datetime.utcnow()
        return claim

    def _add_agent_analysis(self, claim: Claim, analysis: AgentAnalysis) -> Claim:
        """Attach an agent analysis record to the claim."""
        claim.agent_analyses.append(analysis)
        return claim

    def _build_stage_result(
        self,
        claim: Claim,
        success: bool = True,
        next_stage: WorkflowStage | None = None,
        agent_analysis: AgentAnalysis | None = None,
        notifications_sent: list[dict[str, Any]] | None = None,
        messages: list[str] | None = None,
    ) -> StageResult:
        return StageResult(
            success=success,
            next_stage=next_stage if next_stage is not None else self.next_stage,
            updated_claim=claim,
            agent_analysis=agent_analysis,
            notifications_sent=notifications_sent or [],
            messages=messages or [],
        )
