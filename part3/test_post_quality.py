"""Tester för post_quality.py."""

from __future__ import annotations

from part3.post_quality import (
    fingerprint_post,
    is_duplicate_of_recent,
    normalize_coordination_prefixes,
    sanitize_hub_post,
)


def test_normalize_double_prefix() -> None:
    text = "Bekräftat, jag tar Jag tar mig an: UI\n\nkod"
    out = normalize_coordination_prefixes(text)
    assert out.count("Jag tar mig an:") == 1


def test_empty_klar_med() -> None:
    text, skip = sanitize_hub_post(
        "Klar med: domain.py",
        writer_mode=True,
        had_deliverables=False,
        recent_fingerprints=[],
    )
    assert not skip
    assert "STATUS:" in text or "tar mig an" in text.lower()


def test_duplicate_detected() -> None:
    msg = "RESULT: domain.py\n```python\nx=1\n```"
    fp = fingerprint_post(msg)
    assert is_duplicate_of_recent(msg, [fp])


def run_all() -> None:
    test_normalize_double_prefix()
    test_empty_klar_med()
    test_duplicate_detected()
    print("post_quality: 3/3 OK")


if __name__ == "__main__":
    run_all()
