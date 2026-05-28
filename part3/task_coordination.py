"""Hub-koordinering enligt klassens konvention (Hassan m.fl.).

- Jag tar mig an: <uppgift>  — när agenten claimar / börjar
- Klar med: <uppgift>       — när agenten är färdig
- Bekräftat, jag tar <uppgift> — när någon tilldelar agenten en uppgift
"""

from __future__ import annotations

import re

CLAIM_PREFIX = "Jag tar mig an:"
DONE_PREFIX = "Klar med:"
CONFIRM_PREFIX = "Bekräftat, jag tar"

_ASSIGNMENT_MARKERS = (
    "tar dig",
    "ta dig",
    "tar du på",
    "ta dig på",
    "tar du ",
    "din uppgift",
    "tilldela",
    "ansvar för",
    "sköta ",
    "du ska ",
    "ska du ",
    "kan du ta",
    "ta hand om",
)

_TASK_EXTRACT_RE = [
    re.compile(r"ta[r]?\s+(?:dig|du)\s+på\s+(.+?)(?:\.|$|\n)", re.IGNORECASE),
    re.compile(r"din uppgift[:\s]+(.+?)(?:\.|$|\n)", re.IGNORECASE),
    re.compile(
        r"(?:skapa|bygg|implementera|fixa|reviewa|granska)\s+(.+?)(?:\.|$|\n)",
        re.IGNORECASE,
    ),
]


def is_assignment_message(content: str) -> bool:
    """True om meddelandet tilldelar agenten en konkret uppgift."""
    lower = content.lower()
    return any(m in lower for m in _ASSIGNMENT_MARKERS)


def extract_task_label(trigger_content: str, draft_or_final: str = "") -> str:
    """Kort etikett för uppgiften (max ~80 tecken)."""
    for pattern in _TASK_EXTRACT_RE:
        m = pattern.search(trigger_content)
        if m:
            label = m.group(1).strip()
            if label:
                return label[:80]
    for text in (draft_or_final, trigger_content):
        line = (text or "").strip().split("\n")[0].strip()
        if line and len(line) > 8:
            return line[:80]
    return "uppgiften"


def _has_prefix(text: str, prefix: str) -> bool:
    return prefix.lower() in text.lower()


def ensure_confirm_prefix(text: str, task: str) -> str:
    if _has_prefix(text, CONFIRM_PREFIX) or _has_prefix(text, CLAIM_PREFIX):
        return text
    return f"{CONFIRM_PREFIX} {task}\n\n{text}"


def ensure_claim_prefix(text: str, task: str) -> str:
    if _has_prefix(text, CLAIM_PREFIX) or _has_prefix(text, CONFIRM_PREFIX):
        return text
    return f"{CLAIM_PREFIX} {task}\n\n{text}"


def ensure_done_prefix(text: str, task: str) -> str:
    if _has_prefix(text, DONE_PREFIX):
        return text
    return f"{DONE_PREFIX} {task}\n\n{text}"


def assignment_response_hint(trigger_content: str) -> str:
    """Extra system-instruktion vid tilldelad uppgift."""
    if not is_assignment_message(trigger_content):
        return ""
    task = extract_task_label(trigger_content)
    return (
        f"\n\nUPPGIFTSKONVENTION: Du tilldelades en uppgift. Börja hub-svaret med "
        f"exakt raden `{CONFIRM_PREFIX} {task}` (inga extra ord före). "
        f"Om du levererar kod/filer i samma svar, avsluta med `{DONE_PREFIX} {task}` "
        f"på en egen rad efter leveransen."
    )


def apply_coordination_format(
    final_text: str,
    *,
    trigger_content: str,
    draft: str = "",
    had_deliverables: bool = False,
    started_tools: bool = False,
) -> str:
    """Säkerställ klassens fraser om modellen glömde dem."""
    text = (final_text or "").strip()
    if not text:
        return text

    task = extract_task_label(trigger_content, draft or text)
    assigned = is_assignment_message(trigger_content)

    if assigned:
        text = ensure_confirm_prefix(text, task)
    elif started_tools and not _has_prefix(text, DONE_PREFIX):
        text = ensure_claim_prefix(text, task)

    if had_deliverables:
        text = ensure_done_prefix(text, task)

    return text
