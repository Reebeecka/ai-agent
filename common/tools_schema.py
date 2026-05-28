"""OpenAI function/tool-schemas för bash, file_edit, file_create, file_read.

Används av Part 2 och Part 3 när de skickar `tools=[...]` till OpenAI.
Schemats `description` är agentens enda källa till hur den ska använda
verktyget — håll dem tydliga.
"""

from __future__ import annotations

from typing import Any

from .bash_tool import DEFAULT_OUTPUT_CAP_CHARS


BASH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Kör ett bash-kommando på den lokala maskinen och returnera "
            f"stdout/stderr/exit_code. Output är begränsad till {DEFAULT_OUTPUT_CAP_CHARS} "
            "tecken per stream — om utdata är längre trunkeras den. Kommandot går "
            "genom en allow-list och kräver y/n-godkännande från användaren. "
            "Destruktiva kommandon (rm -rf /, sudo, curl|sh, ...) blockas automatiskt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash-kommandot som ska köras",
                }
            },
            "required": ["command"],
        },
    },
}

FILE_EDIT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_edit",
        "description": (
            "Ersätt en exakt sträng en gång i en befintlig fil. "
            "`find`-strängen måste matcha exakt 1 gång — om den matchar 0 eller "
            ">1 gånger returneras ett fel utan att filen ändras. Använd för "
            "punktedits av enskilda avsnitt; för helt nya filer använd file_create."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filsökväg"},
                "find": {
                    "type": "string",
                    "description": "Exakt sträng som ska ersättas (måste vara unik i filen)",
                },
                "replace": {"type": "string", "description": "Ny sträng"},
            },
            "required": ["path", "find", "replace"],
        },
    },
}

FILE_CREATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_create",
        "description": (
            "Skapa en ny fil med det angivna innehållet. Returnerar fel om filen "
            "redan finns (om inte overwrite=true sätts)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filsökväg"},
                "content": {"type": "string", "description": "Filens innehåll"},
                "overwrite": {
                    "type": "boolean",
                    "description": "Skriv över om filen redan finns",
                    "default": False,
                },
            },
            "required": ["path", "content"],
        },
    },
}

FILE_READ_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_read",
        "description": (
            f"Läs en fil och returnera innehållet (cap {DEFAULT_OUTPUT_CAP_CHARS} tecken)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filsökväg"},
            },
            "required": ["path"],
        },
    },
}


ALL_TOOLS: list[dict[str, Any]] = [
    BASH_TOOL,
    FILE_EDIT_TOOL,
    FILE_CREATE_TOOL,
    FILE_READ_TOOL,
]
