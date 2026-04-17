"""
test_criteria_extractor.py
---------------------------
Unit tests for :mod:`backend.criteria_extractor`.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.criteria_extractor import (
    SearchCriteria,
    last_month_range,
    last_n_days_range,
    parse_criteria,
    resolve_relative_dates,
)


# ---------------------------------------------------------------------------
# parse_criteria
# ---------------------------------------------------------------------------


class TestParseCriteria:
    def test_full_criteria(self) -> None:
        raw = {
            "sender": "alice@example.com",
            "recipient": "bob@example.com",
            "subject_keywords": ["Q1", "budget"],
            "body_keywords": ["spreadsheet"],
            "date_start": "2026-03-01",
            "date_end": "2026-03-31",
            "folder": "Inbox",
            "read_state": "unread",
            "has_attachment": True,
            "importance": "high",
            "categories": ["Finance"],
        }
        criteria = parse_criteria(raw)

        assert criteria.sender == "alice@example.com"
        assert criteria.recipient == "bob@example.com"
        assert criteria.subject_keywords == ["Q1", "budget"]
        assert criteria.body_keywords == ["spreadsheet"]
        assert criteria.date_start == date(2026, 3, 1)
        assert criteria.date_end == date(2026, 3, 31)
        assert criteria.folder == "Inbox"
        assert criteria.read_state == "unread"
        assert criteria.has_attachment is True
        assert criteria.importance == "high"
        assert criteria.categories == ["Finance"]

    def test_empty_criteria(self) -> None:
        criteria = parse_criteria({})
        assert criteria.sender is None
        assert criteria.recipient is None
        assert criteria.subject_keywords == []
        assert criteria.body_keywords == []
        assert criteria.date_start is None
        assert criteria.date_end is None
        assert criteria.folder is None
        assert criteria.read_state is None
        assert criteria.has_attachment is None
        assert criteria.importance is None
        assert criteria.categories == []

    def test_null_values(self) -> None:
        raw = {
            "sender": None,
            "subject_keywords": None,
            "date_start": None,
            "has_attachment": None,
        }
        criteria = parse_criteria(raw)
        assert criteria.sender is None
        assert criteria.subject_keywords == []
        assert criteria.date_start is None
        assert criteria.has_attachment is None

    def test_invalid_date_ignored(self) -> None:
        raw = {"date_start": "not-a-date", "date_end": "2026-13-45"}
        criteria = parse_criteria(raw)
        assert criteria.date_start is None
        assert criteria.date_end is None

    def test_invalid_read_state_ignored(self) -> None:
        raw = {"read_state": "maybe"}
        criteria = parse_criteria(raw)
        assert criteria.read_state is None

    def test_invalid_importance_ignored(self) -> None:
        raw = {"importance": "critical"}
        criteria = parse_criteria(raw)
        assert criteria.importance is None

    def test_read_state_case_insensitive(self) -> None:
        assert parse_criteria({"read_state": "READ"}).read_state == "read"
        assert parse_criteria({"read_state": "Unread"}).read_state == "unread"

    def test_importance_case_insensitive(self) -> None:
        assert parse_criteria({"importance": "HIGH"}).importance == "high"
        assert parse_criteria({"importance": "Normal"}).importance == "normal"
        assert parse_criteria({"importance": "LOW"}).importance == "low"

    def test_has_attachment_int_coercion(self) -> None:
        assert parse_criteria({"has_attachment": 1}).has_attachment is True
        assert parse_criteria({"has_attachment": 0}).has_attachment is False

    def test_has_attachment_string_coercion(self) -> None:
        assert parse_criteria({"has_attachment": "true"}).has_attachment is True
        assert parse_criteria({"has_attachment": "false"}).has_attachment is False
        assert parse_criteria({"has_attachment": "yes"}).has_attachment is True

    def test_sender_stripped(self) -> None:
        criteria = parse_criteria({"sender": "  Alice  "})
        assert criteria.sender == "Alice"

    def test_blank_sender_becomes_none(self) -> None:
        criteria = parse_criteria({"sender": "   "})
        assert criteria.sender is None

    def test_subject_keywords_filters_blanks(self) -> None:
        raw = {"subject_keywords": ["Q1", "", "  ", "budget"]}
        criteria = parse_criteria(raw)
        assert criteria.subject_keywords == ["Q1", "budget"]

    def test_categories_as_string_becomes_list(self) -> None:
        criteria = parse_criteria({"categories": "Finance"})
        assert criteria.categories == ["Finance"]

    def test_unknown_fields_ignored(self) -> None:
        raw = {"unknown_field": "value", "sender": "alice@example.com"}
        criteria = parse_criteria(raw)
        assert criteria.sender == "alice@example.com"
        assert not hasattr(criteria, "unknown_field")


# ---------------------------------------------------------------------------
# last_month_range / last_n_days_range
# ---------------------------------------------------------------------------


class TestDateHelpers:
    def test_last_month_range_returns_correct_boundaries(self) -> None:
        start, end = last_month_range()
        assert start.day == 1
        assert end >= start
        # End should be last day of previous month (i.e., start - 1 day would be this month)
        next_day = end.replace(day=end.day)
        import calendar
        _, last_day = calendar.monthrange(end.year, end.month)
        assert end.day == last_day

    def test_last_n_days_range(self) -> None:
        start, end = last_n_days_range(7)
        from datetime import timedelta
        assert end == date.today()
        assert start == date.today() - timedelta(days=6)

    def test_last_n_days_range_single_day(self) -> None:
        start, end = last_n_days_range(1)
        assert start == end == date.today()


# ---------------------------------------------------------------------------
# resolve_relative_dates
# ---------------------------------------------------------------------------


class TestResolveRelativeDates:
    def test_last_month_resolved(self) -> None:
        criteria = SearchCriteria()
        raw = {"_query_hint": "emails from last month"}
        result = resolve_relative_dates(criteria, raw)
        expected_start, expected_end = last_month_range()
        assert result.date_start == expected_start
        assert result.date_end == expected_end

    def test_last_n_days_resolved(self) -> None:
        criteria = SearchCriteria()
        raw = {"_query_hint": "emails from last 14 days"}
        result = resolve_relative_dates(criteria, raw)
        expected_start, expected_end = last_n_days_range(14)
        assert result.date_start == expected_start
        assert result.date_end == expected_end

    def test_explicit_dates_not_overwritten(self) -> None:
        fixed_start = date(2026, 1, 1)
        fixed_end = date(2026, 1, 31)
        criteria = SearchCriteria(date_start=fixed_start, date_end=fixed_end)
        raw = {"_query_hint": "emails from last month"}
        result = resolve_relative_dates(criteria, raw)
        assert result.date_start == fixed_start
        assert result.date_end == fixed_end

    def test_no_relative_hint_leaves_dates_none(self) -> None:
        criteria = SearchCriteria()
        raw = {"_query_hint": "emails about budget"}
        result = resolve_relative_dates(criteria, raw)
        assert result.date_start is None
        assert result.date_end is None
