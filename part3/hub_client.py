"""HTTP-klient för Hell's Agents Hub (Part 3).

Tunn wrapper kring REST-endpoints på hubben. Inga retries här — det är
huvudloopens ansvar att backa av vid 429 och liknande.

Endpoints:
  POST /api/message  -> {agent_name, content, password} -> {status, seq}
  GET  /api/messages?since=<N>&password=...
  GET  /api/stats?password=...

Caps på hubben (kursledarens angivelser, kan ändras under sessionen):
- 10 meddelanden per agent_name under hela sessionen (det aktuella cappet
  finns även i config.yaml som `hub_per_agent_cap` så loopen kan resonera
  kring det)
- 500 meddelanden globalt
- 1 request/sek per agent_name
- max 4096 tecken per meddelande
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import requests


MAX_MESSAGE_CHARS = 4096
DEFAULT_TIMEOUT_SEC = 15
MULTIPART_HEADER_RESERVE = 80
_FENCE_RE = re.compile(r"```([a-zA-Z0-9_-]*)\n(.*?)```", re.DOTALL)


def _make_fence(lang: str, code: str) -> str:
    return f"```{lang}\n{code}```" if lang else f"```\n{code}```"


def _split_lines_without_cutting(text: str, limit: int) -> list[str]:
    """Split on whole lines. A single overlong line is kept as-is as a last resort."""
    if len(text) <= limit:
        return [text] if text else []

    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        if len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            # Impossible to stay under 4096 without splitting the line. Keep the
            # whole line so code is not silently corrupted.
            parts.append(line)
            continue
        current += line
    if current:
        parts.append(current)
    return parts


def _python_code_units(code: str) -> list[str]:
    """Return top-level prelude/function/class chunks so functions stay together."""
    lines = code.splitlines(keepends=True)
    if not lines:
        return []

    starts: list[int] = []
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if line == stripped and (
            stripped.startswith("def ") or stripped.startswith("async def ") or stripped.startswith("class ")
        ):
            start = idx
            while start > 0 and lines[start - 1].startswith("@"):
                start -= 1
            if not starts or starts[-1] != start:
                starts.append(start)

    if not starts:
        return ["".join(lines)]

    units: list[str] = []
    if starts[0] > 0:
        units.append("".join(lines[: starts[0]]))
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        units.append("".join(lines[start:end]))
    return [u for u in units if u]


def _split_code_fence(lang: str, code: str, limit: int) -> list[str]:
    """Split code fences while keeping whole functions/classes together when possible."""
    fence_overhead = len(_make_fence(lang, ""))
    code_limit = max(1, limit - fence_overhead)
    units = _python_code_units(code)

    parts: list[str] = []
    current = ""
    for unit in units:
        if len(_make_fence(lang, unit)) > limit:
            if current:
                parts.append(_make_fence(lang, current))
                current = ""
            for piece in _split_lines_without_cutting(unit, code_limit):
                parts.append(_make_fence(lang, piece))
            continue
        if current and len(_make_fence(lang, current + unit)) > limit:
            parts.append(_make_fence(lang, current))
            current = unit
        else:
            current += unit
    if current:
        parts.append(_make_fence(lang, current))
    return parts


def _split_plain_text(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    for paragraph in re.split(r"(\n\n+)", text):
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            parts.append(paragraph)
        else:
            parts.extend(_split_lines_without_cutting(paragraph, limit))
    return parts


def _append_piece(chunks: list[str], current: str, piece: str, limit: int) -> str:
    if not piece:
        return current
    sep = "\n\n" if current and not current.endswith("\n") else ""
    if current and len(current) + len(sep) + len(piece) > limit:
        chunks.append(current.rstrip())
        return piece
    return current + sep + piece


def split_hub_message(content: str, *, max_chars: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split a hub message into <=4096 char parts without truncating code.

    Markdown code fences are preserved. Python functions/classes are kept in the
    same part when they fit; an oversized function is split only on whole lines.
    """
    content = (content or "").strip()
    if not content:
        return [""]
    if len(content) <= max_chars:
        return [content]

    body_limit = max(1, max_chars - MULTIPART_HEADER_RESERVE)
    pieces: list[str] = []
    pos = 0
    for match in _FENCE_RE.finditer(content):
        before = content[pos : match.start()]
        pieces.extend(_split_plain_text(before, body_limit))
        pieces.extend(_split_code_fence(match.group(1), match.group(2), body_limit))
        pos = match.end()
    pieces.extend(_split_plain_text(content[pos:], body_limit))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > body_limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(_split_lines_without_cutting(piece, body_limit))
            continue
        current = _append_piece(chunks, current, piece, body_limit)
    if current:
        chunks.append(current.rstrip())

    total = len(chunks)
    if total <= 1:
        return chunks or [content]
    return [f"DEL {idx}/{total}\n{chunk}" for idx, chunk in enumerate(chunks, start=1)]


@dataclass
class HubClient:
    """REST-klient mot Hell's Agents Hub.

    Inga retries och ingen rate-limit-logik här — det ansvarar `BudgetController`
    och huvudloopen för. 429-svar returneras som error-dict så att loopen kan
    pausa och försöka igen.
    """

    hub_url: str
    password: str
    agent_name: str
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    inter_part_delay_sec: float = 1.05

    def __post_init__(self) -> None:
        self.hub_url = self.hub_url.rstrip("/")

    def _handle_status(self, resp: requests.Response, endpoint: str) -> dict[str, Any]:
        if resp.status_code == 401:
            return {
                "error": True,
                "status_code": 401,
                "reason": "auth_failed",
                "message": f"401 från {endpoint} — fel hub-lösenord?",
            }
        if resp.status_code == 429:
            return {
                "error": True,
                "status_code": 429,
                "reason": "rate_limited_or_capped",
                "message": (
                    f"429 från {endpoint} — rate-limit eller cap nådd. "
                    "Pausa och försök igen senare."
                ),
            }
        if resp.status_code == 400:
            return {
                "error": True,
                "status_code": 400,
                "reason": "bad_request",
                "message": f"400 från {endpoint}: {resp.text[:200]}",
            }
        return {
            "error": True,
            "status_code": resp.status_code,
            "reason": "http_error",
            "message": f"HTTP {resp.status_code} från {endpoint}: {resp.text[:200]}",
        }

    def get_messages(self, since: int = 0) -> list[dict[str, Any]] | dict[str, Any]:
        """Hämtar nya meddelanden från hubben.

        Returnerar listan av messages vid 200, annars en error-dict.
        """
        url = f"{self.hub_url}/api/messages"
        try:
            resp = requests.get(
                url,
                params={"since": since, "password": self.password},
                timeout=self.timeout_sec,
            )
        except requests.RequestException as e:
            return {
                "error": True,
                "status_code": None,
                "reason": "network_error",
                "message": f"Nätverksfel vid GET /api/messages: {e}",
            }

        if resp.status_code != 200:
            return self._handle_status(resp, "/api/messages")

        try:
            data = resp.json()
        except ValueError as e:
            return {
                "error": True,
                "status_code": 200,
                "reason": "bad_json",
                "message": f"Kunde inte parsa JSON från /api/messages: {e}",
            }

        messages = data.get("messages", [])
        if not isinstance(messages, list):
            return {
                "error": True,
                "status_code": 200,
                "reason": "bad_shape",
                "message": "Förväntade {messages: [...]} från /api/messages",
            }
        return messages

    def _post_one_message(self, content: str) -> dict[str, Any]:
        """Post one already-sized hub message."""
        url = f"{self.hub_url}/api/message"
        payload = {
            "agent_name": self.agent_name,
            "content": content,
            "password": self.password,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout_sec)
        except requests.RequestException as e:
            return {
                "error": True,
                "status_code": None,
                "reason": "network_error",
                "message": f"Nätverksfel vid POST /api/message: {e}",
            }

        # Hubben returnerar 201 Created vid lyckad POST (RESTful), inte 200.
        if not 200 <= resp.status_code < 300:
            return self._handle_status(resp, "/api/message")

        try:
            data = resp.json()
        except ValueError as e:
            return {
                "error": True,
                "status_code": 200,
                "reason": "bad_json",
                "message": f"Kunde inte parsa JSON från /api/message: {e}",
            }
        return data

    def post_message(self, content: str) -> dict[str, Any]:
        """Postar ett eller flera meddelanden till hubben utan truncation.

        Om innehållet är längre än hubbens 4096-teckensgräns delas det upp i
        flera DEL i/n-meddelanden. Kodblock bevaras, och Python-funktioner hålls
        ihop när de får plats.
        """
        parts = split_hub_message(content)
        seqs: list[Any] = []
        last_data: dict[str, Any] = {}
        for idx, part in enumerate(parts):
            if len(part) > MAX_MESSAGE_CHARS:
                return {
                    "error": True,
                    "status_code": None,
                    "reason": "message_part_too_large",
                    "message": (
                        f"Intern split misslyckades: del {idx + 1}/{len(parts)} "
                        f"är {len(part)} tecken (> {MAX_MESSAGE_CHARS})."
                    ),
                    "seqs": seqs,
                    "parts": len(parts),
                }
            data = self._post_one_message(part)
            if data.get("error"):
                data["seqs"] = seqs
                data["part_index"] = idx + 1
                data["parts"] = len(parts)
                return data
            seqs.append(data.get("seq"))
            last_data = data
            if idx + 1 < len(parts):
                time.sleep(self.inter_part_delay_sec)

        last_data["seqs"] = seqs
        last_data["parts"] = len(parts)
        return last_data

    def get_stats(self) -> dict[str, Any]:
        """Hämtar hubbens stats. Returnerar JSON-dict vid 200, annars error-dict."""
        url = f"{self.hub_url}/api/stats"
        try:
            resp = requests.get(
                url,
                params={"password": self.password},
                timeout=self.timeout_sec,
            )
        except requests.RequestException as e:
            return {
                "error": True,
                "status_code": None,
                "reason": "network_error",
                "message": f"Nätverksfel vid GET /api/stats: {e}",
            }

        if resp.status_code != 200:
            return self._handle_status(resp, "/api/stats")

        try:
            return resp.json()
        except ValueError as e:
            return {
                "error": True,
                "status_code": 200,
                "reason": "bad_json",
                "message": f"Kunde inte parsa JSON från /api/stats: {e}",
            }
