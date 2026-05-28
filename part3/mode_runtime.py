"""Runtime project/github-mode — aktiveras utan omstart.

Auto-detection: skannar nya hub-meddelanden mot konfigurerade fraser.
Vid träff frågas användaren med y/n i terminalen (via BudgetController
som pausar konsol-tråden under prompten).

Manuellt: `enable project` / `enable github` / `disable project` i konsolen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from part3.budget import BudgetController

ModeKind = Literal["project", "github"]

DEFAULT_PROJECT_DETECT_PHRASES: list[str] = [
    "kodprojekt",
    "bygg ett projekt",
    "bygg projekt",
    "skapa ett projekt",
    "skapa projekt",
    "starta ett projekt",
    "gemensamt projekt",
    "gemensam kodbas",
    "arbeta tillsammans",
    "alla agenter kan",
    "alla agenter ska",
    "skapa en app",
    "skapa ett spel",
    "skriv kod",
    "implementera",
    "file_create",
    "agent_workspace",
    "PROJECT:",
    "TASK:",
    "RESULT:",
]

DEFAULT_GITHUB_DETECT_PHRASES: list[str] = [
    "github",
    "github.com",
    "git clone",
    "git push",
    "git commit",
    "git pull",
    "pull request",
    "öppna pr",
    "open pr",
    "gh pr",
    "klona repo",
    "klona repot",
    "pusha till",
    "committa",
    "feature branch",
    "gemensam kodbas på github",
    "kodbas på github",
    "REPO:",
    "BRANCH:",
]

# Regex-heuristiker utöver substring-listan (fångar naturligt språk).
_PROJECT_INTENT_RE = re.compile(
    r"(?:skapa|bygg|starta|sätt\s+upp).{0,40}(?:projekt|projektet|kodbas|codebase|repo)",
    re.IGNORECASE,
)
_PROJECT_COLLAB_RE = re.compile(
    r"projekt.{0,60}(?:tillsammans|alla\s+agenter|gemensam)",
    re.IGNORECASE,
)
_GITHUB_INTENT_RE = re.compile(
    r"\bgithub\b|gemensam\s+kodbas|kodbas\s+på\s+github",
    re.IGNORECASE,
)


def _text_matches_phrases(text: str, phrases: list[str]) -> bool:
    lower = text.lower()
    return any(p.lower() in lower for p in phrases if p)


def _heuristic_project_intent(text: str) -> bool:
    return bool(_PROJECT_INTENT_RE.search(text) or _PROJECT_COLLAB_RE.search(text))


def _heuristic_github_intent(text: str) -> bool:
    return bool(_GITHUB_INTENT_RE.search(text))


@dataclass
class ModeRuntime:
    """Mutable project/github-state under en körning."""

    assignment_root: Path
    template_path: Path
    agent_name: str
    agent_role: str
    project_workspace_rel: str
    github_owner: str
    github_default_branch: str

    project_mode: bool = False
    github_mode: bool = False
    workspace_root: Path | None = None
    system_prompt: str = ""

    auto_detect_project: bool = True
    auto_detect_github: bool = True
    project_phrases: list[str] = field(default_factory=lambda: list(DEFAULT_PROJECT_DETECT_PHRASES))
    github_phrases: list[str] = field(default_factory=lambda: list(DEFAULT_GITHUB_DETECT_PHRASES))

    project_declined: bool = False
    github_declined: bool = False

    def rebuild_system_prompt(self) -> str:
        from part3.chat_agent import render_system_prompt  # noqa: PLC0415 — undvik cirkulär import vid modulnivå

        return render_system_prompt(
            self.template_path,
            agent_name=self.agent_name,
            agent_role=self.agent_role,
            project_mode=self.project_mode,
            project_workspace=str(self.workspace_root) if self.workspace_root is not None else "",
            github_mode=self.github_mode,
            github_owner=self.github_owner,
            github_default_branch=self.github_default_branch,
        )

    def _ensure_workspace(self) -> Path:
        root = (self.assignment_root / self.project_workspace_rel).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.workspace_root = root
        return root

    def enable_project(self) -> str:
        """Aktivera project-mode. Returnerar statusrad för logg."""
        if self.project_mode:
            return f"[mode] project-mode redan aktiv — {self.workspace_root}"
        root = self._ensure_workspace()
        self.project_mode = True
        self.system_prompt = self.rebuild_system_prompt()
        return (
            f"[mode] project-mode AKTIVERAT — workspace: {root}\n"
            "  file-tools + bash begränsade till workspace."
        )

    def enable_github(self) -> str:
        """Aktivera github-mode (project-mode aktiveras automatiskt)."""
        lines: list[str] = []
        if not self.project_mode:
            lines.append(self.enable_project())
        if self.github_mode:
            lines.append("[mode] github-mode redan aktiv.")
            return "\n".join(lines)
        self.github_mode = True
        self.system_prompt = self.rebuild_system_prompt()
        lines.append(
            "[mode] github-mode AKTIVERAT — git/gh-allowlist på, y/n tvingad för git-kommandon."
        )
        return "\n".join(lines)

    def disable_project(self) -> str:
        """Stäng av båda lägen (github kan inte vara på utan project)."""
        if not self.project_mode and not self.github_mode:
            return "[mode] inga lägen aktiva."
        self.project_mode = False
        self.github_mode = False
        self.workspace_root = None
        self.system_prompt = self.rebuild_system_prompt()
        return "[mode] project+github-mode AV — tillbaka till default."

    def mode_status_line(self) -> str:
        if self.github_mode:
            return "github+project"
        if self.project_mode:
            return "project"
        return "default"

    def _wants_github_offer(self, text: str, *, force: bool = False) -> bool:
        if not self.auto_detect_github or self.github_mode:
            return False
        if self.github_declined and not force:
            return False
        return _text_matches_phrases(text, self.github_phrases) or _heuristic_github_intent(text)

    def _wants_project_offer(self, text: str, *, force: bool = False) -> bool:
        if not self.auto_detect_project or self.project_mode:
            return False
        if self.project_declined and not force:
            return False
        return _text_matches_phrases(text, self.project_phrases) or _heuristic_project_intent(text)

    def detect_trigger(self, text: str, *, force: bool = False) -> ModeKind | None:
        """Vilket läge (om något) bör erbjudas utifrån text? Github prioriteras."""
        if self._wants_github_offer(text, force=force):
            return "github"
        if self._wants_project_offer(text, force=force):
            return "project"
        return None

    def offer_modes_for_direct_request(
        self,
        content: str,
        *,
        agent_name: str,
        budget: "BudgetController",
        was_name_addressed: bool,
    ) -> bool:
        """Vid direkt tilltal + skapa-projekt: fråga y/n även om användaren tidigare sa nej."""
        if not was_name_addressed:
            return False
        if not (_heuristic_project_intent(content) or _heuristic_github_intent(content)):
            if not _text_matches_phrases(content, self.project_phrases + self.github_phrases):
                return False
        return self.maybe_auto_enable_from_messages(
            [{"agent_name": "direct-request", "content": content}],
            agent_name=agent_name,
            budget=budget,
            force=True,
        )

    def maybe_auto_enable_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        agent_name: str,
        budget: "BudgetController",
        force: bool = False,
    ) -> bool:
        """Skanna nya meddelanden; fråga y/n vid träff. Returnerar True om läge ändrades.

        `force=True` ignorerar project_declined/github_declined — använd vid
        direkt tilltal där användaren uttryckligen bad om ett projekt.
        """
        from_others = [m for m in messages if m.get("agent_name") != agent_name]
        if not from_others and not force:
            return False

        combined = "\n".join((m.get("content") or "") for m in from_others)
        if force and messages:
            combined = "\n".join((m.get("content") or "") for m in messages)
        trigger = self.detect_trigger(combined, force=force)
        if trigger is None:
            return False

        source_msgs = from_others if from_others else messages
        latest_author = source_msgs[-1].get("agent_name", "?") if source_msgs else "?"
        changed = False

        if trigger == "github":
            if not self.project_mode:
                prompt = (
                    f"\n[MODE] Github-uppdrag detekterat från [{latest_author}].\n"
                    "Aktivera project-mode + github-mode?\n"
                    "  • workspace-spärr (agent_workspace/)\n"
                    "  • git clone/commit/push/gh (y/n per kommando)\n"
                    "[y/N]: "
                )
                if budget.confirm_interactive(prompt):
                    print(self.enable_github())
                    changed = True
                else:
                    self.project_declined = True
                    self.github_declined = True
                    print("[mode] avböjt — frågar inte igen denna körning.")
            else:
                prompt = (
                    f"\n[MODE] Github-uppdrag detekterat från [{latest_author}].\n"
                    "Aktivera github-mode? (git/gh-allowlist)\n"
                    "[y/N]: "
                )
                if budget.confirm_interactive(prompt):
                    print(self.enable_github())
                    changed = True
                else:
                    self.github_declined = True
                    print("[mode] github-mode avböjt — frågar inte igen denna körning.")
        elif trigger == "project":
            prompt = (
                f"\n[MODE] Kodprojekt detekterat från [{latest_author}].\n"
                "Aktivera project-mode?\n"
                "  • file-tools i agent_workspace/\n"
                "  • bash med cwd=workspace\n"
                "[y/N]: "
            )
            if budget.confirm_interactive(prompt):
                print(self.enable_project())
                changed = True
            else:
                self.project_declined = True
                print("[mode] project-mode avböjt — frågar inte igen denna körning.")

        return changed

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        assignment_root: Path,
        template_path: Path,
        agent_name: str,
        agent_role: str,
    ) -> ModeRuntime:
        """Skapa runtime-state från config (inkl. förifyllda lägen vid start)."""
        runtime = cls(
            assignment_root=assignment_root,
            template_path=template_path,
            agent_name=agent_name,
            agent_role=agent_role,
            project_workspace_rel=config.get("project_workspace", "agent_workspace"),
            github_owner=config.get("github_owner", "") or "",
            github_default_branch=config.get("github_default_branch", "main") or "main",
            auto_detect_project=bool(config.get("auto_detect_project_mode", True)),
            auto_detect_github=bool(config.get("auto_detect_github_mode", True)),
            project_phrases=list(
                config.get("project_detect_phrases", DEFAULT_PROJECT_DETECT_PHRASES)
            ),
            github_phrases=list(
                config.get("github_detect_phrases", DEFAULT_GITHUB_DETECT_PHRASES)
            ),
        )

        if bool(config.get("github_mode", False)) and not bool(config.get("project_mode", False)):
            raise ValueError(
                "github_mode kräver project_mode=true i config — git-operationer "
                "måste alltid ske inom en workspace-spärr."
            )

        if bool(config.get("project_mode", False)):
            print(runtime.enable_project())
        if bool(config.get("github_mode", False)):
            print(runtime.enable_github())

        if not runtime.system_prompt:
            runtime.system_prompt = runtime.rebuild_system_prompt()

        if runtime.auto_detect_project or runtime.auto_detect_github:
            print(
                "[mode] auto-detection på — vid kodprojekt/github i chatten "
                "får du y/n-prompt i terminalen (ingen omstart)."
            )

        return runtime
