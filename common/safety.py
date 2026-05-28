"""Säkerhetsspärr för bash-kommandon.

Tre lager:
 1. Deny-list (regex) — kommandon som matchar något här refuseras direkt
 2. Allow-list (regex) — endast kommandon som matchar något här tillåts
 3. Y/n-bekräftelse i lokal konsol — sista mänskliga kontrollen

Används i Part 1, 2 och 3. I Part 3 körs y/n-prompten fortfarande lokalt,
även när triggern kom från grupp-chatten.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

# Part 3 sätter denna så y/n går via samma stdin-läsare som konsol-kommandon.
_confirm_provider: Callable[[str], bool] | None = None


def set_confirm_provider(provider: Callable[[str], bool] | None) -> None:
    """Dirigera y/n-prompts till en trådsäker stdin-mux (Part 3 budget)."""
    global _confirm_provider
    _confirm_provider = provider


DENY_PATTERNS: list[str] = [
    r"\brm\s+-rf?\s+/",
    r"\brm\s+-rf?\s+~",
    r"\brm\s+-rf?\s+\*",
    r"\bsudo\b",
    r"\bsu\s+-",
    r"\bdd\s+if=",
    r"\bmkfs\.",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bcurl\b[^|]*\|\s*(sh|bash|zsh)\b",
    r"\bwget\b[^|]*\|\s*(sh|bash|zsh)\b",
    r"\bchmod\s+777\b",
    r">\s*/dev/sda",
    r"\b/etc/passwd\b",
    r"\b/etc/shadow\b",
    r"~/\.ssh\b",
    r"~/\.aws\b",
    r"~/\.gnupg\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bkillall\b",
    r"\bnpm\s+publish\b",
    r"\bpip\s+install\b.*--break-system-packages",
]

# Extra deny-mönster som bara gäller i github-mode. Skyddar push-tokens från
# att läcka ut via bash (Python kan fortfarande nå os.environ — system-
# prompten har en explicit no-leak-regel som komplement).
DENY_PATTERNS_GITHUB_EXTRA: list[str] = [
    r"\bGITHUB_TOKEN\b",
    r"\bGH_TOKEN\b",
    r"\bGITHUB_PAT\b",
    r"\bprintenv\b",
    r"\benv\s*(\||$)",
]

ALLOW_PATTERNS: list[str] = [
    r"^ls(\s|$)",
    r"^pwd(\s|$)",
    r"^cd\s+[^;&|]+$",
    r"^cat\s+[^;&|]+$",
    r"^head(\s|$)",
    r"^tail(\s|$)",
    r"^wc(\s|$)",
    r"^echo\s+",
    r"^printf\s+",
    r"^mkdir(\s|$)",
    r"^touch(\s|$)",
    r"^grep\b",
    r"^rg\b",
    r"^find\b",
    r"^python3?\s+",
    r"^pip\s+(list|show|freeze)\b",
    r"^pip\s+install\s+(?!.*--break-system-packages)",
    r"^which\b",
    r"^whoami(\s|$)",
    r"^date(\s|$)",
    r"^uname(\s|$)",
    r"^git\s+(status|log|diff|branch|show|remote)\b",
    r"^ls\b",
    r"^cp\s+[^;&|]+$",
    r"^mv\s+[^;&|]+$",
    r"^rm\s+(?!-rf?\s+/)(?!-rf?\s+~)(?!-rf?\s+\*)[^;&|]+$",
    r"^sed\s+",
    r"^awk\s+",
    r"^sort\b",
    r"^uniq\b",
    r"^cut\b",
    r"^tr\b",
    r"^test\b",
    r"^\[\s+",
]

# Utökade allow-mönster när github_mode är på. Aktiveras av caller-kod via
# `is_command_safe(cmd, github_mode=True)`. Default OFF.
#
# Filosofi: tillåt git/gh-kommandon men håll dem på remote-URLs (inte lokala
# filsystems-stigar) och tillåt curl bara mot github.com / api.github.com.
ALLOW_PATTERNS_GITHUB: list[str] = [
    r"^git\s+(config\s+--get\s+\S+|config\s+user\.(name|email)\s+)",
    r"^git\s+clone\s+(https://|git@|ssh://)\S+",
    r"^git\s+(add|rm|mv|restore)\b",
    r"^git\s+commit\b",
    r"^git\s+(tag|stash|cherry-pick|revert)\b",
    r"^git\s+(reset(\s+--soft\b|\s+HEAD\b)?)(?!\s+--hard\s+)",
    r"^git\s+(checkout|switch)\b",
    r"^git\s+(merge|rebase)\b(?!.*--exec)",
    r"^git\s+(fetch|pull|push)\b",
    r"^git\s+init\b",
    r"^gh\s+(repo|pr|issue|api|auth\s+status|run)\b",
    r"^curl\s+(-[a-zA-Z]+\s+)*https://(api\.|raw\.)?github\.com/",
]


@dataclass
class SafetyVerdict:
    allowed: bool
    reason: str


def is_command_safe(command: str, *, github_mode: bool = False) -> SafetyVerdict:
    """Lager 1+2: regex-check. y/n-bekräftelse hanteras separat.

    När `github_mode=True` tillämpas både utökad deny (token-skydd) och
    utökad allow (git/gh/curl-mot-github). Default OFF.
    """
    cmd = command.strip()
    if not cmd:
        return SafetyVerdict(False, "tomt kommando")

    all_deny = DENY_PATTERNS + (DENY_PATTERNS_GITHUB_EXTRA if github_mode else [])
    for pattern in all_deny:
        if re.search(pattern, cmd):
            return SafetyVerdict(False, f"matchar deny-list-mönster: {pattern}")

    all_allow = ALLOW_PATTERNS + (ALLOW_PATTERNS_GITHUB if github_mode else [])
    for pattern in all_allow:
        if re.match(pattern, cmd):
            return SafetyVerdict(True, f"matchar allow-list-mönster: {pattern}")

    return SafetyVerdict(False, "matchar inget i allow-list (default deny)")


def confirm_y_n(command: str, *, auto_yes: bool = False) -> bool:
    """Lager 3: interaktiv y/n-prompt i lokal konsol."""
    if auto_yes:
        return True
    prompt = f"\n[SAFETY] Köra detta kommando? \n  $ {command}\n[y/N]: "
    if _confirm_provider is not None:
        return _confirm_provider(prompt)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    answer = sys.stdin.readline().strip().lower()
    return answer in ("y", "yes", "j", "ja")


def check_and_confirm(
    command: str,
    *,
    auto_yes: bool = False,
    github_mode: bool = False,
) -> SafetyVerdict:
    """Kombinerar regex-check + y/n. Returnerar slutligt verdict.

    I github-mode tvingas y/n även om `auto_yes=True` — vi vill alltid
    ha mänsklig bekräftelse vid write-side git-operationer.
    """
    verdict = is_command_safe(command, github_mode=github_mode)
    if not verdict.allowed:
        return verdict
    effective_auto_yes = auto_yes and not github_mode
    if not confirm_y_n(command, auto_yes=effective_auto_yes):
        return SafetyVerdict(False, "användaren nekade i y/n-prompt")
    return SafetyVerdict(True, "regex ok + användaren godkände")
