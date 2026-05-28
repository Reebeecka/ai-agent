"""Subprocess-runner med timeout och output-cap.

Används av Part 1, 2 och 3. Cap:en gäller stdout/stderr separat och
markerar trunkering tydligt så att modellen vet att resten är borta.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


DEFAULT_TIMEOUT_SEC = 30
DEFAULT_OUTPUT_CAP_CHARS = 4000


@dataclass
class BashResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool

    def as_observation(self) -> str:
        """Formatera för att stoppas in som OBSERVATION/tool-output till modellen."""
        parts = [f"exit_code: {self.exit_code}"]
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        if self.truncated:
            parts.append("(output trunkerades p.g.a. storleksgräns)")
        return "\n".join(parts)


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    marker = f"\n... [trunkerad, totalt {len(text)} tecken, visar första {cap}] ..."
    return text[:cap] + marker, True


def run_bash(
    command: str,
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    output_cap_chars: int = DEFAULT_OUTPUT_CAP_CHARS,
    cwd: str | None = None,
) -> BashResult:
    """Kör ett bash-kommando med timeout och output-cap.

    Anropas EFTER safety.check_and_confirm har godkänt kommandot.
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd,
        )
        stdout, t1 = _truncate(proc.stdout, output_cap_chars)
        stderr, t2 = _truncate(proc.stderr, output_cap_chars)
        return BashResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            truncated=t1 or t2,
        )
    except subprocess.TimeoutExpired:
        return BashResult(
            command=command,
            stdout="",
            stderr=f"TIMEOUT efter {timeout_sec}s",
            exit_code=124,
            truncated=False,
        )
    except Exception as e:
        return BashResult(
            command=command,
            stdout="",
            stderr=f"subprocess-fel: {type(e).__name__}: {e}",
            exit_code=1,
            truncated=False,
        )
