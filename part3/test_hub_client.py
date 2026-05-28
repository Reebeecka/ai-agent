"""Tester för hub_client-meddelandesplit."""

from __future__ import annotations

from part3.hub_client import split_hub_message


def test_split_plain_text_under_cap() -> None:
    text = "\n".join(f"rad {i}" for i in range(500))
    parts = split_hub_message(text, max_chars=300)
    assert len(parts) > 1
    assert all(len(part) <= 300 for part in parts)
    assert "trunkerad" not in "\n".join(parts)


def test_split_code_keeps_functions_together_when_possible() -> None:
    func_a = "def alpha():\n" + "\n".join(f"    x{i} = {i}" for i in range(20)) + "\n"
    func_b = "def beta():\n" + "\n".join(f"    y{i} = {i}" for i in range(20)) + "\n"
    msg = f"RESULT: sample.py\n```python\n{func_a}\n{func_b}```"
    parts = split_hub_message(msg, max_chars=500)
    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    joined_parts = "\n---PART---\n".join(parts)
    alpha_part = next(part for part in parts if "def alpha" in part)
    beta_part = next(part for part in parts if "def beta" in part)
    assert "x19 = 19" in alpha_part
    assert "y19 = 19" in beta_part
    assert "trunkerad" not in joined_parts


def test_oversized_function_splits_on_whole_lines() -> None:
    long_func = "def huge():\n" + "\n".join(f"    value_{i} = {i}" for i in range(80)) + "\n"
    parts = split_hub_message(f"```python\n{long_func}```", max_chars=500)
    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert all("value_" not in line or line.strip().endswith(tuple(str(i) for i in range(80))) for part in parts for line in part.splitlines())


def run_all() -> None:
    test_split_plain_text_under_cap()
    test_split_code_keeps_functions_together_when_possible()
    test_oversized_function_splits_on_whole_lines()
    print("hub_client: 3/3 OK")


if __name__ == "__main__":
    run_all()
