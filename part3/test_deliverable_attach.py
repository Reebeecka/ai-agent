"""Enhetstester för deliverable_attach."""

from __future__ import annotations

import tempfile
from pathlib import Path

from part3.deliverable_attach import (
    append_deliverables_to_hub_message,
    build_code_attachment,
    extract_deliverable_paths,
)


def test_extract_from_tool_calls() -> None:
    msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "file_create",
                        "arguments": '{"path": "foo.py", "content": "x=1"}',
                    },
                }
            ],
        },
        {"role": "tool", "content": "ok=True: skapade bar.py"},
    ]
    assert extract_deliverable_paths(msgs) == ["foo.py", "bar.py"]


def test_extract_ignores_failed_tools() -> None:
    msgs = [{"role": "tool", "content": "ok=False: fil hittades ej"}]
    assert extract_deliverable_paths(msgs) == []


def test_build_attachment_reads_workspace_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "hello.py").write_text("print('hi')\n", encoding="utf-8")
        out = build_code_attachment(
            ["hello.py"],
            workspace_root=ws,
            max_total_chars=2000,
            max_lines_per_file=50,
        )
        assert "DELIVERABLE" in out
        assert "print('hi')" in out
        assert "```python" in out


def test_append_does_not_truncate() -> None:
    long_body = "A" * 3000
    attachment = "B" * 2000
    combined = append_deliverables_to_hub_message(long_body, attachment)
    assert len(combined) > 4096
    assert "trunkerad" not in combined
    assert long_body in combined
    assert attachment in combined


def run_all() -> None:
    test_extract_from_tool_calls()
    test_extract_ignores_failed_tools()
    test_build_attachment_reads_workspace_file()
    test_append_does_not_truncate()
    print("deliverable_attach: 4/4 OK")


if __name__ == "__main__":
    run_all()
