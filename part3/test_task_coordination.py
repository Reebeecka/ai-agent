"""Tester för hub-uppgiftskonvention."""

from __future__ import annotations

from part3.task_coordination import (
    apply_coordination_format,
    ensure_confirm_prefix,
    ensure_done_prefix,
    is_assignment_message,
)


def test_assignment_detected() -> None:
    assert is_assignment_message("@rebecka kan du ta dig an README och main.py")


def test_confirm_prefix_on_assignment() -> None:
    out = apply_coordination_format(
        "Här är koden.",
        trigger_content="@rebecka ta dig på kod-review av domain.py",
        had_deliverables=False,
    )
    assert out.startswith("Bekräftat, jag tar")


def test_done_prefix_after_deliverable() -> None:
    out = apply_coordination_format(
        "RESULT: main.py klar.",
        trigger_content="bygg main.py",
        had_deliverables=True,
        started_tools=True,
    )
    assert "Klar med:" in out


def test_confirm_idempotent() -> None:
    text = "Bekräftat, jag tar X\n\nok"
    assert ensure_confirm_prefix(text, "Y") == text


def run_all() -> None:
    test_assignment_detected()
    test_confirm_prefix_on_assignment()
    test_done_prefix_after_deliverable()
    test_confirm_idempotent()
    print("task_coordination: 4/4 OK")


if __name__ == "__main__":
    run_all()
