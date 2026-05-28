"""Part 3 — multi-agent som ansluter till Hell's Agents Hub.

Loopens kontrakt:
- Pollar /api/messages och ser nya meddelanden från andra agenter
- Per ny chat-tur: kör ett deterministiskt pre-filter som klassar varje nytt
  meddelande som FORCE_RESPOND (direkt tilltal, broadcast, kompetens-match)
  eller MAYBE (kan vara relevant — kör LLM-gating för PASS/RESPOND-beslut).
- Vid FORCE: generera svaret direkt utan PASS-gating (sparar token-budget).
- Vid MAYBE: kör PASS-beslutet med 1 LLM-anrop. Vid RESPOND körs sedan
  structured_loop om svaret hintar om verifiering (bash/file_edit/file_read).
- Postar slutsvar till hubben via `HubClient.post_message`.
- Stannar när `budget.stopped` eller token-budget överskriden. `max_messages_
  to_send` är en mjuk gräns — sätt till null för att helt strunta i den.
- Hubbens per-agent-cap hanteras via "read-only-mode": vid 429 pausar vi posts en
  konfigurerbar tid men fortsätter polla så vi har kontext när cappet höjs.

Kör som:
    python3 -m part3.chat_agent
    python3 part3/chat_agent.py
"""

from __future__ import annotations

import random
import re
import os
import sys
import time
from pathlib import Path
from typing import Any

# Gör så att `python3 part3/chat_agent.py` också fungerar.
_PART3_DIR = Path(__file__).resolve().parent
_ASSIGNMENT_ROOT = _PART3_DIR.parent
if str(_ASSIGNMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_ASSIGNMENT_ROOT))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

from common import safety  # noqa: E402
from common.llm_client import LLMClient  # noqa: E402
from common.structured_loop import LoopConfig, LoopResult, run_structured_loop  # noqa: E402
from part3.deliverable_attach import (  # noqa: E402
    append_deliverables_to_hub_message,
    build_attachment_for_hub,
)
from part3.hub_code_persist import (  # noqa: E402
    persist_hub_codeblocks,
    resolve_workspace,
)
from common.tools_schema import ALL_TOOLS  # noqa: E402
from part3.budget import BudgetController  # noqa: E402
from part3.hub_client import HubClient  # noqa: E402
from part3.mode_runtime import ModeRuntime  # noqa: E402
from part3.hub_review import (  # noqa: E402
    ensure_honest_review_prefix,
    prepare_role_evidence,
    requires_code_evidence,
    strip_pass_leak,
)
from part3.collaboration import (  # noqa: E402
    build_collaboration_system_hint,
    enforce_collaboration_on_post,
    is_collaboration_context,
    is_smalltalk_message,
    is_writer_role,
    parse_peer_tasks,
    should_skip_duplicate_attach,
    suggested_writer_scope,
    wants_full_code_in_hub,
)
from part3.post_quality import (  # noqa: E402
    extract_posted_files,
    fingerprint_post,
    sanitize_hub_post,
)
from part3.task_coordination import (  # noqa: E402
    apply_coordination_format,
    assignment_response_hint,
    ensure_claim_prefix,
    extract_task_label,
)


CONTEXT_WINDOW_MSGS = 20
MAX_TOOL_OUTPUT_CHARS = 4000
PASS_PROMPT_USER_SUFFIX = (
    "Ska du svara på det senaste meddelandet i tråden ovan? "
    "Svara exakt `PASS` (utan annat) för att avstå, eller börja med "
    "`RESPOND: ` följt av ditt svar till chatten. Inga andra format."
)
PASS_DECISION_SYSTEM = (
    "Du är gating-filter för en multi-agent grupp-chat med ~30 agenter. "
    "Default = PASS. Svara bara RESPOND när du har konkret mervärde som "
    "ingen annan postat. Var konservativ: brus i ett 30-agent-rum kostar "
    "alla andra tokens. Svara exakt `PASS` eller `RESPOND: <ditt svar>`. "
    "Om RESPOND: håll svaret < 600 tecken om möjligt och konkret."
)
CONSERVATIVE_PASS_SUFFIX = (
    "\n\nVIKTIGT: agenten är nära sin hub-cap. Var EXTRA strikt — passa "
    "om du inte är direkt tilltalad eller har en konkret kodfix att dela."
)
FORCE_RESPOND_SYSTEM = (
    "Du har redan beslutat att svara (du blev tilltalad vid namn, via "
    "broadcast, eller frågan matchar din kärnkompetens). Generera svaret "
    "direkt — INGEN PASS, ingen meta-kommentar. Håll det kort (< 600 "
    "tecken om möjligt), konkret och i linje med din roll."
)
COLLAB_FORCE_SUFFIX_WRITER = (
    "\n\nSamarbetsläge — KODSKRIVARE: respektera andras `Jag tar mig an:` "
    "(UI/test/review). Du implementerar BARA din core-modul. "
    "Börja med `Jag tar mig an: <en fil/modul>`. Ingen kod-review. "
    "Efter file_create: `RESULT:` + kod i ``` + `Klar med:`."
)
COLLAB_FORCE_SUFFIX_DEFAULT = (
    "\n\nSamarbetsläge: process före färdig produkt. Respektera andras CLAIM. "
    "Börja med `Jag tar mig an: <smal del>` om du kodar."
)


def collab_force_suffix(config: dict[str, Any]) -> str:
    if is_writer_role(
        str(config.get("agent_role_mode", "")),
        str(config.get("agent_role", "")),
    ):
        return COLLAB_FORCE_SUFFIX_WRITER
    return COLLAB_FORCE_SUFFIX_DEFAULT


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_env_value(value: Any, *, name: str) -> str:
    """Support YAML values like `${HUB_PASSWORD}` without storing secrets in git."""
    text = str(value or "")
    if text.startswith("${") and text.endswith("}"):
        env_name = text[2:-1]
        resolved = os.environ.get(env_name, "")
        if not resolved:
            raise RuntimeError(f"{name} kräver miljövariabeln {env_name}")
        return resolved
    return text


def render_system_prompt(
    template_path: Path,
    *,
    agent_name: str,
    agent_role: str,
    project_mode: bool = False,
    project_workspace: str = "",
    github_mode: bool = False,
    github_owner: str = "",
    github_default_branch: str = "main",
) -> str:
    template_dir = template_path.parent
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = env.get_template(template_path.name)
    return template.render(
        agent_name=agent_name,
        agent_role=agent_role,
        max_tool_output_chars=MAX_TOOL_OUTPUT_CHARS,
        project_mode=project_mode,
        project_workspace=project_workspace,
        github_mode=github_mode,
        github_owner=github_owner,
        github_default_branch=github_default_branch,
    )


def format_context(messages: list[dict[str, Any]], *, window: int) -> str:
    """Format de sista `window` chat-meddelandena som `[agent]: content`."""
    tail = messages[-window:]
    lines = []
    for msg in tail:
        author = msg.get("agent_name", "?")
        content = (msg.get("content") or "").strip()
        lines.append(f"[{author}]: {content}")
    return "\n".join(lines)


def _compile_alias_patterns(aliases: list[str]) -> list[re.Pattern[str]]:
    """Bygg word-boundary regex för varje alias så vi inte matchar inuti ord.

    `rebecka` ska matcha "rebecka:" men inte "rebeckaski" eller "rebecka-other".
    `@rebecka` ska matcha bokstavligt.
    """
    patterns: list[re.Pattern[str]] = []
    for alias in aliases:
        if not alias:
            continue
        escaped = re.escape(alias)
        if alias.startswith("@"):
            patterns.append(re.compile(escaped, re.IGNORECASE))
        else:
            patterns.append(re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE))
    return patterns


def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def _contains_any_substring(text: str, needles: list[str]) -> bool:
    lower = text.lower()
    return any(n.lower() in lower for n in needles if n)


def classify_message(
    content: str,
    *,
    name_patterns: list[re.Pattern[str]],
    broadcast_phrases: list[str],
    competence_keywords: list[str],
) -> tuple[str, str]:
    """Klassificera ett enskilt meddelande.

    Returnerar (kategori, anledning):
    - "name": tilltalad vid namn → måste svara
    - "broadcast": riktat till alla agenter → svara om relevant
    - "competence": matchar agentens kärnkompetens → svara om ingen annan har
    - "maybe": möjligen relevant — LLM-gating får avgöra
    """
    if _matches_any(content, name_patterns):
        return "name", "tilltalad vid namn"
    if _contains_any_substring(content, broadcast_phrases):
        return "broadcast", "broadcast till alla agenter"
    if _contains_any_substring(content, competence_keywords):
        return "competence", "matchar kärnkompetens"
    return "maybe", ""


def pick_trigger_message(
    new_msgs: list[dict[str, Any]],
    *,
    agent_name: str,
    name_patterns: list[re.Pattern[str]],
    broadcast_phrases: list[str],
    competence_keywords: list[str],
    responded_seqs: set[int],
) -> tuple[str, dict[str, Any] | None, str]:
    """Välj vilket meddelande (om något) som triggar ett svar denna tur.

    Itererar nya meddelanden i ordning. Returnerar första `name`/`broadcast`/
    `competence`-träffen. Om inget force-match: returnera senaste `maybe` om
    det finns (då får LLM-gating avgöra). Hoppar över egna och redan besvarade.

    Returnerar (kategori, msg_or_None, reason).
    """
    maybe_msg: dict[str, Any] | None = None
    for msg in new_msgs:
        if msg.get("agent_name") == agent_name:
            continue
        seq = msg.get("seq")
        if isinstance(seq, int) and seq in responded_seqs:
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        category, reason = classify_message(
            content,
            name_patterns=name_patterns,
            broadcast_phrases=broadcast_phrases,
            competence_keywords=competence_keywords,
        )
        if category in ("name", "broadcast", "competence"):
            return category, msg, reason
        maybe_msg = msg
    if maybe_msg is not None:
        return "maybe", maybe_msg, ""
    return "skip", None, ""


def collaboration_context_for_turn(
    config: dict[str, Any],
    *,
    agent_name: str,
    agent_role: str,
    trigger_content: str,
    chat_context: str,
    chat_history: list[dict[str, Any]],
) -> tuple[str, bool]:
    if not config.get("collaboration_mode", True):
        return "", False
    peer_tasks = parse_peer_tasks(chat_history, self_agent_name=agent_name)
    hint = build_collaboration_system_hint(
        trigger_content=trigger_content,
        chat_context=chat_context,
        agent_name=agent_name,
        agent_role=agent_role,
        agent_role_mode=str(config.get("agent_role_mode", "")),
        peer_tasks=peer_tasks,
    )
    return hint, True


def decide_pass_or_respond(
    client: LLMClient,
    *,
    system_prompt: str,
    chat_context: str,
    conservative: bool,
    collaboration_hint: str = "",
) -> tuple[bool, str]:
    """Kör PASS-beslutet med ETT LLM-anrop, ingen tools.

    Returnerar (should_respond, response_text). Om should_respond=False är
    response_text tomt. När `conservative=True` blir gating ännu striktare.
    """
    gating_system = (
        PASS_DECISION_SYSTEM
        + (CONSERVATIVE_PASS_SUFFIX if conservative else "")
        + collaboration_hint
    )
    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + gating_system},
        {
            "role": "user",
            "content": f"Chat-kontext (senaste först nederst):\n{chat_context}\n\n{PASS_PROMPT_USER_SUFFIX}",
        },
    ]
    response = client.chat(messages, temperature=0.2)
    text = (response.choices[0].message.content or "").strip()

    stripped = text.lstrip()
    if stripped.upper().startswith("PASS"):
        return False, ""
    if stripped.upper().startswith("RESPOND:"):
        body = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
        return True, body
    return True, stripped


def _no_promise_suffix(mode_runtime: ModeRuntime, trigger_content: str) -> str:
    """Om project/github inte är på men användaren bad om projekt — sluta lova."""
    if mode_runtime.project_mode:
        return ""
    from part3.mode_runtime import (  # noqa: PLC0415
        _heuristic_github_intent,
        _heuristic_project_intent,
        _text_matches_phrases,
    )

    wants_work = (
        _heuristic_project_intent(trigger_content)
        or _heuristic_github_intent(trigger_content)
        or _text_matches_phrases(
            trigger_content,
            mode_runtime.project_phrases + mode_runtime.github_phrases,
        )
    )
    if not wants_work:
        return ""
    return (
        "\n\nVIKTIGT: project/github-mode är INTE aktivt på din maskin. "
        "Lova INTE att du 'går vidare', skapar repo eller filer. Säg att "
        "användaren ska svara y på [MODE]-prompten i terminalen, eller "
        "skriv `enable project` / `enable github`, innan du kan leverera kod."
    )


def generate_forced_response(
    client: LLMClient,
    *,
    system_prompt: str,
    chat_context: str,
    reason: str,
    mode_runtime: ModeRuntime | None = None,
    trigger_content: str = "",
    collaboration_hint: str = "",
    collaboration_active: bool = False,
    review_evidence_hint: str = "",
    collab_suffix: str = "",
) -> str:
    """Generera svar direkt utan PASS-gating (vid name/broadcast/competence)."""
    extra = ""
    if trigger_content:
        extra += assignment_response_hint(trigger_content)
    if mode_runtime is not None and trigger_content:
        extra += _no_promise_suffix(mode_runtime, trigger_content)
    extra += collaboration_hint + review_evidence_hint
    force_suffix = FORCE_RESPOND_SYSTEM + (
        collab_suffix if collaboration_active else ""
    )
    user_tail = (
        f"Du måste svara ({reason}). Generera svaret som ren text — "
        "ALDRIG ordet PASS i svaret. Returnera ENDAST det som ska postas till chatten."
    )
    if requires_code_evidence(trigger_content) and review_evidence_hint:
        user_tail += (
            " Om du delar kod: ange källa (agent + seq) i första meningen."
        )
    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + force_suffix + extra},
        {
            "role": "user",
            "content": (
                f"Chat-kontext (senaste först nederst):\n{chat_context}\n\n"
                f"{user_tail}"
            ),
        },
    ]
    response = client.chat(messages, temperature=0.3)
    return (response.choices[0].message.content or "").strip()


def run_tool_loop_for_response(
    client: LLMClient,
    *,
    system_prompt: str,
    chat_context: str,
    draft_response: str,
    cfg_max_rounds: int,
    auto_yes: bool,
    github_mode: bool = False,
    workspace_root: Path | None = None,
    extra_system: str = "",
    collaboration_active: bool = False,
) -> LoopResult:
    """Kör structured_loop med chat-historiken som kontext.

    Ger modellen utkastet som assistant-message så att den kan välja att
    (a) leverera det rakt av eller (b) köra tools för att verifiera och
    justera. Slutsvaret är det som postas till hubben.
    """
    tool_user_tail = (
        "Ditt jobb: posta ETT svar till chatten. Du har lokala tools "
        "(bash/file_edit/file_create/file_read). "
        "VIKTIGT: spara all kod med file_create/file_edit under workspace "
        "(sökvägar som main.py eller src/app.py, inte mappar utanför workspace). "
        "Posta OCKSÅ full kod i chatten i ```-block så andra agenter ser den. "
        "När du är klar — returnera det slutgiltiga svaret som vanlig text."
    )
    if collaboration_active:
        tool_user_tail = (
            "Ditt jobb: posta ETT svar till chatten. Tools för din SMALA del bara. "
            "Samarbetsläge: duplicera inte hela projektet om andra CLAIM:at core. "
            "Spara med file_create i workspace; i hub: kort snippet + filnamn. "
            "Returnera slutgiltigt svar som vanlig text."
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt + extra_system},
        {
            "role": "user",
            "content": (
                "Du är inne i grupp-chatten. Senaste kontexten:\n"
                f"{chat_context}\n\n"
                f"{tool_user_tail}"
            ),
        },
        {
            "role": "assistant",
            "content": f"Utkast: {draft_response}",
        },
        {
            "role": "user",
            "content": (
                "Verifiera/justera utkastet om det behövs (kör tools vid behov), "
                "annars returnera det som slutsvar."
            ),
        },
    ]
    loop_cfg = LoopConfig(
        max_tool_rounds=cfg_max_rounds,
        auto_yes=auto_yes,
        github_mode=github_mode,
        workspace_root=workspace_root,
    )
    result = run_structured_loop(client, messages, ALL_TOOLS, config=loop_cfg)
    if not result.final_text:
        result.final_text = draft_response
    return result


def needs_tools(draft_response: str) -> bool:
    """Heuristik: kör tool-loopen bara om utkastet hintar om verifiering."""
    triggers = (
        "kör",
        "kolla",
        "läs",
        "verifiera",
        "testa",
        "run ",
        "read ",
        "verify",
        "check ",
        "bash",
        "file_read",
        "file_edit",
        "file_create",
        "skapa ",
        "sätt upp",
        "readme",
        "struktur",
        "repository",
        "git clone",
        "file_read",
        "hub_mirror",
    )
    lowered = draft_response.lower()
    return any(t in lowered for t in triggers)


def needs_tools_for_turn(
    draft_response: str,
    *,
    trigger_content: str,
    force_tools: bool = False,
) -> bool:
    if force_tools or requires_code_evidence(trigger_content):
        return True
    return needs_tools(draft_response)


def post_join_greeting(
    hub: HubClient,
    budget: BudgetController,
    *,
    template: str,
    agent_name: str,
    agent_role: str,
    already_introduced: bool,
) -> bool:
    """Posta greeting när agenten joinar rummet. Returnerar True om postad.

    Skippar tyst om:
    - template är tom
    - vi redan har postat tidigare (already_introduced)
    - format-strängen saknar en variabel (loggar och fortsätter)
    """
    if not template:
        return False
    if already_introduced:
        print("[greeting] redan introducerad i tråden — hoppar över.")
        return False

    try:
        greeting = template.format(agent_name=agent_name, agent_role=agent_role)
    except KeyError as e:
        print(f"[greeting] format-fel i join_greeting (saknar variabel: {e}) — hoppar över.")
        return False

    greeting = greeting.strip()
    if not greeting:
        return False

    wait_s = budget.seconds_until_can_post()
    if wait_s > 0:
        time.sleep(wait_s)

    print(f"[greeting] -> hub ({len(greeting)} tecken)")
    result = hub.post_message(greeting)
    if result.get("error"):
        print(f"[greeting] FEL: {result.get('message')}")
        return False

    budget.note_post()
    seq = result.get("seq", "?")
    parts = int(result.get("parts", 1) or 1)
    if parts > 1:
        print(f"[greeting] OK seqs={result.get('seqs', [seq])} ({parts} delar)")
    else:
        print(f"[greeting] OK seq={seq}")
    return True


def print_banner(agent_name: str, hub_url: str, config: dict[str, Any]) -> None:
    max_msgs = config.get("max_messages_to_send")
    max_msgs_str = "obegränsat (hub-cap enforceas serverside)" if max_msgs is None else str(max_msgs)
    token_budget = config.get("default_token_budget")
    token_budget_str = "∞ (obegränsad)" if token_budget is None else str(token_budget)
    print("=" * 64)
    print(f"  Part 3 — hub-agent  ::  {agent_name}")
    print(f"  Hub: {hub_url}")
    print(f"  Modell: {config['model']}")
    print(
        f"  Token-budget: {token_budget_str} | "
        f"rate-limit: {config['default_rate_limit_sec']}s | "
        f"local cap: {max_msgs_str}"
    )
    print(
        f"  Hub-cap: {config.get('hub_per_agent_cap', '?')} | "
        f"conservative @ {config.get('conservative_threshold', '?')}"
    )
    auto_p = config.get("auto_detect_project_mode", True)
    auto_g = config.get("auto_detect_github_mode", True)
    if auto_p or auto_g:
        print(
            f"  Mode auto-detect: project={auto_p}, github={auto_g} "
            "(y/n i terminalen vid träff)"
        )
    print("=" * 64)
    print("Skriv `help` för konsol-kommandon. Skriv `stop` för att avsluta.")
    print()


def agent_workspace_path(
    config: dict[str, Any],
    mode_runtime: ModeRuntime,
) -> Path:
    """Workspace-mapp — även när project_mode är av (för lokal sparning)."""
    if mode_runtime.workspace_root is not None:
        return mode_runtime.workspace_root
    return resolve_workspace(
        _ASSIGNMENT_ROOT,
        str(config.get("project_workspace", "agent_workspace")),
    )


def tool_workspace_root(
    config: dict[str, Any],
    mode_runtime: ModeRuntime,
) -> Path | None:
    """Workspace för file-tools/bash: alltid under agent_workspace om konfigurerat."""
    if mode_runtime.project_mode:
        return mode_runtime.workspace_root
    if config.get("persist_chat_code_to_workspace", True) or config.get(
        "tools_use_agent_workspace", True
    ):
        return agent_workspace_path(config, mode_runtime)
    return None


def _count_other_msgs_since(
    chat_history: list[dict[str, Any]],
    *,
    agent_name: str,
    since_index: int,
) -> int:
    return sum(
        1
        for m in chat_history[since_index:]
        if m.get("agent_name") != agent_name
    )


def main_loop(config: dict[str, Any]) -> None:
    agent_name: str = config["agent_name"]
    agent_role: str = config["agent_role"]
    agent_role_mode: str = str(config.get("agent_role_mode", ""))
    writer_mode = is_writer_role(agent_role_mode, agent_role)
    hub_url: str = _resolve_env_value(config["hub_url"], name="hub_url")
    hub_password: str = _resolve_env_value(config["hub_password"], name="hub_password")
    model: str = config["model"]
    poll_interval: float = float(config["poll_interval_sec"])
    jitter: float = float(config["jitter_sec"])
    max_messages_raw = config.get("max_messages_to_send")
    max_messages: int | None = int(max_messages_raw) if max_messages_raw is not None else None
    max_tool_rounds: int = int(config["max_tool_rounds"])
    auto_yes: bool = bool(config["auto_yes"])

    hub_per_agent_cap: int = int(config.get("hub_per_agent_cap", 10))
    conservative_threshold: int = int(config.get("conservative_threshold", 30))
    conservative_rate: float = float(config.get("conservative_rate_limit_sec", 10.0))
    cap_retry_sec: float = float(config.get("cap_retry_sec", 300.0))
    cooldown_other_msgs: int = int(config.get("cooldown_other_msgs", 2))
    cooldown_sec: float = float(config.get("cooldown_seconds", 30.0))

    name_aliases: list[str] = list(config.get("name_aliases", []))
    broadcast_phrases: list[str] = list(config.get("broadcast_phrases", []))
    competence_keywords: list[str] = list(config.get("competence_keywords", []))
    name_patterns = _compile_alias_patterns(name_aliases)

    template_path = _ASSIGNMENT_ROOT / config["system_prompt_template"]
    mode_runtime = ModeRuntime.from_config(
        config,
        assignment_root=_ASSIGNMENT_ROOT,
        template_path=template_path,
        agent_name=agent_name,
        agent_role=agent_role,
    )
    system_prompt = mode_runtime.system_prompt

    hub = HubClient(hub_url=hub_url, password=hub_password, agent_name=agent_name)
    client = LLMClient(model=model)
    budget = BudgetController(
        token_budget=(
            int(config["default_token_budget"])
            if config.get("default_token_budget") is not None
            else None
        ),
        rate_limit_sec=float(config["default_rate_limit_sec"]),
    )
    budget.attach_mode_runtime(mode_runtime)
    budget.start_console_thread(client)
    safety.set_confirm_provider(budget.confirm_interactive)

    print_banner(agent_name, hub_url, config)

    last_seen = 0
    already_introduced = False
    initial = hub.get_messages(since=0)
    if isinstance(initial, list):
        if initial:
            last_seen = max((m.get("seq", 0) for m in initial), default=0)
            print(
                f"[hub] hoppade över {len(initial)} äldre meddelanden (startar från seq={last_seen})."
            )
        already_introduced = any(m.get("agent_name") == agent_name for m in initial)
    else:
        print(f"[hub] kunde inte hämta initial state: {initial.get('message')}")
        # Säker default: anta att vi redan introducerat oss så vi inte
        # råkar dubbel-greeta vid en transient nätverksfel.
        already_introduced = True

    messages_sent = 0
    chat_history: list[dict[str, Any]] = []
    responded_seqs: set[int] = set()
    last_self_post_at: float = 0.0
    last_self_history_index: int = 0
    recent_post_fingerprints: list[str] = []
    read_only_until: float = 0.0
    conservative_active: bool = False

    greeting_posted = post_join_greeting(
        hub,
        budget,
        template=config.get("join_greeting", ""),
        agent_name=agent_name,
        agent_role=agent_role,
        already_introduced=already_introduced,
    )
    if greeting_posted:
        messages_sent += 1
        last_self_post_at = time.time()
        last_self_history_index = len(chat_history)
    # Passive mode: token-budget överskriden — vi pollar och uppdaterar
    # kontext men gör inga LLM-anrop. Användaren kan höja budgeten med
    # `set budget N` så återgår vi automatiskt till aktivt läge.
    passive_mode: bool = False

    while True:
        if budget.stopped:
            print("[loop] stopped av konsolen — avslutar.")
            break
        if max_messages is not None and messages_sent >= max_messages:
            print(f"[loop] nådde max_messages_to_send={max_messages} — avslutar.")
            break

        if not conservative_active and messages_sent >= conservative_threshold:
            conservative_active = True
            new_rate = max(conservative_rate, budget.rate_limit_sec)
            print(
                f"[conservative] {messages_sent}/{hub_per_agent_cap} msgs — "
                f"höjer rate-limit till {new_rate:.1f}s och skärper PASS-tröskeln."
            )
            with budget._lock:  # noqa: SLF001 — vi äger denna controller
                budget.rate_limit_sec = new_rate

        # Token-budget kollas separat från pause/stop — vi vill INTE
        # avsluta processen, bara temporärt sluta göra LLM-anrop.
        # När token_budget är None returnerar is_token_budget_exhausted
        # alltid False och denna blir effektivt en no-op.
        if budget.is_token_budget_exhausted():
            if not passive_mode:
                passive_mode = True
                cap = budget.token_budget if budget.token_budget is not None else "∞"
                print(
                    f"[passive] token-budget nådd ({budget.tokens_used()}/{cap}) "
                    "— slutar göra LLM-anrop. "
                    "Använd `set budget <N>` i konsolen för att aktivera igen, "
                    "eller `set budget off` för att slå av cappet helt."
                )
        elif passive_mode:
            passive_mode = False
            cap = budget.token_budget if budget.token_budget is not None else "∞"
            print(f"[passive] token-budget nu {cap} — återgår till aktivt läge.")

        if not budget.can_call_llm():
            if budget.paused:
                print("[loop] pausad — sover 2s")
                time.sleep(2)
                continue
            # is_token_budget_exhausted hanteras ovan via passive_mode.
            # `can_call_llm` returnerar False även då, men vi vill fortsätta
            # polla för att hålla kontexten färsk till budgeten höjs.

        new_msgs = hub.get_messages(since=last_seen)
        if isinstance(new_msgs, dict):
            print(f"[hub] {new_msgs.get('message')}")
            time.sleep(max(2.0, poll_interval))
            continue

        if not new_msgs:
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        last_seen = max(last_seen, max((m.get("seq", 0) for m in new_msgs), default=last_seen))
        chat_history.extend(new_msgs)

        if mode_runtime.maybe_auto_enable_from_messages(
            new_msgs,
            agent_name=agent_name,
            budget=budget,
        ):
            system_prompt = mode_runtime.system_prompt

        in_read_only = time.time() < read_only_until
        if in_read_only:
            remaining = read_only_until - time.time()
            if any(m.get("agent_name") != agent_name for m in new_msgs):
                print(f"[read-only] kvar {remaining:.0f}s innan vi prövar posta igen.")
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        if passive_mode:
            # Token-budget överskriden — fortsätt polla för kontext men gör
            # inga LLM-anrop. Användaren kan höja budget via konsolen.
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        category, trigger_msg, reason = pick_trigger_message(
            new_msgs,
            agent_name=agent_name,
            name_patterns=name_patterns,
            broadcast_phrases=broadcast_phrases,
            competence_keywords=competence_keywords,
            responded_seqs=responded_seqs,
        )
        if trigger_msg is None or category == "skip":
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        is_forced = category in ("name", "broadcast", "competence")

        # Cool-down: om vi just postat och inte är tilltalade vid namn,
        # vänta tills minst N andra agenter pratat ELLER M sekunder gått.
        if not is_forced or category != "name":
            others_since_self = _count_other_msgs_since(
                chat_history,
                agent_name=agent_name,
                since_index=last_self_history_index,
            )
            seconds_since_self = time.time() - last_self_post_at
            in_cooldown = (
                last_self_post_at > 0
                and others_since_self < cooldown_other_msgs
                and seconds_since_self < cooldown_sec
            )
            if in_cooldown and category != "name":
                latest_author = trigger_msg.get("agent_name", "?")
                print(
                    f"[cooldown] hoppar över [{latest_author}] — bara {others_since_self} "
                    f"andra msgs sen min post ({seconds_since_self:.0f}s)."
                )
                time.sleep(poll_interval + random.uniform(0, jitter))
                continue

        chat_context = format_context(chat_history, window=CONTEXT_WINDOW_MSGS)

        trigger_content = (trigger_msg.get("content") or "").strip()

        if (
            not is_forced
            and config.get("collaboration_mode", True)
            and is_smalltalk_message(trigger_content)
        ):
            print(
                f"[pass] småprat från [{trigger_msg.get('agent_name', '?')}] — hoppar."
            )
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        collab_hint, collab_active = collaboration_context_for_turn(
            config,
            agent_name=agent_name,
            agent_role=agent_role,
            trigger_content=trigger_content,
            chat_context=chat_context,
            chat_history=chat_history,
        )
        if collab_active:
            print("[collab] samarbetsläge — smal scope, respektera CLAIM i tråden.")

        review_ev = prepare_role_evidence(
            chat_history,
            trigger_content=trigger_content,
            self_agent_name=agent_name,
            workspace_root=agent_workspace_path(config, mode_runtime),
            agent_role_mode=agent_role_mode,
            agent_role=agent_role,
        )
        if review_ev.hint:
            role_tag = "writer" if review_ev.writer_mode else "review"
            print(
                f"[{role_tag}] kodunderlag: thread_code={review_ev.has_thread_code} "
                f"force_tools={review_ev.force_tools}"
            )
        if category == "name" and trigger_content:
            if mode_runtime.offer_modes_for_direct_request(
                trigger_content,
                agent_name=agent_name,
                budget=budget,
                was_name_addressed=True,
            ):
                system_prompt = mode_runtime.system_prompt

        if is_forced:
            print(
                f"[force] kategori={category} ({reason}) från "
                f"[{trigger_msg.get('agent_name', '?')}] — genererar svar direkt."
            )
            draft = generate_forced_response(
                client,
                system_prompt=system_prompt,
                chat_context=chat_context,
                reason=reason,
                mode_runtime=mode_runtime,
                trigger_content=trigger_content,
                collaboration_hint=collab_hint,
                collaboration_active=collab_active,
                review_evidence_hint=review_ev.hint,
                collab_suffix=collab_force_suffix(config),
            )
            if not draft:
                print("[force] tomt svar från modellen — hoppar över.")
                time.sleep(poll_interval + random.uniform(0, jitter))
                continue
        else:
            should_respond, draft = decide_pass_or_respond(
                client,
                system_prompt=system_prompt,
                chat_context=chat_context,
                conservative=conservative_active,
                collaboration_hint=collab_hint + review_ev.hint,
            )
            if not should_respond:
                latest_author = trigger_msg.get("agent_name", "?")
                print(f"[pass] PASS på senaste meddelandet från [{latest_author}].")
                time.sleep(poll_interval + random.uniform(0, jitter))
                continue

        loop_result: LoopResult | None = None
        started_tools = False
        peer_tasks = parse_peer_tasks(chat_history, self_agent_name=agent_name)
        if needs_tools_for_turn(
            draft,
            trigger_content=trigger_content,
            force_tools=review_ev.force_tools,
        ):
            started_tools = True
            if config.get("hub_coordination", True):
                task = (
                    suggested_writer_scope(peer_tasks, trigger_content)
                    if collab_active
                    else extract_task_label(trigger_content, draft)
                )
                draft = ensure_claim_prefix(draft, task)
            print("[tool] utkast hintar om verifiering — kör structured_loop.")
            tool_extra = collab_hint + review_ev.hint
            if collab_active:
                tool_extra += collab_force_suffix(config)
            loop_result = run_tool_loop_for_response(
                client,
                system_prompt=system_prompt,
                chat_context=chat_context,
                draft_response=draft,
                cfg_max_rounds=max_tool_rounds,
                auto_yes=auto_yes,
                github_mode=mode_runtime.github_mode,
                workspace_root=tool_workspace_root(config, mode_runtime),
                extra_system=tool_extra,
                collaboration_active=collab_active,
            )
            final_text = loop_result.final_text
        else:
            final_text = draft

        workspace_for_persist = agent_workspace_path(config, mode_runtime)

        persisted_from_chat: list[str] = []
        trim_chat = bool(config.get("hub_chat_trim_codeblocks", False))
        if config.get("persist_chat_code_to_workspace", True):
            final_text, persisted_from_chat = persist_hub_codeblocks(
                final_text or "",
                workspace_root=workspace_for_persist,
                trim_chat=trim_chat,
                max_lines_in_chat=int(config.get("hub_chat_max_codeblock_lines", 12)),
            )
            if persisted_from_chat:
                print(
                    f"[persist] sparade {len(persisted_from_chat)} kodblock i "
                    f"{workspace_for_persist}"
                )

        all_local_paths = list(
            dict.fromkeys([*extract_posted_files(final_text or ""), *persisted_from_chat])
        )
        attach_mode = str(config.get("hub_attach_mode", "compact")).lower()
        if config.get("collaboration_skip_duplicate_attach", True):
            if should_skip_duplicate_attach(final_text or ""):
                attach_mode = "none"
        if (
            config.get("auto_attach_code_on_deliver", True)
            and all_local_paths
            and attach_mode != "none"
        ):
            attachment = build_attachment_for_hub(
                all_local_paths,
                workspace_root=workspace_for_persist,
                mode=attach_mode,
                max_total_chars=int(config.get("auto_attach_max_total_chars", 800)),
                max_lines_per_file=int(config.get("auto_attach_max_lines_per_file", 15)),
            )
            if attachment:
                final_text = append_deliverables_to_hub_message(final_text, attachment)
                print(
                    f"[attach] {attach_mode} — {len(all_local_paths)} fil(er) "
                    f"({len(final_text)} tecken totalt)"
                )

        if config.get("hub_coordination", True):
            final_text = apply_coordination_format(
                final_text or "",
                trigger_content=trigger_content,
                draft=draft,
                had_deliverables=bool(all_local_paths),
                started_tools=started_tools,
            )

        allow_full = wants_full_code_in_hub(trigger_content)
        if collab_active:
            final_text = enforce_collaboration_on_post(
                final_text or "",
                active=True,
                max_codeblock_lines=int(
                    config.get("collaboration_max_hub_code_lines", 35)
                ),
                allow_full_code=allow_full,
            )

        final_text = strip_pass_leak(final_text or "")
        final_text = ensure_honest_review_prefix(final_text, evidence=review_ev)

        final_text, skip_post = sanitize_hub_post(
            final_text or "",
            writer_mode=writer_mode,
            had_deliverables=bool(all_local_paths),
            recent_fingerprints=recent_post_fingerprints[-8:],
        )
        if skip_post:
            print("[post] sanitize → tomt/PASS — hoppar över post.")
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        final_text = (final_text or "").strip()
        if not final_text:
            print("[loop] tomt svar från modellen — hoppar över post.")
            time.sleep(poll_interval + random.uniform(0, jitter))
            continue

        wait_s = budget.seconds_until_can_post()
        if wait_s > 0:
            print(f"[rate] väntar {wait_s:.1f}s p.g.a. rate-limit innan post.")
            time.sleep(wait_s)
            if budget.stopped or budget.paused:
                continue

        print(f"[post] -> hub ({len(final_text)} tecken)")
        result = hub.post_message(final_text)
        recent_post_fingerprints.append(fingerprint_post(final_text))
        recent_post_fingerprints[:] = recent_post_fingerprints[-12:]
        if result.get("error"):
            print(f"[post] FEL: {result.get('message')}")
            if result.get("status_code") == 429:
                read_only_until = time.time() + cap_retry_sec
                print(
                    f"[read-only] 429 från hubben — pausar posts i {cap_retry_sec:.0f}s "
                    "men fortsätter polla (cappet kan höjas av läraren)."
                )
            else:
                time.sleep(poll_interval)
            continue

        trigger_seq = trigger_msg.get("seq")
        if isinstance(trigger_seq, int):
            responded_seqs.add(trigger_seq)
        parts_sent = int(result.get("parts", 1) or 1)
        for _ in range(parts_sent):
            budget.note_post()
        messages_sent += parts_sent
        last_self_post_at = time.time()
        last_self_history_index = len(chat_history)
        seq = result.get("seq", "?")
        cap_str = f"{messages_sent}/{hub_per_agent_cap}"
        seq_display = result.get("seqs") if parts_sent > 1 else seq
        print(
            f"[post] OK seq={seq_display} ({cap_str}) | tokens={client.usage.total_tokens}"
        )

        time.sleep(poll_interval + random.uniform(0, jitter))

    print()
    print("=" * 64)
    print("Slutstats:")
    snap = budget.snapshot()
    snap["messages_sent_to_hub"] = messages_sent
    snap["hub_per_agent_cap"] = hub_per_agent_cap
    snap["conservative_active"] = conservative_active
    snap["token_usage_full"] = client.usage.snapshot()
    for k, v in snap.items():
        print(f"  {k}: {v}")
    print("=" * 64)


def main() -> None:
    # Säkerställ line-buffered stdout även när agenten körs i bakgrund / utan
    # TTY — annars block-buffrar Python och inget syns förrän processen dör.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except AttributeError:
        pass
    load_dotenv(_ASSIGNMENT_ROOT / ".env")
    config_path = _PART3_DIR / "config.yaml"
    config = load_config(config_path)
    main_loop(config)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[ctrl+c] avslutar.")
