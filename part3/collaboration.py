"""Samarbetsregler för multi-agent hub — peer-claims, roller, kodskrivare."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_CLAIM_LINE_RE = re.compile(
    r"^\s*CLAIM\s+([^\s#:]+)(?:#([^\s:]+))?\s*:?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_CLAIM_INLINE_RE = re.compile(
    r"CLAIM\s+([^\s#:]+)(?:#([^\s:]+))?\s*:?\s*([^\n]+)",
    re.IGNORECASE,
)
_JAG_TAR_RE = re.compile(
    r"Jag tar mig an:\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_BEKRÄFTAT_TAR_RE = re.compile(
    r"Bekräftat,\s*jag tar\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```(?:\w*)\n(.*?)```", re.DOTALL)

_COLLAB_TRIGGER_MARKERS = (
    "tillsammans",
    "arbeta tillsammans",
    "gemensamt",
    "olika roller",
    "roller",
    "dubbelarbete",
    "dubbel arbete",
    "samarbeta",
    "fokus på samarbete",
    "inte göra samma",
    "undvik dubbel",
    "delar upp",
    "strukturera arbetet",
    "vem gör vad",
    "vem vill",
)

_FULL_FILE_MARKERS = (
    "dela hela",
    "hela filen",
    "hela koden",
    "fullständiga",
    "fullständig kod",
    "hela modulen",
    "share the code",
    "share its code",
    "dela koden",
    "skicka koden",
    "posta koden",
    "visa koden",
    "fortsätt rebecka",
    "fortsätt ",
)

_SMALLTALK_MARKERS = (
    "välkommen tillbaka",
    "ser fram emot",
    "trevligt att höra",
    "redo att samarbeta",
    "hej hassan",
    "tack, rebecka",
    "bra jobbat",
    "hur låter det",
    "vad har ni på gång",
    "vad ville du",
)

_SCOPE_UI = ("ui", "gränssnitt", "terminalgränssnitt", "interface", "meny")
_SCOPE_TEST = ("test", "tester", "pytest", "debugging", "debug")
_SCOPE_REVIEW = ("review", "granska", "feedback", "kod-review", "code review")
_SCOPE_CORE = ("beräkningslogik", "logik", "core", "modul", "funktioner")
_SCOPE_DESIGN = ("design", "struktur", "planera")

@dataclass
class PeerTask:
    author: str
    kind: str  # claim | claim_format | done
    task: str
    scopes: tuple[str, ...]


def is_collaboration_context(trigger_content: str, chat_context: str = "") -> bool:
    blob = f"{trigger_content}\n{chat_context}".lower()
    return any(m in blob for m in _COLLAB_TRIGGER_MARKERS)


def is_smalltalk_message(content: str) -> bool:
    lower = content.lower().strip()
    if len(lower) > 200:
        return False
    return any(m in lower for m in _SMALLTALK_MARKERS)


def wants_full_code_in_hub(trigger_content: str) -> bool:
    lower = trigger_content.lower()
    return any(m in lower for m in _FULL_FILE_MARKERS)


def _detect_scopes(text: str) -> tuple[str, ...]:
    lower = text.lower()
    found: list[str] = []
    for label, markers in (
        ("ui", _SCOPE_UI),
        ("test", _SCOPE_TEST),
        ("review", _SCOPE_REVIEW),
        ("core", _SCOPE_CORE),
        ("design", _SCOPE_DESIGN),
    ):
        if any(m in lower for m in markers):
            found.append(label)
    return tuple(found)


def parse_peer_tasks(
    messages: list[dict[str, Any]],
    *,
    self_agent_name: str,
) -> list[PeerTask]:
    """CLAIM + Jag tar mig an + Bekräftat jag tar från andra agenter."""
    tasks: list[PeerTask] = []
    seen: set[tuple[str, str, str]] = set()

    def add(author: str, kind: str, task: str) -> None:
        task = task.strip()[:120]
        if not task:
            return
        key = (author, kind, task[:60])
        if key in seen:
            return
        seen.add(key)
        tasks.append(
            PeerTask(
                author=author,
                kind=kind,
                task=task,
                scopes=_detect_scopes(task),
            )
        )

    for msg in messages:
        author = (msg.get("agent_name") or "").strip()
        if not author or author == self_agent_name:
            continue
        content = msg.get("content") or ""
        for line in content.splitlines():
            stripped = line.strip()
            m = _CLAIM_LINE_RE.match(stripped)
            if m:
                desc = (m.group(3) or m.group(1) or "").strip()
                add(author, "claim", f"{m.group(1)} {desc}".strip())
                continue
            for pat in (_JAG_TAR_RE, _BEKRÄFTAT_TAR_RE):
                m2 = pat.search(stripped)
                if m2:
                    add(author, "claim", m2.group(1).strip())
                    break
        for m in _CLAIM_INLINE_RE.finditer(content):
            add(author, "claim", f"{m.group(1)} {m.group(3) or ''}".strip())
        if "klar med:" in content.lower():
            m3 = re.search(r"Klar med:\s*(.+?)(?:\.|$)", content, re.IGNORECASE)
            if m3:
                add(author, "done", m3.group(1).strip())

    return tasks


def parse_peer_claims(
    messages: list[dict[str, Any]],
    *,
    self_agent_name: str,
) -> list[dict[str, str]]:
    """Bakåtkompatibel dict-lista för äldre anrop."""
    return [
        {
            "author": t.author,
            "resource": t.task,
            "tag": t.kind,
            "description": ",".join(t.scopes),
        }
        for t in parse_peer_tasks(messages, self_agent_name=self_agent_name)
        if t.kind != "done"
    ]


def peer_owns_scopes(peer_tasks: list[PeerTask], scopes: tuple[str, ...]) -> bool:
    for t in peer_tasks:
        if t.kind == "done":
            continue
        if any(s in t.scopes for s in scopes):
            return True
    return False


def suggested_writer_scope(
    peer_tasks: list[PeerTask],
    trigger_content: str,
) -> str:
    """Vad kodskrivaren bör claima när andra tagit UI/test/review."""
    if peer_owns_scopes(peer_tasks, ("ui",)):
        if peer_owns_scopes(peer_tasks, ("test",)):
            return "core-logik i en modul (inga tester/UI)"
        return "core-logik i en modul — inte UI"
    if peer_owns_scopes(peer_tasks, ("test",)):
        return "core-modul / beräkningsfunktioner (andra gör test)"
    if peer_owns_scopes(peer_tasks, ("review",)):
        return "implementation av tilldelad modul (andra gör review)"
    lower = trigger_content.lower()
    if "beräkningslogik" in lower or "logik" in lower:
        return "beräkningslogik (en modul)"
    if "test" in lower and "review" not in lower:
        return "STATUS: test är inte min roll — jag skriver kod"
    return "en tydlig kodmodul (max 1–2 filer) efter rollfördelning"


def is_writer_role(agent_role_mode: str, agent_role: str) -> bool:
    mode = (agent_role_mode or "").lower()
    if mode == "writer":
        return True
    if mode == "reviewer":
        return False
    role = agent_role.lower()
    return any(
        w in role
        for w in ("kodskriv", "implement", "utveckl", "developer", "programmer")
    ) and "review" not in role


def build_collaboration_system_hint(
    *,
    trigger_content: str,
    chat_context: str,
    agent_name: str,
    agent_role: str,
    agent_role_mode: str,
    peer_tasks: list[PeerTask],
) -> str:
    writer = is_writer_role(agent_role_mode, agent_role)
    lines = [
        "\n\n=== SAMARBETE (viktigast — process före brus) ===",
    ]

    if writer:
        scope = suggested_writer_scope(peer_tasks, trigger_content)
        lines.extend(
            [
                f"Din roll: KODSKRIVARE — {agent_role}",
                f"Din scope just nu: {scope}",
                "Andra agenter gör UI, test och kod-review — gör INTE deras jobb.",
                "Gör INTE generisk kod-review, 'bra jobbat'-loopar eller tomma förbättringar.",
                "Flöde: `Jag tar mig an: <smal modul>` → file_create i workspace → "
                "`RESULT: filnamn` + kod i chat → `Klar med: <samma modul>`.",
                "Om någon redan tagit UI/test/review: implementera BARA core/logik de inte äger.",
                "Vid oklar roll: `NEEDS_APPROVAL: får jag ta <X> medan Y har UI?` — vänta på svar.",
            ]
        )
    else:
        lines.extend(
            [
                f"Din roll: {agent_role}",
                "Smalt bidrag — inte hela appen om andra claimat delar.",
            ]
        )

    if peer_tasks:
        lines.append("Andras uppgifter i tråden (respektera — ingen dubbel implementation):")
        for t in peer_tasks[-8:]:
            if t.kind == "done":
                continue
            scopes = f" [{','.join(t.scopes)}]" if t.scopes else ""
            lines.append(f"  - [{t.author}] {t.task}{scopes}")

    if writer and peer_owns_scopes(peer_tasks, ("ui", "test", "review")):
        lines.append(
            "BLOCKERA: Du får INTE leverera UI-skript, testfiler eller review-text "
            "när andra redan claimat det. Skriv bara core/logik i en avgränsad modul."
        )

    if wants_full_code_in_hub(trigger_content):
        lines.append(
            "Användaren vill ha HELA filen i chatten — posta full kodblock, "
            "ingen trunkering. Nämn `agent_workspace/<fil>`."
        )
    elif writer:
        lines.append(
            "Hub: full kod är OK när du levererat en modul; undvik att reposta "
            "samma fil om inget ändrats."
        )

    if "tilldel" in trigger_content.lower() or "din roll" in trigger_content.lower():
        lines.append("Tilldelad roll: börja med `Bekräftat, jag tar <din scope>`.")

    return "\n".join(lines)


def _longest_fence_lines(text: str) -> int:
    return max((len(m.group(1).splitlines()) for m in _FENCE_RE.finditer(text)), default=0)


def enforce_collaboration_on_post(
    text: str,
    *,
    active: bool,
    max_codeblock_lines: int,
    allow_full_code: bool = False,
) -> str:
    """Samarbetsläge får inte längre trunkera kod.

    HubClient delar upp långa meddelanden i flera hub-posts, så full kod kan
    skickas utan att förlora rader.
    """
    return text


def should_skip_duplicate_attach(final_text: str) -> bool:
    return _longest_fence_lines(final_text) >= 12
