"""
test_email_retriever.py
-----------------------
Unit tests for :mod:`backend.email_retriever`.

Because this module uses MAPI/COM (Windows-only), all Outlook interactions
are mocked.  The tests exercise:

* ``_matches_criteria`` — the metadata filter predicate
* ``EmailCandidate.as_dict`` — serialisation
* ``retrieve_emails`` — raises ``OutlookUnavailableError`` on non-Windows
"""

from __future__ import annotations

import platform
from datetime import date

import pytest

from backend.criteria_extractor import SearchCriteria
from backend.email_retriever import (
    EmailCandidate,
    OutlookUnavailableError,
    _matches_criteria,
    retrieve_emails,
)
from tests.conftest import make_email


# ---------------------------------------------------------------------------
# _matches_criteria
# ---------------------------------------------------------------------------


class TestMatchesCriteria:
    def test_empty_criteria_matches_all(self) -> None:
        email = make_email()
        assert _matches_criteria(email, SearchCriteria()) is True

    def test_sender_filter_matches(self) -> None:
        email = make_email(sender="alice@example.com")
        criteria = SearchCriteria(sender="alice")
        assert _matches_criteria(email, criteria) is True

    def test_sender_filter_case_insensitive(self) -> None:
        email = make_email(sender="Alice@Example.com")
        criteria = SearchCriteria(sender="alice")
        assert _matches_criteria(email, criteria) is True

    def test_sender_filter_no_match(self) -> None:
        email = make_email(sender="bob@example.com")
        criteria = SearchCriteria(sender="alice")
        assert _matches_criteria(email, criteria) is False

    def test_recipient_filter_matches(self) -> None:
        email = make_email(recipients=["me@company.com", "team@company.com"])
        criteria = SearchCriteria(recipient="me@company.com")
        assert _matches_criteria(email, criteria) is True

    def test_recipient_filter_no_match(self) -> None:
        email = make_email(recipients=["other@company.com"])
        criteria = SearchCriteria(recipient="me@company.com")
        assert _matches_criteria(email, criteria) is False

    def test_date_start_filter(self) -> None:
        email = make_email(date_=date(2026, 3, 15))
        assert _matches_criteria(email, SearchCriteria(date_start=date(2026, 3, 1))) is True
        assert _matches_criteria(email, SearchCriteria(date_start=date(2026, 3, 16))) is False

    def test_date_end_filter(self) -> None:
        email = make_email(date_=date(2026, 3, 15))
        assert _matches_criteria(email, SearchCriteria(date_end=date(2026, 3, 31))) is True
        assert _matches_criteria(email, SearchCriteria(date_end=date(2026, 3, 14))) is False

    def test_date_range_filter(self) -> None:
        email = make_email(date_=date(2026, 3, 15))
        in_range = SearchCriteria(date_start=date(2026, 3, 1), date_end=date(2026, 3, 31))
        out_range = SearchCriteria(date_start=date(2026, 1, 1), date_end=date(2026, 2, 28))
        assert _matches_criteria(email, in_range) is True
        assert _matches_criteria(email, out_range) is False

    def test_read_state_read(self) -> None:
        read_email = make_email(is_read=True)
        unread_email = make_email(is_read=False)
        criteria = SearchCriteria(read_state="read")
        assert _matches_criteria(read_email, criteria) is True
        assert _matches_criteria(unread_email, criteria) is False

    def test_read_state_unread(self) -> None:
        read_email = make_email(is_read=True)
        unread_email = make_email(is_read=False)
        criteria = SearchCriteria(read_state="unread")
        assert _matches_criteria(read_email, criteria) is False
        assert _matches_criteria(unread_email, criteria) is True

    def test_has_attachment_true(self) -> None:
        with_attach = make_email(has_attachment=True)
        without_attach = make_email(has_attachment=False)
        criteria = SearchCriteria(has_attachment=True)
        assert _matches_criteria(with_attach, criteria) is True
        assert _matches_criteria(without_attach, criteria) is False

    def test_has_attachment_false(self) -> None:
        with_attach = make_email(has_attachment=True)
        without_attach = make_email(has_attachment=False)
        criteria = SearchCriteria(has_attachment=False)
        assert _matches_criteria(with_attach, criteria) is False
        assert _matches_criteria(without_attach, criteria) is True

    def test_importance_filter(self) -> None:
        high_email = make_email(importance="high")
        normal_email = make_email(importance="normal")
        criteria = SearchCriteria(importance="high")
        assert _matches_criteria(high_email, criteria) is True
        assert _matches_criteria(normal_email, criteria) is False

    def test_subject_keywords_all_must_match(self) -> None:
        email = make_email(subject="Q1 Budget Review 2026")
        criteria_match = SearchCriteria(subject_keywords=["Q1", "budget"])
        criteria_no_match = SearchCriteria(subject_keywords=["Q1", "missing"])
        assert _matches_criteria(email, criteria_match) is True
        assert _matches_criteria(email, criteria_no_match) is False

    def test_subject_keywords_case_insensitive(self) -> None:
        email = make_email(subject="Q1 Budget Review")
        criteria = SearchCriteria(subject_keywords=["BUDGET"])
        assert _matches_criteria(email, criteria) is True

    def test_body_keywords(self) -> None:
        email = make_email(snippet="Please review the spreadsheet before the meeting.")
        criteria_match = SearchCriteria(body_keywords=["spreadsheet"])
        criteria_no_match = SearchCriteria(body_keywords=["invoice"])
        assert _matches_criteria(email, criteria_match) is True
        assert _matches_criteria(email, criteria_no_match) is False

    def test_categories_filter(self) -> None:
        email = make_email(categories=["Finance", "Q1"])
        criteria_match = SearchCriteria(categories=["finance"])
        criteria_no_match = SearchCriteria(categories=["HR"])
        assert _matches_criteria(email, criteria_match) is True
        assert _matches_criteria(email, criteria_no_match) is False

    def test_multiple_criteria_combined(self) -> None:
        email = make_email(
            sender="alice@example.com",
            date_=date(2026, 3, 15),
            has_attachment=True,
            importance="high",
            subject="Q1 Budget",
        )
        criteria = SearchCriteria(
            sender="alice",
            date_start=date(2026, 3, 1),
            date_end=date(2026, 3, 31),
            has_attachment=True,
            importance="high",
            subject_keywords=["Q1"],
        )
        assert _matches_criteria(email, criteria) is True

    def test_single_failing_criterion_fails_overall(self) -> None:
        email = make_email(
            sender="alice@example.com",
            date_=date(2026, 3, 15),
            has_attachment=False,  # ← this will fail
        )
        criteria = SearchCriteria(
            sender="alice",
            has_attachment=True,  # ← requires attachment
        )
        assert _matches_criteria(email, criteria) is False


# ---------------------------------------------------------------------------
# EmailCandidate.as_dict
# ---------------------------------------------------------------------------


class TestEmailCandidateAsDict:
    def test_as_dict_fields(self) -> None:
        email = make_email(
            subject="Test Subject",
            sender="alice@example.com",
            date_=date(2026, 3, 15),
            snippet="A short snippet.",
            folder="Inbox",
            score=0.95,
        )
        d = email.as_dict()
        assert d["subject"] == "Test Subject"
        assert d["sender"] == "alice@example.com"
        assert d["date"] == "2026-03-15"
        assert d["snippet"] == "A short snippet."
        assert d["folder"] == "Inbox"
        assert d["score"] == pytest.approx(0.95)

    def test_as_dict_does_not_include_private_fields(self) -> None:
        email = make_email()
        d = email.as_dict()
        assert "is_read" not in d
        assert "has_attachment" not in d
        assert "recipients" not in d
        assert "categories" not in d
        assert "importance" not in d


# ---------------------------------------------------------------------------
# retrieve_emails — platform guard
# ---------------------------------------------------------------------------


class TestRetrieveEmailsPlatformGuard:
    def test_raises_on_non_windows(self) -> None:
        if platform.system() == "Windows":
            pytest.skip("This test only runs on non-Windows platforms.")

        with pytest.raises(OutlookUnavailableError, match="Windows"):
            retrieve_emails(SearchCriteria())
