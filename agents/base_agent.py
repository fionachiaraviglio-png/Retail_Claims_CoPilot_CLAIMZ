from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import anthropic
from pydantic import BaseModel

from config.settings import settings

logger = logging.getLogger(__name__)


class AgentResult(BaseModel):
    success: bool
    data: dict[str, Any]
    recommendation: str
    confidence: float
    raw_response: str


class BaseAgent(ABC):
    """Abstract base class for all ClearProcess AI agents.

    Each agent wraps an Anthropic LLM with a specific set of tools
    and domain expertise for a particular stage of the claim workflow.
    """

    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        if client is not None:
            self.client = client
        else:
            client_kwargs: dict[str, Any] = {
                "api_key": settings.anthropic_api_key,
            }
            if settings.anthropic_base_url:
                client_kwargs["base_url"] = settings.anthropic_base_url
            self.client = anthropic.AsyncAnthropic(**client_kwargs)
        self.model = settings.primary_model

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Domain-specific system prompt for this agent."""

    @property
    @abstractmethod
    def tools(self) -> list[dict[str, Any]]:
        """Tool definitions available to this agent."""

    @abstractmethod
    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool call and return the result as a string."""

    async def run(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
        max_iterations: int = 10,
    ) -> AgentResult:
        """Run the agent in batch mode (single request, single response)."""
        if context:
            context_json = json.dumps(context, indent=2, default=str)
            full_message = f"{user_message}\n\nContext:\n{context_json}"
        else:
            full_message = user_message

        messages = [{"role": "user", "content": full_message}]
        _ = max_iterations  # Kept for backward compatibility with existing callers.

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            # thinking={"type": "adaptive"},
            system=self.system_prompt,
            messages=messages,
        )

        logger.debug(
            "Agent %s batch stop_reason=%s",
            self.__class__.__name__,
            response.stop_reason,
        )

        final_text = ""
        for block in response.content:
            if block.type == "text":
                final_text = block.text
                break

        return self._parse_result(final_text)

    def _parse_result(self, text: str) -> AgentResult:
        """Parse the agent's final text response into a structured result.
        Override in subclasses for domain-specific parsing.
        """
        try:
            # Try to extract JSON from the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return AgentResult(
                    success=True,
                    data=data,
                    recommendation=data.get("recommendation", text),
                    confidence=data.get("confidence", 0.8),
                    raw_response=text,
                )
        except (json.JSONDecodeError, KeyError):
            pass

        return AgentResult(
            success=True,
            data={"response": text},
            recommendation=text,
            confidence=0.7,
            raw_response=text,
        )
