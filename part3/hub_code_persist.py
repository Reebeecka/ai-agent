"""Spara hub-kod lokalt — utan att ta bort kod från chatten."""

from __future__ import annotations

import re
from pathlib import Path

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

_LANG_EXT: dict[str, str] = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "bash": ".sh",
    "sh": ".sh",
    "markdown": ".md",
    "md": ".md",
    "json": ".json",
    "yaml": ".yaml",
    "html": ".html",
    "css": ".css",
    "text": ".txt",
    "": ".txt",
}

_FILENAME_HINT_RE = re.compile(
    r"(?:RESULT:|skapade|uppdaterade|fil(?:en)?|file)\s*[`']?"
    r"([\w./-]+\.(?:py|pyw|md|json|yaml|yml|js|ts|tsx|jsx|html|css|sh|txt))"
    r"[`']?",
    re.IGNORECASE,
)

_SAFE_NAME_RE = re.compile(r"^[\w./-]+$")


def resolve_workspace(assignment_root: Path, workspace_rel: str) -> Path:
    root = (assignment_root / workspace_rel).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ext_for_lang(lang: str) -> str:
    return _LANG_EXT.get((lang or "").strip().lower(), ".txt")


def _filename_hint_before(text: str, block_start: int) -> str | None:
    window = text[max(0, block_start - 400) : block_start]
    matches = list(_FILENAME_HINT_RE.finditer(window))
    if not matches:
        return None
    name = matches[-1].group(1).strip()
    if ".." in name or not _SAFE_NAME_RE.match(name):
        return None
    return Path(name).name


def _save_block(
    code: str,
    *,
    workspace_root: Path,
    rel_path: Path,
) -> str:
    dest = workspace_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code + "\n", encoding="utf-8")
    return rel_path.as_posix()


def persist_hub_codeblocks(
    text: str,
    *,
    workspace_root: Path,
    trim_chat: bool = False,
    max_lines_in_chat: int = 12,
    min_lines_to_persist: int = 2,
    subdir: str = "hub_inbox",
) -> tuple[str, list[str]]:
    """Spara ```-block till workspace. Chatten oförändrad om trim_chat=False."""
    text = text or ""
    if not text.strip():
        return text, []

    saved_rel: list[str] = []
    snippet_counter = 0
    inbox = workspace_root / subdir
    inbox.mkdir(parents=True, exist_ok=True)

    def replace_block(match: re.Match[str]) -> str:
        nonlocal snippet_counter
        original = match.group(0)
        lang = match.group(1) or ""
        code = match.group(2)
        if code.endswith("\n"):
            code = code[:-1]
        lines = code.splitlines()
        if len(lines) < min_lines_to_persist:
            return original

        hint = _filename_hint_before(text, match.start())
        if hint:
            rel = Path(hint)
            saved_rel.append(_save_block(code, workspace_root=workspace_root, rel_path=rel))
        else:
            snippet_counter += 1
            rel = Path(subdir) / f"snippet_{snippet_counter:03d}{_ext_for_lang(lang)}"
            saved_rel.append(_save_block(code, workspace_root=workspace_root, rel_path=rel))

        if not trim_chat:
            return original

        if len(lines) <= max_lines_in_chat:
            snippet = "\n".join(lines)
            fence_lang = lang or ""
            return (
                f"`{saved_rel[-1]}` ({len(lines)} rader, sparad i workspace):\n"
                f"```{fence_lang}\n{snippet}\n```"
            )
        preview = "\n".join(lines[:max_lines_in_chat])
        fence_lang = lang or ""
        return (
            f"`{saved_rel[-1]}` — {len(lines)} rader sparade lokalt "
            f"(hub max {max_lines_in_chat} rader):\n"
            f"```{fence_lang}\n{preview}\n# ...\n```"
        )

    new_text = _FENCE_RE.sub(replace_block, text)
    return new_text, saved_rel


# Bakåtkompatibilitet för tester
def persist_and_trim_hub_code(
    text: str,
    *,
    workspace_root: Path,
    max_lines_in_chat: int = 12,
    min_lines_to_persist: int = 3,
    subdir: str = "hub_inbox",
) -> tuple[str, list[str]]:
    return persist_hub_codeblocks(
        text,
        workspace_root=workspace_root,
        trim_chat=True,
        max_lines_in_chat=max_lines_in_chat,
        min_lines_to_persist=min_lines_to_persist,
        subdir=subdir,
    )


def build_compact_attachment(
    paths: list[str],
    *,
    workspace_root: Path,
) -> str:
    """Kort fil-lista till hubben — ingen full kod."""
    if not paths:
        return ""

    lines: list[str] = [
        "",
        "---",
        "LOKALT (sparad i workspace — full kod på disk):",
    ]
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = workspace_root / path
        if not path.is_file():
            continue
        try:
            n = len(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            n = "?"
        try:
            rel = path.resolve().relative_to(workspace_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        lines.append(f"- `{rel}` ({n} rader)")

    if len(lines) <= 3:
        return ""
    return "\n".join(lines)
