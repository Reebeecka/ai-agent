"""Kodunderlag från hub — review (ärlighet) eller writer (implementation)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from part3.collaboration import is_writer_role

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

_EVIDENCE_TRIGGER_MARKERS = (
    "hur ser koden",
    "inte sett",
    "inte sett koden",
    "dela koden",
    "skicka koden",
    "dela kod",
    "var kom koden",
    "var kom den koden",
    "kodereview på något",
    "review på något du inte",
    "granskat",
    "reviewat",
    "share the code",
    "share its code",
)

_REVIEW_ROLE_MARKERS = (
    "kod-review",
    "code review",
    "granska",
    "feedback på",
    "gått igenom",
    "review av",
)

_FILE_HINT_RE = re.compile(
    r"([\w./-]+\.(?:py|md|txt|yaml|yml))",
    re.IGNORECASE,
)


@dataclass
class HubCodeBlock:
    author: str
    seq: int
    lang: str
    code: str
    filename_hint: str | None = None


@dataclass
class ReviewEvidence:
    hint: str
    force_tools: bool
    has_thread_code: bool
    saved_paths: list[str]
    source_citation: str
    writer_mode: bool = False


def _filename_in_message(content: str) -> str | None:
    m = _FILE_HINT_RE.search(content)
    return m.group(1) if m else None


def extract_hub_code_blocks(
    messages: list[dict[str, Any]],
    *,
    self_agent_name: str,
) -> list[HubCodeBlock]:
    blocks: list[HubCodeBlock] = []
    for msg in messages:
        author = (msg.get("agent_name") or "?").strip()
        if author == self_agent_name:
            continue
        content = msg.get("content") or ""
        seq = int(msg.get("seq") or 0)
        file_hint = _filename_in_message(content)
        for m in _FENCE_RE.finditer(content):
            code = m.group(2)
            if code.endswith("\n"):
                code = code[:-1]
            if len(code.splitlines()) < 3:
                continue
            blocks.append(
                HubCodeBlock(
                    author=author,
                    seq=seq,
                    lang=m.group(1) or "python",
                    code=code,
                    filename_hint=file_hint,
                )
            )
    return blocks


def requires_code_evidence(trigger_content: str) -> bool:
    lower = trigger_content.lower()
    return any(m in lower for m in _EVIDENCE_TRIGGER_MARKERS)


def is_review_shaped_response(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in _REVIEW_ROLE_MARKERS) or (
        "klar med" in lower and ("review" in lower or "gransk" in lower)
    )


def _pick_block(
    blocks: list[HubCodeBlock],
    trigger_content: str,
) -> HubCodeBlock | None:
    if not blocks:
        return None
    lower = trigger_content.lower()
    for b in reversed(blocks):
        hint = (b.filename_hint or "").lower()
        if hint and hint in lower:
            return b
    return blocks[-1]


def persist_hub_blocks(
    blocks: list[HubCodeBlock],
    *,
    workspace_root: Path,
) -> list[str]:
    saved: list[str] = []
    mirror = workspace_root / "hub_mirror"
    mirror.mkdir(parents=True, exist_ok=True)
    for b in blocks[-8:]:
        safe_author = re.sub(r"[^\w-]+", "_", b.author)[:40]
        base = b.filename_hint or "snippet.py"
        if not base.endswith((".py", ".md", ".txt")):
            base += ".py"
        name = f"{Path(base).stem}_from_{safe_author}_seq{b.seq}{Path(base).suffix}"
        rel = Path("hub_mirror") / name
        dest = workspace_root / rel
        if dest.exists() and dest.read_text(encoding="utf-8") == b.code + "\n":
            saved.append(rel.as_posix())
            continue
        dest.write_text(b.code + "\n", encoding="utf-8")
        saved.append(rel.as_posix())
    return saved


def _prepare_review_evidence_inner(
    messages: list[dict[str, Any]],
    *,
    trigger_content: str,
    self_agent_name: str,
    workspace_root: Path | None,
) -> ReviewEvidence:
    blocks = extract_hub_code_blocks(messages, self_agent_name=self_agent_name)
    chosen = _pick_block(blocks, trigger_content)
    saved: list[str] = []
    if workspace_root is not None and blocks:
        saved = persist_hub_blocks(blocks, workspace_root=workspace_root)

    needs = requires_code_evidence(trigger_content)
    has_code = chosen is not None

    if not needs and not is_review_shaped_response(trigger_content):
        return ReviewEvidence("", False, has_code, saved, "", writer_mode=False)

    if not has_code:
        return ReviewEvidence(
            hint=(
                "\n\n=== KODUNDERLAG (OBLIGATORISKT) ===\n"
                "Det finns INGEN ```-kod från andra i tråden som du kan reviewa. "
                "Säg ärligt: `STATUS: jag har inte sett källkoden än — kan någon "
                "posta filen i chatten?` "
                "LJUG INTE om att ha granskat filer du inte sett. "
                "Kör `file_read` på hub_mirror/ eller workspace om fil finns lokalt."
            ),
            force_tools=bool(saved),
            has_thread_code=False,
            saved_paths=saved,
            source_citation="",
            writer_mode=False,
        )

    cite = f"[{chosen.author}] seq={chosen.seq}"
    file_note = f" ({chosen.filename_hint})" if chosen.filename_hint else ""
    preview = chosen.code[:2500]
    if len(chosen.code) > 2500:
        preview += "\n# ... [trunkerad för prompt] ..."

    var_kom = "var kom" in trigger_content.lower()
    provenance = ""
    if var_kom:
        provenance = (
            f"Förklara var koden kommer från: t.ex. kopierad från {cite}{file_note} "
            "(inte påhittad). "
        )

    hint = (
        f"\n\n=== KODUNDERLAG FRÅN HUB (använd detta, hitta inte på) ===\n"
        f"Källa: {cite}{file_note}. {provenance}"
        f"Basera svar på EXAKT denna kod — citera radnr om möjligt.\n"
        f"```\n{preview}\n```"
    )
    if saved:
        hint += f"\nSparad lokalt: {', '.join(saved[-3:])} — du kan file_read dessa."

    return ReviewEvidence(
        hint=hint,
        force_tools=True,
        has_thread_code=True,
        saved_paths=saved,
        source_citation=cite + file_note,
        writer_mode=False,
    )


def prepare_role_evidence(
    messages: list[dict[str, Any]],
    *,
    trigger_content: str,
    self_agent_name: str,
    workspace_root: Path | None,
    agent_role_mode: str = "",
    agent_role: str = "",
) -> ReviewEvidence:
    """Review-läge: ärlig granskning. Writer-läge: implementera, inte fejk-review."""
    writer = is_writer_role(agent_role_mode, agent_role)
    if writer:
        inner = _prepare_review_evidence_inner(
            messages,
            trigger_content=trigger_content,
            self_agent_name=self_agent_name,
            workspace_root=workspace_root,
        )
        if requires_code_evidence(trigger_content) or "var kom" in trigger_content.lower():
            inner.writer_mode = True
            return inner
        return ReviewEvidence(
            hint=(
                "\n\n=== KODSKRIVARE (inte reviewer) ===\n"
                "Du implementerar din tilldelade modul — gör INTE kod-review om "
                "ingen uttryckligen bad dig granska. Andra agenter äger review/UI/test. "
                "Flöde: `Jag tar mig an:` → file_create → `RESULT:` + ```-block → "
                "`Klar med:` när levererat."
            ),
            force_tools=True,
            has_thread_code=inner.has_thread_code,
            saved_paths=inner.saved_paths,
            source_citation="",
            writer_mode=True,
        )
    ev = _prepare_review_evidence_inner(
        messages,
        trigger_content=trigger_content,
        self_agent_name=self_agent_name,
        workspace_root=workspace_root,
    )
    return ev


def prepare_review_evidence(
    messages: list[dict[str, Any]],
    *,
    trigger_content: str,
    self_agent_name: str,
    workspace_root: Path | None,
) -> ReviewEvidence:
    """Bakåtkompatibelt — anrop utan roll."""
    return _prepare_review_evidence_inner(
        messages,
        trigger_content=trigger_content,
        self_agent_name=self_agent_name,
        workspace_root=workspace_root,
    )


def strip_pass_leak(text: str) -> str:
    lines = (text or "").splitlines()
    cleaned = [ln for ln in lines if ln.strip().upper() != "PASS"]
    return "\n".join(cleaned).strip()


def ensure_honest_review_prefix(
    text: str,
    *,
    evidence: ReviewEvidence,
) -> str:
    if evidence.writer_mode:
        return text
    if evidence.has_thread_code or not is_review_shaped_response(text):
        return text
    if "STATUS:" in text or "inte sett" in text.lower():
        return text
    return (
        "STATUS: jag har inte faktisk källkod i tråden att granska — "
        "väntar på att någon postar koden i chatten.\n\n"
        + text
    )
