"""Den strukturerade tool-calling-loopen.

Används av Part 2 (CLI-driven) och Part 3 (hub-driven). Loopen:

  1. Skickar messages till OpenAI med tools=[bash, file_edit, file_create, file_read]
  2. Om modellen returnerar tool_calls -> exekvera dem -> appenda resultat -> loop
  3. Om modellen returnerar vanligt text-innehåll -> yield (assistant-message)

Loopen ANSVARAR INTE för:
  - input från användare/hub (det är callerns ansvar)
  - persistering av session.jsonl (det är callerns ansvar, vi exponerar hooks)

Detta gör att Part 2 (console) och Part 3 (hub) kan dela samma kärna.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bash_tool import run_bash
from .file_edit_tool import file_create, file_edit, file_read
from .llm_client import LLMClient
from .safety import check_and_confirm


MAX_TOOL_ROUNDS_DEFAULT = 10


@dataclass
class LoopConfig:
    max_tool_rounds: int = MAX_TOOL_ROUNDS_DEFAULT
    auto_yes: bool = False
    temperature: float = 0.2
    # Github-mode: utökar safety-allowlist:n med git/gh-kommandon och
    # tvingar workspace-spärr på file-tools. Skickas vidare till
    # check_and_confirm och file_*.
    github_mode: bool = False
    workspace_root: Path | None = None


@dataclass
class LoopResult:
    final_text: str
    rounds_used: int
    tool_calls_made: int
    messages_appended: list[dict[str, Any]] = field(default_factory=list)


OnMessageHook = Callable[[dict[str, Any]], None]


def _execute_tool_call(
    tool_call: Any,
    *,
    auto_yes: bool,
    github_mode: bool = False,
    workspace_root: Path | None = None,
) -> str:
    """Kör ett enskilt tool_call och returnera output-strängen."""
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as e:
        return f"FEL: tool-argumenten kunde inte parsas som JSON: {e}"

    bash_cwd = str(workspace_root) if workspace_root is not None else None

    if name == "bash":
        command = args.get("command", "")
        verdict = check_and_confirm(
            command,
            auto_yes=auto_yes,
            github_mode=github_mode,
        )
        if not verdict.allowed:
            return f"BLOCKAD: {verdict.reason}"
        result = run_bash(command, cwd=bash_cwd)
        return result.as_observation()

    if name == "file_edit":
        result = file_edit(
            args.get("path", ""),
            args.get("find", ""),
            args.get("replace", ""),
            workspace_root=workspace_root,
        )
        return f"ok={result.ok}: {result.message}"

    if name == "file_create":
        result = file_create(
            args.get("path", ""),
            args.get("content", ""),
            overwrite=bool(args.get("overwrite", False)),
            workspace_root=workspace_root,
        )
        return f"ok={result.ok}: {result.message}"

    if name == "file_read":
        result = file_read(args.get("path", ""), workspace_root=workspace_root)
        return result.message if result.ok else f"FEL: {result.message}"

    return f"FEL: okänt tool: {name}"


def run_structured_loop(
    client: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    config: LoopConfig | None = None,
    on_message: OnMessageHook | None = None,
) -> LoopResult:
    """Kör structured tool-calling tills modellen yieldar text.

    `messages` muteras inte. Använd `result.messages_appended` för att
    veta vad som tillkommit (för session-persistens).
    """
    cfg = config or LoopConfig()
    working = list(messages)
    appended: list[dict[str, Any]] = []
    tool_calls_made = 0

    def append(msg: dict[str, Any]) -> None:
        working.append(msg)
        appended.append(msg)
        if on_message is not None:
            on_message(msg)

    for round_idx in range(cfg.max_tool_rounds):
        response = client.chat(
            working,
            tools=tools,
            tool_choice="auto",
            temperature=cfg.temperature,
        )
        choice = response.choices[0]
        message = choice.message

        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if message.content:
            assistant_msg["content"] = message.content
        if message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        append(assistant_msg)

        if not message.tool_calls:
            return LoopResult(
                final_text=message.content or "",
                rounds_used=round_idx + 1,
                tool_calls_made=tool_calls_made,
                messages_appended=appended,
            )

        for tool_call in message.tool_calls:
            tool_calls_made += 1
            output = _execute_tool_call(
                tool_call,
                auto_yes=cfg.auto_yes,
                github_mode=cfg.github_mode,
                workspace_root=cfg.workspace_root,
            )
            append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                }
            )

    return LoopResult(
        final_text="(max_tool_rounds nådd utan slutgiltigt svar)",
        rounds_used=cfg.max_tool_rounds,
        tool_calls_made=tool_calls_made,
        messages_appended=appended,
    )
