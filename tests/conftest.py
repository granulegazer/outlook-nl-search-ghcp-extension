"""
conftest.py — shared pytest fixtures for the Outlook NL-search backend tests.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from unittest.mock import MagicMock

import pytest

from backend.email_retriever import EmailCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_email(
    subject: str = "Test Email",
    sender: str = "alice@example.com",
    date_: date | None = None,
    snippet: str = "This is a test email snippet.",
    folder: str = "Inbox",
    is_read: bool = True,
    has_attachment: bool = False,
    importance: str = "normal",
    categories: list[str] | None = None,
    recipients: list[str] | None = None,
    score: float = 0.0,
) -> EmailCandidate:
    """Factory for :class:`EmailCandidate` instances in tests."""
    return EmailCandidate(
        subject=subject,
        sender=sender,
        date=date_ or date(2026, 3, 15),
        snippet=snippet,
        folder=folder,
        is_read=is_read,
        has_attachment=has_attachment,
        importance=importance,
        categories=categories or [],
        recipients=recipients or [],
        score=score,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_emails() -> list[EmailCandidate]:
    """A list of diverse EmailCandidate objects for use in tests."""
    return [
        make_email(
            subject="Q1 Budget Review",
            sender="alice@example.com",
            date_=date(2026, 3, 14),
            snippet="Please review the attached Q1 budget spreadsheet before the meeting.",
            has_attachment=True,
            importance="high",
        ),
        make_email(
            subject="Team Lunch Invitation",
            sender="bob@example.com",
            date_=date(2026, 3, 10),
            snippet="You are invited to the team lunch on Friday at noon.",
        ),
        make_email(
            subject="Project Alpha Update",
            sender="carol@example.com",
            date_=date(2026, 2, 28),
            snippet="Here is the latest update on Project Alpha milestones.",
            is_read=False,
            folder="Sent Items",
        ),
        make_email(
            subject="Invoice #1234",
            sender="finance@vendor.com",
            date_=date(2026, 1, 15),
            snippet="Please find attached invoice #1234 for services rendered in December.",
            has_attachment=True,
            categories=["Finance"],
            recipients=["me@company.com"],
        ),
        make_email(
            subject="Annual Performance Review",
            sender="hr@company.com",
            date_=date(2026, 3, 20),
            snippet="Your annual performance review is scheduled for next week.",
            importance="high",
        ),
    ]


@pytest.fixture(autouse=False)
def mock_win32com(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake win32com.client module so tests can run on non-Windows."""
    win32com = types.ModuleType("win32com")
    win32com_client = types.ModuleType("win32com.client")
    win32com.client = win32com_client  # type: ignore[attr-defined]
    sys.modules.setdefault("win32com", win32com)
    sys.modules.setdefault("win32com.client", win32com_client)
