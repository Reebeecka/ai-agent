"""Part 2 — lokal SWE-agent med structured tool-calling och persistent session.

Outer-loop: en tur = ett user-message + ev. flera tool-rundor + ett assistant-svar.
Inner-loop: `common.structured_loop.run_structured_loop` kör tool-rundorna.
Persistens: varje ny message skrivs till `part2/sessions/<id>.jsonl` via
on_message-hooken — både assistant-messages och tool-results loggas i realtid.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from common.llm_client import LLMClient
from common.structured_loop import LoopConfig, run_structured_loop
from common.tools_schema import ALL_TOOLS


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "part2" / "config.yaml"
TOOL_NAMES = [t["function"]["name"] for t in ALL_TOOLS]


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_system_prompt(template_path: Path, *, max_tool_output_chars: int) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_path.name)
    return template.render(
        max_tool_output_chars=max_tool_output_chars,
        tools_available=TOOL_NAMES,
    )


def session_file(session_dir: Path, session_id: str) -> Path:
    return session_dir / f"{session_id}.jsonl"


def load_session(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if not path.exists():
        return messages
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            messages.append(json.loads(line))
    return messages


def append_message(path: Path, message: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")


def ensure_system_prompt(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str,
    session_path: Path,
) -> list[dict[str, Any]]:
    if messages and messages[0].get("role") == "system":
        return messages
    system_msg = {"role": "system", "content": system_prompt}
    append_message(session_path, system_msg)
    return [system_msg, *messages]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Part 2 SWE-agent")
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Session-id att fortsätta eller skapa. Default: genereras automatiskt.",
    )
    parser.add_argument(
        "--auto-yes",
        action="store_true",
        help="Hoppa över y/n-prompten för bash (demo-läge).",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Kör en enskild user-tur med detta innehåll och avsluta.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Sökväg till config.yaml.",
    )
    return parser.parse_args()


def run_turn(
    client: LLMClient,
    messages: list[dict[str, Any]],
    *,
    user_text: str,
    loop_config: LoopConfig,
    session_path: Path,
) -> list[dict[str, Any]]:
    user_msg = {"role": "user", "content": user_text}
    messages.append(user_msg)
    append_message(session_path, user_msg)

    def on_message(msg: dict[str, Any]) -> None:
        append_message(session_path, msg)

    result = run_structured_loop(
        client,
        messages,
        ALL_TOOLS,
        config=loop_config,
        on_message=on_message,
    )

    messages.extend(result.messages_appended)

    final_text = result.final_text.strip() or "(inget svar)"
    print(f"\nAssistant: {final_text}")
    print(
        f"[rounds={result.rounds_used}, tool_calls={result.tool_calls_made}, "
        f"total_tokens={client.usage.total_tokens}]"
    )
    return messages


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))

    session_dir = REPO_ROOT / config["session_dir"]
    session_dir.mkdir(parents=True, exist_ok=True)

    session_id = args.session or f"session_{int(time.time())}"
    session_path = session_file(session_dir, session_id)

    system_prompt = render_system_prompt(
        REPO_ROOT / config["system_prompt_template"],
        max_tool_output_chars=int(config["max_tool_output_chars"]),
    )

    messages = load_session(session_path)
    messages = ensure_system_prompt(
        messages,
        system_prompt=system_prompt,
        session_path=session_path,
    )

    client = LLMClient(model=config["model"])
    loop_config = LoopConfig(
        max_tool_rounds=int(config["max_tool_rounds"]),
        auto_yes=bool(args.auto_yes or config.get("auto_yes", False)),
        temperature=float(config["temperature"]),
    )

    print(f"Session: {session_id}  ({session_path})")
    print(f"Model:   {config['model']}")
    print(f"Tools:   {', '.join(TOOL_NAMES)}")
    if loop_config.auto_yes:
        print("Auto-yes aktiverat — bash-kommandon kräver inget y/n.")

    if args.task is not None:
        run_turn(
            client,
            messages,
            user_text=args.task,
            loop_config=loop_config,
            session_path=session_path,
        )
        return 0

    print("Skriv 'quit' eller 'exit' för att avsluta.\n")
    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_text:
            continue
        if user_text.lower() in {"quit", "exit"}:
            return 0
        run_turn(
            client,
            messages,
            user_text=user_text,
            loop_config=loop_config,
            session_path=session_path,
        )


if __name__ == "__main__":
    sys.exit(main())
