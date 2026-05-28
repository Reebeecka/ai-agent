"""File-edit-verktyg för Part 2 och Part 3.

Editing av enskilda avsnitt av filer är ett krav i Part 2
(se instructions.txt rad 22). Vi använder exakt sträng-match
för find/replace — ingen regex — och kräver att find matchar
exakt 1 gång för att undvika oavsiktliga massbyten.

Workspace-spärr: när `workspace_root` skickas in på någon av funktionerna
nedan tvingas alla path-argument att ligga inom den mappen (efter resolve).
Sker när chat_agent.py kör i github-mode — där vill vi inte att andra
agenter kan trigga edits av filer utanför `agent_workspace/`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileEditResult:
    ok: bool
    message: str
    path: str | None = None


def _resolve_in_workspace(path: str, workspace_root: Path | None) -> str:
    """Relativa paths tolvas mot workspace_root, inte processens cwd."""
    if workspace_root is None:
        return path
    p = Path(path)
    if p.is_absolute():
        return str(p.resolve())
    return str((workspace_root / p).resolve())


def _check_workspace(path: str, workspace_root: Path | None) -> FileEditResult | None:
    """Returnera ett fel-result om path är utanför workspace_root, annars None."""
    if workspace_root is None:
        return None
    try:
        abs_path = Path(_resolve_in_workspace(path, workspace_root)).resolve()
        abs_root = workspace_root.resolve()
        abs_path.relative_to(abs_root)
        return None
    except ValueError:
        return FileEditResult(
            False,
            f"sökvägen är utanför workspace ({workspace_root}) — neka",
            path=path,
        )


def file_edit(
    path: str,
    find: str,
    replace: str,
    *,
    workspace_root: Path | None = None,
) -> FileEditResult:
    """Ersätt en exakt sträng en gång i en fil.

    - find måste matcha *exakt 1* gång (annars fel).
    - Returnerar tydligt felmeddelande som kan skickas tillbaka som tool-output.
    - `workspace_root`: om satt, kräv att path är inom den mappen.
    """
    if not path:
        return FileEditResult(False, "path saknas")

    workspace_err = _check_workspace(path, workspace_root)
    if workspace_err is not None:
        return workspace_err

    resolved = _resolve_in_workspace(path, workspace_root)

    if not os.path.exists(resolved):
        return FileEditResult(False, f"fil hittades ej: {resolved}", path=resolved)

    if not os.path.isfile(resolved):
        return FileEditResult(False, f"{resolved} är inte en vanlig fil", path=resolved)

    try:
        with open(resolved, encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return FileEditResult(
            False, f"{resolved} är inte UTF-8 — kan inte editera", path=resolved
        )

    occurrences = content.count(find)
    if occurrences == 0:
        return FileEditResult(
            False,
            f"find-strängen finns inte i {resolved}",
            path=resolved,
        )
    if occurrences > 1:
        return FileEditResult(
            False,
            f"find-strängen matchar {occurrences} gånger i {resolved} — måste vara unik",
            path=resolved,
        )

    new_content = content.replace(find, replace, 1)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write(new_content)

    return FileEditResult(True, f"ersatte 1 förekomst i {resolved}", path=resolved)


def file_create(
    path: str,
    content: str,
    *,
    overwrite: bool = False,
    workspace_root: Path | None = None,
) -> FileEditResult:
    """Skapa en ny fil. Kräver overwrite=True för att skriva över befintlig.

    `workspace_root`: om satt, kräv att path är inom den mappen.
    """
    if not path:
        return FileEditResult(False, "path saknas")

    workspace_err = _check_workspace(path, workspace_root)
    if workspace_err is not None:
        return workspace_err

    resolved = _resolve_in_workspace(path, workspace_root)

    if os.path.exists(resolved) and not overwrite:
        return FileEditResult(
            False,
            f"{resolved} finns redan (sätt overwrite=true för att skriva över)",
            path=resolved,
        )

    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write(content)
    return FileEditResult(True, f"skapade {resolved}", path=resolved)


def file_read(
    path: str,
    *,
    max_chars: int = 4000,
    workspace_root: Path | None = None,
) -> FileEditResult:
    """Läs en fil och returnera innehållet (med cap).

    `workspace_root`: om satt, kräv att path är inom den mappen.
    """
    workspace_err = _check_workspace(path, workspace_root)
    if workspace_err is not None:
        return workspace_err

    resolved = _resolve_in_workspace(path, workspace_root)

    if not os.path.exists(resolved):
        return FileEditResult(False, f"fil hittades ej: {resolved}", path=resolved)
    try:
        with open(resolved, encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return FileEditResult(False, f"{resolved} är inte UTF-8", path=resolved)

    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... [trunkerad vid {max_chars} tecken] ..."
    return FileEditResult(True, content, path=resolved)
