"""Tunn wrapper kring OpenAI Chat Completions.

Centralt ställe för alla LLM-anrop i Part 1, 2 och 3.
Loggar token-användning så att Part 3 kan budgetera i realtid.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@dataclass
class UsageTracker:
    """Räknar tokens över hela agentens livstid."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def add(self, usage: Any) -> None:
        if usage is None:
            return
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        self.call_count += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "call_count": self.call_count,
        }


@dataclass
class LLMClient:
    """OpenAI-klient + usage-tracker."""

    model: str = "gpt-4o-mini"
    usage: UsageTracker = field(default_factory=UsageTracker)
    _client: OpenAI | None = None

    def __post_init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY saknas. Skapa .env från .env.example och fyll i din nyckel."
            )
        self._client = OpenAI(api_key=api_key)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Any:
        """Skickar en chat-completion-förfrågan och uppdaterar usage."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools is not None:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        assert self._client is not None
        response = self._client.chat.completions.create(**kwargs)
        self.usage.add(response.usage)
        return response
