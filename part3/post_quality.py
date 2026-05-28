"""Sanera hub-poster: prefix, tomma Klar med, dedup, roll (kodskrivare)."""

from __future__ import annotations

import re
from typing import Any

from part3.task_coordination import CLAIM_PREFIX, CONFIRM_PREFIX, DONE_PREFIX

_FENCE_RE = re.compile(r"```(?:\w*)\n(.*?)```", re.DOTALL)
_RESULT_FILE_RE = re.compile(
    r"RESULT:\s*(?:skapade|uppdaterade)?\s*[`']?([\w./-]+\.(?:py|md|txt|yaml|yml))[`']?",
    re.IGNORECASE,
)
_KLAR_MED_RE = re.compile(r"Klar med:\s*(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL)

_REVIEW_SHAPED = (
    "kod-review",
    "code review",
    "gått igenom",
    "granskat",
    "feedback på",
    "förslag för att förbättra",
    "bra jobbat med",
    "inga ytterligare förslag",
)

_DUP_PREFIX_RE = re.compile(
    r"^(?:Bekräftat,\s*ag tar\s+)?(?:Jag tar mig an:\s*)+",
    re.IGNORECASE | re.MULTILINE,
)


def normalize_coordination_prefixes(text: str) -> str:
    """Ta bort dubbla Jag tar mig an / Bekräftat staplade."""
    text = _DUP_PREFIX_RE.sub("Jag tar mig an: ", text)
    text = re.sub(
        r"^(Bekräftat,\s*ag tar\s+)+Bekräftat,\s*ag tar\s+",
        "Bekräftat, jag tar ",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return text


def extract_posted_files(text: str) -> list[str]:
    files: list[str] = []
    for m in _RESULT_FILE_RE.finditer(text):
        files.append(m.group(1))
    for m in re.finditer(r"[`']([\w./-]+\.py)[`']", text):
        files.append(m.group(1))
    return list(dict.fromkeys(files))


def fingerprint_post(text: str) -> str:
    """Grovt avtryck för dedup."""
    files = extract_posted_files(text)
    if files:
        return "|".join(sorted(files))
    fences = _FENCE_RE.findall(text)
    if fences:
        body = fences[-1][:200]
        return f"fence:{hash(body)}"
    return text[:80].lower()


def is_duplicate_of_recent(
    text: str,
    recent_fingerprints: list[str],
) -> bool:
    fp = fingerprint_post(text)
    return fp in recent_fingerprints


def klar_med_has_substance(text: str, *, had_deliverables: bool) -> bool:
    if had_deliverables:
        return True
    if _FENCE_RE.search(text):
        return True
    if _RESULT_FILE_RE.search(text):
        return True
    lower = text.lower()
    if "klar med" not in lower:
        return True
    substantive = any(
        w in lower
        for w in (
            "def ",
            "class ",
            "import ",
            "pytest",
            "file_create",
            "```",
            "result:",
        )
    )
    return substantive


def block_writer_doing_review(text: str, *, writer_mode: bool) -> str:
    if not writer_mode:
        return text
    lower = text.lower()
    if not any(m in lower for m in _REVIEW_SHAPED):
        return text
    if _FENCE_RE.search(text) and "review" not in lower:
        return text
    return (
        "STATUS: Jag är kodskrivare — jag gör inte kod-review. "
        "Tagga den som äger review eller posta kod så kan de granska.\n\n"
        + text
    )


def sanitize_hub_post(
    text: str,
    *,
    writer_mode: bool,
    had_deliverables: bool,
    recent_fingerprints: list[str],
) -> tuple[str, bool]:
    """Returnerar (text, should_skip_post)."""
    text = (text or "").strip()
    if not text or text.upper() == "PASS":
        return "", True

    text = normalize_coordination_prefixes(text)
    text = block_writer_doing_review(text, writer_mode=writer_mode)

    if is_duplicate_of_recent(text, recent_fingerprints):
        return (
            "STATUS: Samma leverans postades nyss — ingen ändring. "
            "Be om ny diff eller annan fil om ni behöver mer.",
            False,
        )

    if not klar_med_has_substance(text, had_deliverables=had_deliverables):
        km = _KLAR_MED_RE.search(text)
        task = km.group(1).strip() if km else "uppgiften"
        return (
            f"STATUS: Jag tar mig an: {task} — behöver fortfarande implementera. "
            "NEEDS_APPROVAL: bekräfta scope innan jag postar Klar med.",
            False,
        )

    return text, False
