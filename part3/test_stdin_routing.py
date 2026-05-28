"""Testar stdin-routing till y/n vs konsol-kommandon."""

from __future__ import annotations

import io
import sys
import threading
import time

from part3.budget import BudgetController


def test_route_y_to_pending_confirm() -> None:
    budget = BudgetController()
    event = threading.Event()
    holder: list[str] = []
    budget._pending_confirm = (event, holder)
    budget._route_stdin_line("y\n")
    assert holder == ["y\n"]
    assert event.is_set()


def test_route_unknown_when_no_pending() -> None:
    budget = BudgetController()
    budget._route_stdin_line("stats\n")
    assert budget._pending_confirm is None


def test_confirm_interactive_gets_answer_via_console_route() -> None:
    budget = BudgetController()
    stdout_backup = sys.stdout
    sys.stdout = io.StringIO()
    result: list[bool] = []

    def ask() -> None:
        result.append(budget.confirm_interactive("[y/N]: "))

    t = threading.Thread(target=ask, daemon=True)
    t.start()
    time.sleep(0.05)
    budget._route_stdin_line("ja\n")
    t.join(timeout=2.0)
    sys.stdout = stdout_backup
    assert result == [True], result


def run_all() -> None:
    test_route_y_to_pending_confirm()
    test_route_unknown_when_no_pending()
    test_confirm_interactive_gets_answer_via_console_route()
    print("stdin_routing: 3/3 OK")


if __name__ == "__main__":
    run_all()
