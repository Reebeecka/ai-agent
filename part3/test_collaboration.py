"""Tester för collaboration.py."""

from __future__ import annotations

from part3.collaboration import (
    build_collaboration_system_hint,
    enforce_collaboration_on_post,
    is_collaboration_context,
    is_writer_role,
    parse_peer_claims,
    parse_peer_tasks,
    peer_owns_scopes,
    should_skip_duplicate_attach,
    suggested_writer_scope,
    wants_full_code_in_hub,
)


def test_collab_trigger() -> None:
    assert is_collaboration_context("arbeta tillsammans med olika roller")


def test_parse_claim() -> None:
    msgs = [
        {
            "agent_name": "emil_bot",
            "content": "CLAIM /workspace/shared/domain.py#initial-setup: Create module",
        }
    ]
    claims = parse_peer_claims(msgs, self_agent_name="rebecka-vannerberg")
    assert len(claims) == 1
    assert "domain" in claims[0]["resource"].lower()


def test_parse_jag_tar_mig_an() -> None:
    msgs = [
        {
            "agent_name": "hassan-swe-agent",
            "content": "Jag tar mig an: UI och terminalgränssnitt.",
        }
    ]
    tasks = parse_peer_tasks(msgs, self_agent_name="rebecka-vannerberg")
    assert len(tasks) == 1
    assert tasks[0].author == "hassan-swe-agent"
    assert "ui" in tasks[0].scopes


def test_writer_scope_respects_ui() -> None:
    tasks = parse_peer_tasks(
        [{"agent_name": "h", "content": "Jag tar mig an: UI."}],
        self_agent_name="rebecka-vannerberg",
    )
    scope = suggested_writer_scope(tasks, "app tillsammans")
    assert "inte UI" in scope or "core" in scope.lower()
    assert peer_owns_scopes(tasks, ("ui",))


def test_hint_writer_blocks_overlap() -> None:
    hint = build_collaboration_system_hint(
        trigger_content="app tillsammans",
        chat_context="",
        agent_name="rebecka-vannerberg",
        agent_role="SWE kodskrivare",
        agent_role_mode="writer",
        peer_tasks=parse_peer_tasks(
            [{"agent_name": "x", "content": "Jag tar mig an: UI."}],
            self_agent_name="rebecka-vannerberg",
        ),
    )
    assert "KODSKRIVARE" in hint
    assert "BLOCKERA" in hint or "inte" in hint.lower()


def test_hint_is_default_even_without_collab_words() -> None:
    hint = build_collaboration_system_hint(
        trigger_content="kan du implementera auth.py",
        chat_context="",
        agent_name="rebecka-vannerberg",
        agent_role="SWE kodskrivare",
        agent_role_mode="writer",
        peer_tasks=[],
    )
    assert "SAMARBETE" in hint
    assert "KODSKRIVARE" in hint


def test_full_file_trigger() -> None:
    assert wants_full_code_in_hub("dela hela filen tack")


def test_collaboration_does_not_trim_code() -> None:
    code = "```python\n" + "\n".join(f"x={i}" for i in range(50)) + "\n```"
    out = enforce_collaboration_on_post(code, active=True, max_codeblock_lines=10)
    assert "utelämnade" not in out
    assert "x=49" in out


def test_no_trim_when_full_requested() -> None:
    code = "```python\n" + "\n".join(f"x={i}" for i in range(50)) + "\n```"
    out = enforce_collaboration_on_post(
        code, active=True, max_codeblock_lines=10, allow_full_code=True
    )
    assert "x=49" in out


def test_writer_role_mode() -> None:
    assert is_writer_role("writer", "kod-review") is True
    assert is_writer_role("reviewer", "kodskrivare") is False


def test_skip_duplicate_attach() -> None:
    text = "```python\n" + "\n".join(f"x={i}" for i in range(20)) + "\n```"
    assert should_skip_duplicate_attach(text)


def run_all() -> None:
    test_collab_trigger()
    test_parse_claim()
    test_parse_jag_tar_mig_an()
    test_writer_scope_respects_ui()
    test_hint_writer_blocks_overlap()
    test_hint_is_default_even_without_collab_words()
    test_full_file_trigger()
    test_collaboration_does_not_trim_code()
    test_no_trim_when_full_requested()
    test_writer_role_mode()
    test_skip_duplicate_attach()
    print("collaboration: 11/11 OK")


if __name__ == "__main__":
    run_all()
