"""Tester för hub_review."""

from __future__ import annotations

import tempfile
from pathlib import Path

from part3.hub_review import (
    extract_hub_code_blocks,
    prepare_role_evidence,
    prepare_review_evidence,
    requires_code_evidence,
    strip_pass_leak,
)


def test_requires_evidence() -> None:
    assert requires_code_evidence("var kom den koden ifrån?")


def test_extract_blocks() -> None:
    msgs = [
        {
            "agent_name": "hassan-swe-agent",
            "seq": 33,
            "content": "```python\ndef power():\n    return 1\n\nprint(2)\n```",
        }
    ]
    blocks = extract_hub_code_blocks(msgs, self_agent_name="rebecka-vannerberg")
    assert len(blocks) == 1
    assert blocks[0].author == "hassan-swe-agent"


def test_prepare_with_source() -> None:
    msgs = [
        {
            "agent_name": "hassan-swe-agent",
            "seq": 33,
            "content": "ui.py\n```python\nx=1\ny=2\nz=3\n```",
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        ev = prepare_review_evidence(
            msgs,
            trigger_content="var kom koden",
            self_agent_name="rebecka-vannerberg",
            workspace_root=Path(tmp),
        )
        assert ev.has_thread_code
        assert "seq=33" in ev.hint
        assert ev.saved_paths


def test_strip_pass() -> None:
    assert "PASS" not in strip_pass_leak("Hej\n\nPASS")


def test_writer_mode_hint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ev = prepare_role_evidence(
            [],
            trigger_content="implementera logik",
            self_agent_name="rebecka-vannerberg",
            workspace_root=Path(tmp),
            agent_role_mode="writer",
            agent_role="kodskrivare",
        )
        assert ev.writer_mode
        assert "KODSKRIVARE" in ev.hint


def run_all() -> None:
    test_requires_evidence()
    test_extract_blocks()
    test_prepare_with_source()
    test_strip_pass()
    test_writer_mode_hint()
    print("hub_review: 5/5 OK")


if __name__ == "__main__":
    run_all()
