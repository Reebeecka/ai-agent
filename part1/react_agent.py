"""Part 1 — ReAct-agent med rå text-output och egen regex-parsing.

Loopens kontrakt:
- Modellen svarar i exakt formatet `THOUGHT/ACTION/COMMAND` eller `THOUGHT/FINAL`.
- Vi parsar med regex (ingen OpenAI tool-calling, inga ramverk).
- Bash-kommandon går genom `common.safety.check_and_confirm` och
  `common.bash_tool.run_bash` innan output skickas tillbaka som OBSERVATION.

Kör som:
    python3 -m part1.react_agent --task "lista alla .py-filer"
    python3 part1/react_agent.py --task "..."
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

# Gör så att `python3 part1/react_agent.py` också fungerar genom att
# se till att assignment2-roten ligger på sys.path innan vi importerar common.
_PART1_DIR = Path(__file__).resolve().parent
_ASSIGNMENT_ROOT = _PART1_DIR.parent
if str(_ASSIGNMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_ASSIGNMENT_ROOT))

from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

from common.bash_tool import run_bash  # noqa: E402
from common.llm_client import LLMClient  # noqa: E402
from common.safety import check_and_confirm  # noqa: E402


PROMPT_DIR = _PART1_DIR / "prompts"

# Multiline för att `^COMMAND:` ska matcha i början av en rad mitt i texten.
# COMMAND fångar tecken fram till radslut (vi vill bara ha ett enda kommando per round).
COMMAND_PATTERN = re.compile(r"^COMMAND:\s*(.+?)\s*$", re.MULTILINE)

# FINAL fångar allt efter `FINAL:` till slutet av strängen — användarens svar
# kan vara flera rader.
FINAL_PATTERN = re.compile(r"FINAL:\s*(.+)\Z", re.DOTALL)

THOUGHT_PATTERN = re.compile(r"^THOUGHT:\s*(.+?)\s*$", re.MULTILINE)
ACTION_PATTERN = re.compile(r"^ACTION:\s*(.+?)\s*$", re.MULTILINE)


def load_system_prompt() -> str:
    """Laddar och renderar system.j2 via Jinja2."""
    env = Environment(
        loader=FileSystemLoader(str(PROMPT_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = env.get_template("system.j2")
    return template.render()


def parse_model_output(text: str) -> dict[str, str | None]:
    """Plockar ut THOUGHT/ACTION/COMMAND/FINAL ur modellens råa text.

    FINAL prioriteras över COMMAND — om båda förekommer behandlar vi det som
    ett slut-svar (modellen skulle inte producera båda men vi vill ha
    deterministiskt beteende).
    """
    thought_match = THOUGHT_PATTERN.search(text)
    action_match = ACTION_PATTERN.search(text)
    command_match = COMMAND_PATTERN.search(text)
    final_match = FINAL_PATTERN.search(text)

    return {
        "thought": thought_match.group(1).strip() if thought_match else None,
        "action": action_match.group(1).strip() if action_match else None,
        "command": command_match.group(1).strip() if command_match else None,
        "final": final_match.group(1).strip() if final_match else None,
    }


def print_round_header(round_idx: int) -> None:
    print()
    print(f"===== ROUND {round_idx} =====")


def print_section(label: str, body: str) -> None:
    print(f"--- {label} ---")
    print(body.rstrip())


def run_react_loop(task: str, *, max_iters: int, auto_yes: bool, model: str) -> None:
    """Kör själva ReAct-loopen tills FINAL eller max_iters."""
    client = LLMClient(model=model)
    system_prompt = load_system_prompt()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Uppgift: {task}"},
    ]

    print(f"\nTask: {task}")
    print(f"Model: {model} | max_iters: {max_iters} | auto_yes: {auto_yes}")

    rounds_used = 0
    final_answer: str | None = None

    for i in range(1, max_iters + 1):
        rounds_used = i
        print_round_header(i)

        # Notera: skickar INTE tools=. Vi vill ha rå text-output.
        response = client.chat(messages)
        raw = response.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": raw})

        parsed = parse_model_output(raw)

        if parsed["thought"]:
            print_section("THOUGHT", parsed["thought"])

        if parsed["final"] is not None:
            print_section("FINAL", parsed["final"])
            final_answer = parsed["final"]
            break

        if parsed["command"] is not None:
            action = parsed["action"] or "bash"
            print_section("ACTION", action)
            print_section("COMMAND", parsed["command"])

            verdict = check_and_confirm(parsed["command"], auto_yes=auto_yes)
            if not verdict.allowed:
                observation = f"OBSERVATION:\n[SAFETY] kommando nekat — {verdict.reason}"
                print_section("OBSERVATION", observation)
                messages.append({"role": "user", "content": observation})
                continue

            result = run_bash(parsed["command"])
            observation = "OBSERVATION:\n" + result.as_observation()
            print_section("OBSERVATION", observation)
            messages.append({"role": "user", "content": observation})
            continue

        # Varken FINAL eller COMMAND — påminn modellen om formatet.
        # Vi loggar modellens råa svar så att man ser vad som gick fel.
        print_section("RAW (format-fel)", raw)
        reminder = (
            "Du följde inte formatet. Svara igen med exakt "
            "THOUGHT/ACTION/COMMAND eller THOUGHT/FINAL."
        )
        messages.append({"role": "user", "content": reminder})

    print()
    print("===== SLUT =====")
    if final_answer is None:
        print(f"Loop avslutad utan FINAL efter {rounds_used} rounds (max_iters={max_iters}).")
    else:
        print(f"Loop klar efter {rounds_used} rounds.")
    print(f"Token usage: {client.usage.snapshot()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="part1.react_agent",
        description="ReAct-agent med rå text-output och regex-parsing (Part 1).",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Uppgift till agenten. Om utelämnad: frågar interaktivt via input().",
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=10,
        help="Max antal ReAct-rounds innan loopen tvångsavslutas (default: 10).",
    )
    parser.add_argument(
        "--auto-yes",
        action="store_true",
        help="Hoppar över y/n-prompten i säkerhetsspärren (för demo/skript).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="OpenAI-modell att använda (default: gpt-4o-mini).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task = args.task
    if not task:
        task = input("Task: ").strip()
    if not task:
        # Tom uppgift har ingen meningsfull tolkning — avsluta direkt.
        print("Ingen uppgift angiven. Avslutar.")
        return

    run_react_loop(
        task=task,
        max_iters=args.max_iters,
        auto_yes=args.auto_yes,
        model=args.model,
    )


if __name__ == "__main__":
    main()
