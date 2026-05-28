"""Budget- och rate-limit-kontroll för Part 3 + realtids-konsol.

`BudgetController` håller:
- token-budget (kollat mot `LLMClient.usage.total_tokens`)
- rate-limit mellan posts (min antal sekunder mellan två posts till hubben)
- pause/resume + stop-flagga som huvudloopen läser

`start_console_thread(client)` startar en daemon-tråd som läser stdin och
accepterar realtids-kommandon (`set rate N`, `set budget N`, `pause`,
`resume`, `stop`, `stats`). Daemon = ctrl+c funkar och processen avslutas
rent när huvudloopen är klar.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from common.llm_client import LLMClient
    from part3.mode_runtime import ModeRuntime


CONSOLE_HELP = """\
Tillgängliga konsol-kommandon:
  set rate <sek>     - minsta sekunder mellan posts till hubben
  set budget <n>     - sätt token-budget (totalt över sessionen)
  set budget off     - slå av token-budgeten (obegränsad)
  enable project     - aktivera project-mode (workspace-spärr)
  enable github      - aktivera github-mode (kräver project)
  disable project    - stäng av project+github-mode
  mode               - visa aktiva lägen
  pause              - pausa loopen (ingen ny LLM eller post)
  resume             - återuppta loopen
  stop               - avsluta agenten rent
  stats              - visa token-användning, posts, gränser
  help               - visa denna hjälp"""


@dataclass
class BudgetController:
    """Token-budget + rate-limit + pause/stop, trådsäker via en enkel lock.

    `token_budget=None` betyder obegränsad budget — `is_token_budget_exhausted`
    returnerar alltid False och `can_call_llm` ignorerar token-räkningen.
    Räknaren `tokens_used` fortsätter dock fungera (för stats).
    """

    token_budget: int | None = None
    rate_limit_sec: float = 4.0
    paused: bool = False
    stopped: bool = False
    posts_made: int = 0
    _last_post_at: float = 0.0
    _client_ref: "LLMClient | None" = None
    _mode_runtime: "ModeRuntime | None" = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _confirm_lock: threading.Lock = field(default_factory=threading.Lock)
    _pending_confirm: tuple[threading.Event, list[str]] | None = field(
        default=None, repr=False
    )
    _thread: threading.Thread | None = None
    _confirm_timeout_sec: float = 600.0

    def attach_mode_runtime(self, runtime: "ModeRuntime") -> None:
        with self._lock:
            self._mode_runtime = runtime

    def attach_client(self, client: "LLMClient") -> None:
        """Spar referens till LLMClient så att vi kan läsa tokens_used senare."""
        with self._lock:
            self._client_ref = client

    def tokens_used(self) -> int:
        with self._lock:
            if self._client_ref is None:
                return 0
            return self._client_ref.usage.total_tokens

    def can_call_llm(self) -> bool:
        """False om paused, stopped eller token-budget överskriden."""
        with self._lock:
            if self.stopped or self.paused:
                return False
            if self.token_budget is None:
                return True
            if self._client_ref is None:
                return True
            return self._client_ref.usage.total_tokens < self.token_budget

    def can_post(self) -> bool:
        """False om paused, stopped eller för kort tid sedan senaste post."""
        with self._lock:
            if self.stopped or self.paused:
                return False
            elapsed = time.monotonic() - self._last_post_at
            return elapsed >= self.rate_limit_sec

    def is_token_budget_exhausted(self) -> bool:
        with self._lock:
            if self.token_budget is None:
                return False
            if self._client_ref is None:
                return False
            return self._client_ref.usage.total_tokens >= self.token_budget

    def seconds_until_can_post(self) -> float:
        with self._lock:
            elapsed = time.monotonic() - self._last_post_at
            remaining = self.rate_limit_sec - elapsed
            return max(0.0, remaining)

    def note_post(self) -> None:
        with self._lock:
            self._last_post_at = time.monotonic()
            self.posts_made += 1

    def snapshot(self) -> dict[str, float | int | bool | None]:
        with self._lock:
            tokens = (
                self._client_ref.usage.total_tokens if self._client_ref is not None else 0
            )
            return {
                "tokens_used": tokens,
                "token_budget": self.token_budget,
                "rate_limit_sec": self.rate_limit_sec,
                "posts_made": self.posts_made,
                "paused": self.paused,
                "stopped": self.stopped,
            }

    def _set_rate(self, value: str) -> str:
        try:
            new_rate = float(value)
        except ValueError:
            return f"ogiltigt värde för rate: {value!r} (förväntade tal)"
        if new_rate < 0:
            return "rate måste vara >= 0"
        with self._lock:
            self.rate_limit_sec = new_rate
        return f"rate_limit_sec = {new_rate}"

    def _set_budget(self, value: str) -> str:
        # `set budget off` / `set budget none` / `set budget unlimited` slår av cappet.
        if value.lower() in ("off", "none", "unlimited", "null"):
            with self._lock:
                self.token_budget = None
            return "token_budget = ∞ (obegränsad)"
        try:
            new_budget = int(value)
        except ValueError:
            return f"ogiltigt värde för budget: {value!r} (förväntade heltal eller 'off')"
        if new_budget < 0:
            return "budget måste vara >= 0"
        with self._lock:
            self.token_budget = new_budget
        return f"token_budget = {new_budget}"

    def _format_stats(self) -> str:
        s = self.snapshot()
        budget_str = "∞" if s["token_budget"] is None else str(s["token_budget"])
        return (
            f"tokens_used={s['tokens_used']}/{budget_str} | "
            f"posts={s['posts_made']} | rate={s['rate_limit_sec']}s | "
            f"paused={s['paused']} | stopped={s['stopped']}"
        )

    def confirm_interactive(self, prompt: str) -> bool:
        """y/n via konsol-tråden — enda läsare av stdin (ingen race med [console])."""
        event = threading.Event()
        holder: list[str] = []
        with self._confirm_lock:
            if self._pending_confirm is not None:
                print(
                    "[console] väntar på föregående y/n — svara på den prompten först.",
                    flush=True,
                )
                return False
            self._pending_confirm = (event, holder)
        try:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            if not event.wait(timeout=self._confirm_timeout_sec):
                print(
                    f"[console] y/n timeout ({self._confirm_timeout_sec:.0f}s) — räknas som nej.",
                    flush=True,
                )
                return False
            answer = holder[0].strip().lower() if holder else ""
            accepted = answer in ("y", "yes", "j", "ja")
            print(f"[console] svar: {'ja' if accepted else 'nej'}", flush=True)
            return accepted
        finally:
            with self._confirm_lock:
                self._pending_confirm = None

    def _handle_mode_command(self, parts: list[str]) -> str:
        with self._lock:
            runtime = self._mode_runtime
        if runtime is None:
            return "mode-runtime ej kopplad"
        if len(parts) < 2:
            return f"läge: {runtime.mode_status_line()}"
        verb = parts[0].lower()
        target = parts[1].lower()
        if verb == "enable":
            if target == "project":
                return runtime.enable_project()
            if target == "github":
                return runtime.enable_github()
            return f"okänt enable-mål: {target!r} (giltiga: project, github)"
        if verb == "disable" and target == "project":
            return runtime.disable_project()
        return f"okänt mode-kommando: {' '.join(parts)!r}"

    def handle_command(self, raw: str) -> str:
        """Tolkar en konsol-rad och returnerar en feedback-sträng."""
        text = raw.strip()
        if not text:
            return ""
        parts = text.split()
        verb = parts[0].lower()

        if verb == "help":
            return CONSOLE_HELP
        if verb == "mode":
            return self._handle_mode_command(["mode"])
        if verb in ("enable", "disable"):
            return self._handle_mode_command(parts)
        if verb == "stats":
            s = self._format_stats()
            with self._lock:
                runtime = self._mode_runtime
            if runtime is not None:
                s += f" | mode={runtime.mode_status_line()}"
            return s
        if verb == "pause":
            with self._lock:
                self.paused = True
            return "pausad — skriv `resume` för att fortsätta"
        if verb == "resume":
            with self._lock:
                self.paused = False
            return "resumed"
        if verb == "stop":
            with self._lock:
                self.stopped = True
            return "stop registrerat — loopen avslutar"
        if verb == "set" and len(parts) >= 3:
            key = parts[1].lower()
            value = parts[2]
            if key == "rate":
                return self._set_rate(value)
            if key == "budget":
                return self._set_budget(value)
            return f"okänd set-nyckel: {key} (giltiga: rate, budget)"

        return f"okänt kommando: {text!r} — skriv `help` för kommandolista."

    def _route_stdin_line(self, line: str) -> None:
        """Dirigera rad till aktiv y/n-prompt eller konsol-kommando."""
        with self._confirm_lock:
            pending = self._pending_confirm
        if pending is not None:
            event, holder = pending
            holder.append(line)
            event.set()
            return
        response = self.handle_command(line)
        if response:
            print(f"[console] {response}", flush=True)

    def _console_loop(self) -> None:
        """Daemon-tråd: enda läsare av stdin (y/n + kommandon)."""
        print(CONSOLE_HELP, flush=True)
        while True:
            with self._lock:
                if self.stopped:
                    return
            try:
                line = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                return
            if not line:
                return
            self._route_stdin_line(line)

    def start_console_thread(self, client: "LLMClient") -> threading.Thread:
        """Startar daemon-tråden. Anropas en gång från main."""
        self.attach_client(client)
        thread = threading.Thread(
            target=self._console_loop,
            name="budget-console",
            daemon=True,
        )
        thread.start()
        self._thread = thread
        return thread
