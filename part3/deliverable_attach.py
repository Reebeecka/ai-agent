"""Bifoga skapade/redigerade filer till hub-svar efter tool-loopen.

Deterministiskt — LLM kan sammanfatta i RESULT, men vi läser filerna från
disk och lägger till kodblock så andra agenter faktiskt ser innehållet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_LANG_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "bash",
    ".html": "html",
    ".css": "css",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".txt": "text",
}

_TOOL_PATH_RE = re.compile(
    r"ok=True:\s*(?:skapade|ersatte\s+\d+\s+förekomst\s+i)\s+(.+)$",
    re.IGNORECASE,
)


def _fence_lang(path: Path) -> str:
    return _LANG_BY_SUFFIX.get(path.suffix.lower(), "")


def extract_deliverable_paths(messages_appended: list[dict[str, Any]]) -> list[str]:
    """Hitta filvägar som file_create/file_edit lyckades i en tool-loop."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(path: str | None) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        ordered.append(path)

    for msg in messages_appended:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = (tc.get("function") or {}).get("name")
                if fn not in ("file_create", "file_edit"):
                    continue
                raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    continue
                add(args.get("path"))

        if msg.get("role") == "tool":
            content = (msg.get("content") or "").strip()
            m = _TOOL_PATH_RE.match(content)
            if m:
                add(m.group(1).strip())

    return ordered


def _read_file_snippet(
    path: Path,
    *,
    max_lines: int,
    max_chars: int,
) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    return text


def build_attachment_for_hub(
    paths: list[str],
    *,
    workspace_root: Path | None,
    mode: str,
    max_total_chars: int,
    max_lines_per_file: int,
) -> str:
    """mode: 'none' | 'compact' | 'full'."""
    if mode == "none" or not paths:
        return ""
    if mode == "compact" and workspace_root is not None:
        from part3.hub_code_persist import build_compact_attachment

        return build_compact_attachment(paths, workspace_root=workspace_root)
    return build_code_attachment(
        paths,
        workspace_root=workspace_root,
        max_total_chars=max_total_chars,
        max_lines_per_file=max_lines_per_file,
    )


def build_code_attachment(
    paths: list[str],
    *,
    workspace_root: Path | None,
    max_total_chars: int,
    max_lines_per_file: int,
) -> str:
    """Bygg markdown med kodblock för varje levererad fil."""
    if not paths:
        return ""

    parts: list[str] = ["", "---", "DELIVERABLE (auto-bifogat för andra agenter):"]
    ws_resolved = workspace_root.resolve() if workspace_root is not None else None

    for raw_path in paths:
        path = Path(raw_path)
        if ws_resolved is not None:
            path = path if path.is_absolute() else ws_resolved / path
            try:
                path.resolve().relative_to(ws_resolved)
            except ValueError:
                continue
        elif not path.is_absolute():
            path = path.resolve()

        snippet = _read_file_snippet(
            path,
            max_lines=max_lines_per_file,
            max_chars=max_total_chars,
        )
        if snippet is None:
            continue

        lang = _fence_lang(path)
        if ws_resolved is not None:
            try:
                rel_display = path.resolve().relative_to(ws_resolved).as_posix()
            except ValueError:
                rel_display = path.name
        else:
            rel_display = path.name
        header = f"\n**`{path.name}`** (`{rel_display}`):"
        block = f"```{lang}\n{snippet}\n```" if lang else f"```\n{snippet}\n```"
        chunk = header + "\n" + block
        parts.append(chunk)

    if len(parts) <= 3:
        return ""
    return "\n".join(parts)


def append_deliverables_to_hub_message(
    final_text: str,
    attachment: str,
) -> str:
    """Slå ihop LLM-svar + bilaga utan truncation.

    HubClient ansvarar för att dela upp längre meddelanden i flera posts.
    """
    final_text = (final_text or "").strip()
    attachment = (attachment or "").strip()
    if not attachment:
        return final_text

    return f"{final_text}\n{attachment}".strip()
