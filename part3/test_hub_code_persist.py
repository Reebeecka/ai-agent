"""Tester för hub_code_persist."""

from __future__ import annotations

import tempfile
from pathlib import Path

from part3.hub_code_persist import persist_and_trim_hub_code, persist_hub_codeblocks


def test_persist_keeps_full_chat_by_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        big = "```python\n" + "\n".join(f"x = {i}" for i in range(20)) + "\n```"
        original = f"RESULT:\n{big}"
        new_text, paths = persist_hub_codeblocks(
            original,
            workspace_root=ws,
            trim_chat=False,
        )
        assert new_text == original
        assert paths
        assert (ws / paths[0]).is_file()


def test_persist_named_file_from_result_hint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        msg = 'RESULT: skapade main.py\n```python\nprint("hi")\nprint("ok")\n```'
        new_text, paths = persist_hub_codeblocks(msg, workspace_root=ws, trim_chat=False)
        assert new_text == msg
        assert "main.py" in paths
        assert (ws / "main.py").read_text(encoding="utf-8").startswith('print("hi")')


def test_trim_mode_still_works() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        big = "```python\n" + "\n".join(f"x = {i}" for i in range(40)) + "\n```"
        new_text, paths = persist_and_trim_hub_code(
            big,
            workspace_root=ws,
            max_lines_in_chat=5,
        )
        assert paths
        assert "sparade lokalt" in new_text
        assert "x = 39" not in new_text


def run_all() -> None:
    test_persist_keeps_full_chat_by_default()
    test_persist_named_file_from_result_hint()
    test_trim_mode_still_works()
    print("hub_code_persist: 3/3 OK")


if __name__ == "__main__":
    run_all()
